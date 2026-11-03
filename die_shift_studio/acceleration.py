"""Verified, optional FP64 compute. Frozen Windows builds use the NVIDIA driver directly."""
import atexit,importlib.util,logging,multiprocessing,sys
from functools import lru_cache
from threading import Lock,RLock
import numpy as np
from scipy.signal import fftconvolve

_probe_lock=Lock();_gpu_lock=RLock();_runtime=None

def _blend_kernel(xp,xy,w,center,state,method,arc_angle):
    """Float64 only; shared NumPy/CuPy formula for audited coordinate mapping."""
    angle=state[2];co=xp.cos(angle);si=xp.sin(angle);r=xy-center
    dest=xp.stack((r[:,0]*co-r[:,1]*si,r[:,0]*si+r[:,1]*co),axis=1)+center+state[:2]
    delta=dest-xy
    if method=='bezier':return xy+w[:,None]*delta
    gamma=arc_angle*xp.pi/180*(1 if angle>=0 else -1);phase=gamma*w;cot=1/xp.tan(gamma/2)
    a=.5*(1-xp.cos(phase)+cot*xp.sin(phase));b=.5*(cot*(1-xp.cos(phase))-xp.sin(phase))
    return xy+a[:,None]*delta+b[:,None]*xp.stack((-delta[:,1],delta[:,0]),axis=1)


def _rigid_kernel(xp,points,owners,centers,states):
    """Evaluate sin/cos once per DIE, including external fan-out vertices."""
    idx=xp.maximum(owners,0);c=centers[idx];delta=points-c
    co=xp.cos(states[:,2])[idx];si=xp.sin(states[:,2])[idx]
    xy=xp.stack((co*delta[:,0]-si*delta[:,1],si*delta[:,0]+co*delta[:,1]),axis=1)+c+states[idx,:2]
    xy=xp.where((states[idx,2]==0)[:,None],points+states[idx,:2],xy)
    return xp.where((owners<0)[:,None],points,xy)


def _pose_kernel(xp,vertices,center,states):
    v=vertices-center
    co=xp.cos(states[:,2,None]);si=xp.sin(states[:,2,None])
    out=xp.stack((co*v[:,0]-si*v[:,1],si*v[:,0]+co*v[:,1]),axis=-1)+center+states[:,None,:2]
    return xp.where((states[:,2]==0)[:,None,None],vertices+states[:,None,:2],out)


class _CuPy:
    """Compatibility for source installations with their own working CuPy/ROCm."""
    def __init__(self,cp,ordinal):
        self.cp=cp;self.ordinal=ordinal

    def calculate(self,budget,compute):
        cp=self.cp
        with _gpu_lock,cp.cuda.Device(self.ordinal):
            free,_=cp.cuda.runtime.memGetInfo()
            if budget>free*.45:raise MemoryError('该批次超过可用显存预算')
            return cp.asnumpy(compute(cp))

    def blend(self,points,w,center,state,method,angle):
        return self.calculate(len(points)*256,lambda cp:_blend_kernel(cp,*map(cp.asarray,(points,w,center,state)),method,angle))

    def rigid(self,p,owners,centers,states):
        return self.calculate(len(p)*160,lambda cp:_rigid_kernel(cp,*map(cp.asarray,(p,owners,centers,states))))

    def poses(self,points,center,states):
        return self.calculate(len(points)*len(states)*96,lambda cp:_pose_kernel(cp,*map(cp.asarray,(points,center,states))))

    def convolution(self,a,b,mode):
        from cupyx.scipy.signal import fftconvolve as gpu_fft
        return self.calculate(128*int(np.prod(np.add(a.shape,b.shape))),lambda cp:gpu_fft(cp.asarray(a),cp.asarray(b),mode=mode))

def _selftest(rt):
    xy=np.arange(64,dtype=float).reshape(32,2);c=np.array([20.,-5.]);z=np.array([-35.,-35.,-.017])
    owners=np.arange(len(xy),dtype=np.int32)%3-1;centers=np.stack([c,-c]);states=np.stack([z,-z]);w=np.linspace(0,1,len(xy))
    a=np.arange(35,dtype=float).reshape(5,7)/19.;b=np.array([[.1,.03],[.04,.05],[.02,.3]])
    tests={
        'rigid':[(lambda:rt.rigid(xy,owners,centers,states),_rigid_kernel(np,xy,owners,centers,states))],
        'poses':[(lambda:rt.poses(xy,c,states),_pose_kernel(np,xy,c,states))],
        'blend':[(lambda m=m:rt.blend(xy,w,c,z,m,30.),_blend_kernel(np,xy,w,c,z,m,30.)) for m in ('bezier','arc')],
        'convolution':[(lambda m=m:rt.convolution(a,b,m),fftconvolve(a,b,mode=m)) for m in ('full','same','valid')]}
    passed=[];errors={}
    for name,cases in tests.items():
        try:
            for run,expected in cases:np.testing.assert_allclose(run(),expected,rtol=1e-12,atol=1e-10)
            passed.append(name)
        except Exception as exc:errors[name]=str(exc)
    return passed,errors

def _select(devices,open_runtime,provider,info,notes):
    global _runtime
    best=None;best_ops=[];best_device=None;best_errors={}
    for dev in sorted(devices,key=lambda d:d.get('memory_mb',0),reverse=True):
        try:
            rt=open_runtime(dev)
        except Exception as exc:
            notes.append(f"{dev['name']} · {exc}");continue
        try:ops,errors=_selftest(rt)
        except BaseException:
            if hasattr(rt,'close'):rt.close()
            raise
        if len(ops)>len(best_ops):
            if best is not None and hasattr(best,'close'):best.close()
            best,best_ops,best_device,best_errors=rt,ops,dev,errors
        elif hasattr(rt,'close'):rt.close()
        if not ops:notes.append(f"{dev['name']} · "+str(errors))
        if len(best_ops)==4:break
    if best is None:return False
    _runtime=best
    if hasattr(best,'close'):atexit.register(best.close)
    labels={'rigid':'刚体坐标','poses':'批量姿态','blend':'型变坐标','convolution':'概率卷积'}
    reason=f"{provider} · {best_device['name']} · 通过自检："+'、'.join(labels[k] for k in best_ops)
    if best_errors:reason+='；转 CPU：'+'、'.join(labels[k]+' ('+v+')' for k,v in best_errors.items())
    info.update(backend='cuda',provider=provider,selected=best_device,operations=best_ops,operation_errors=best_errors,reason=reason)
    return True

@lru_cache(maxsize=1)
def _probe():
    info=dict(backend='cpu',devices=[],operations=[],reason='');notes=[]
    if multiprocessing.parent_process() is not None:
        info['reason']='工作子进程使用 CPU，主进程统一管理显存';return info
    from .cuda_driver import API,Runtime
    try:
        api=API();info['driver_version']=api.version
        devices=api.devices();info['devices']=devices
        if not devices:notes.append('NVIDIA 驱动未枚举到 CUDA 设备')
        if _select(devices,lambda d:Runtime(api,d),'NVIDIA Driver',info,notes):return info
    except Exception as exc:notes.append('NVIDIA 驱动 · '+str(exc))
    if not getattr(sys,'frozen',False) and importlib.util.find_spec('cupy') is not None:
        try:
            import cupy as cp
            devices=[]
            for i in range(cp.cuda.runtime.getDeviceCount()):
                props=cp.cuda.runtime.getDeviceProperties(i);name=props['name']
                devices.append(dict(index=i,name=name.decode() if isinstance(name,bytes) else str(name),
                                    memory_mb=int(props['totalGlobalMem']//2**20)))
            info['devices']=devices
            if _select(devices,lambda d:_CuPy(cp,d['index']),'CuPy',info,notes):return info
        except Exception as exc:notes.append('CuPy · '+str(exc))
    info['reason']='CPU 回退：'+'；'.join(notes or ['没有通过自检的 CUDA 设备'])
    return info

def capabilities():
    # lru_cache alone allows duplicate in-flight initializations on different threads.
    with _probe_lock:return _probe()

def _device_call(name,backend,eligible,args,check=None):
    if backend=='cpu' or not eligible or multiprocessing.parent_process() is not None:return None
    cap=capabilities()
    if cap['backend']!='cuda' or name not in cap.get('operations',(name,)):return None
    rt=_runtime
    if 'provider' not in cap:  # Source-compatible CuPy provider without recorded metadata.
        import cupy as cp
        rt=_CuPy(cp,cap.get('selected',{}).get('index',0))
    try:
        result=getattr(rt,name)(*args)
        if not np.isfinite(result).all():raise ArithmeticError('CUDA output is not finite')
        if check is not None:check(result)
        return result
    except MemoryError:
        return None
    except Exception as exc:
        logging.getLogger(__name__).error('%s GPU 运算失败，使用 CPU 重算：%s',name,exc)
        with _probe_lock:
            if 'operations' in cap and name in cap['operations']:
                cap['operations']=[op for op in cap['operations'] if op!=name]
                cap.setdefault('operation_errors',{})[name]=str(exc)
                cap['reason']+=f'；{name} 运行时停用：{exc}'
                if not cap['operations']:cap['backend']='cpu'
        return None

def blend_points(xy,weights,center,state,method='bezier',arc_angle=30.,backend='auto'):
    inputs=tuple(np.asarray(v,dtype=np.float64) for v in (xy,weights,center,state))
    def check(result):
        ids=np.linspace(0,len(xy)-1,min(17,len(xy)),dtype=int)
        expected=_blend_kernel(np,inputs[0][ids],inputs[1][ids],inputs[2],inputs[3],method,arc_angle)
        np.testing.assert_allclose(result[ids],expected,rtol=1e-12,atol=1e-8)
    result=_device_call('blend',backend,len(xy)>0 and (len(xy)>=65536 or backend=='cuda'),(*inputs,method,arc_angle),check)
    return result if result is not None else _blend_kernel(np,*inputs,method,arc_angle)

def rigid_points(points,owners,centers,states,backend='auto'):
    if not len(points):return np.array(points,copy=True)
    # Most UI layers share one owner. Avoid per-vertex owner gathers and trig arrays.
    low,high=int(np.min(owners)),int(np.max(owners))
    if high<0:return np.array(points,copy=True)
    if low==high and states[high,2]==0:return points+states[high,:2]
    result=_device_call('rigid',backend,len(points)>=(32768 if backend=='cuda' else 131072),(points,owners,centers,states))
    if result is not None:return result
    if low==high:
        dx,dy,t=states[high];pivot=centers[high];r=points-pivot;co,si=np.cos(t),np.sin(t)
        return np.column_stack((co*r[:,0]-si*r[:,1],si*r[:,0]+co*r[:,1]))+pivot+[dx,dy]
    if not np.any(states[:,2]):
        shifts=np.vstack((states[:,:2],np.zeros(2)))
        return points+shifts[np.where(owners>=0,owners,-1)]
    return _rigid_kernel(np,points,owners,centers,states)

def rigid_pose_batch(vertices,center,states,backend='auto'):
    if not np.any(states[:,2]):return vertices+states[:,None,:2]
    size=len(vertices)*len(states)
    result=_device_call('poses',backend,size>=(32768 if backend=='cuda' else 131072),(vertices,center,states))
    return result if result is not None else _pose_kernel(np,vertices,center,states)

def convolve(a,b,mode='same',backend='auto'):
    eligible=(a.ndim==b.ndim==2 and a.size and b.size and not (np.iscomplexobj(a) or np.iscomplexobj(b))
              and (backend=='cuda' or a.size+b.size>=262144))
    result=_device_call('convolution',backend,eligible,(a,b,mode))
    return result if result is not None else fftconvolve(a,b,mode=mode)

@lru_cache(maxsize=1)
def graphics_inventory():
    """OS adapter inventory is distinct from verified compute capability."""
    import subprocess,sys,shutil,json
    options=dict(text=True,stderr=subprocess.PIPE,timeout=3)
    if sys.platform=='win32':options['creationflags']=subprocess.CREATE_NO_WINDOW
    try:
        if sys.platform=='win32':
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
                'Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress'],stdout=subprocess.PIPE,**options)
            if result.returncode==0:
                entries=json.loads(result.stdout);entries=entries if isinstance(entries,list) else [entries]
                return [dict(name=q['Name'],source='Windows Win32_VideoController') for q in entries]
        elif shutil.which('lspci'):
            result=subprocess.run(['lspci','-mm'],stdout=subprocess.PIPE,**options)
            return [dict(name=line,source='PCI adapter') for line in result.stdout.splitlines() if any(t in line.lower() for t in ('vga','3d controller','display controller'))]
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).warning('显卡信息读取失败：%s',exc)
    return []
