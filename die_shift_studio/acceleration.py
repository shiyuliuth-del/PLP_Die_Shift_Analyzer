"""Optional CUDA convolution; CPU is always a complete, equivalent fallback."""
import logging
from functools import lru_cache
from threading import Lock
import numpy as np
from scipy.signal import fftconvolve

_gpu_lock=Lock()


def _blend_kernel(xp,xy,w,center,state,method,arc_angle):
    """Float64 only; shared NumPy/CuPy formula for audited coordinate mapping."""
    angle=state[2];co=xp.cos(angle);si=xp.sin(angle);r=xy-center
    dest=xp.stack((r[:,0]*co-r[:,1]*si,r[:,0]*si+r[:,1]*co),axis=1)+center+state[:2]
    delta=dest-xy
    if method=='bezier':return xy+w[:,None]*delta
    gamma=arc_angle*xp.pi/180*(1 if angle>=0 else -1);phase=gamma*w;cot=1/xp.tan(gamma/2)
    a=.5*(1-xp.cos(phase)+cot*xp.sin(phase));b=.5*(cot*(1-xp.cos(phase))-xp.sin(phase))
    return xy+a[:,None]*delta+b[:,None]*xp.stack((-delta[:,1],delta[:,0]),axis=1)


def blend_points(xy,weights,center,state,method='bezier',arc_angle=30.,backend='auto'):
    """Offload large coordinate batches only; native topology/DRC remain on CPU.

    Worker processes deliberately use CPU to avoid independent CUDA contexts
    competing for VRAM. No dependency download or unverified geometry fallback.
    """
    from .resources import _local
    inputs=[np.asarray(v,dtype=np.float64) for v in (xy,weights,center,state)]
    if backend!='cpu' and not getattr(_local,'nested',False) and (len(xy)>=65536 or backend=='cuda') and capabilities()['backend']=='cuda':
        try:
            import cupy as cp
            with _gpu_lock,cp.cuda.Device(0):
                free,_=cp.cuda.runtime.memGetInfo()
                if len(xy)*8*32>free*.35:raise MemoryError('型变坐标批次超过GPU内存预算')
                gpu=_blend_kernel(cp,*[cp.asarray(v) for v in inputs],method,arc_angle)
                result=cp.asnumpy(gpu)
                # Per-batch sentinel catches runtime/platform numerical drift.
                indices=np.linspace(0,len(xy)-1,min(17,len(xy)),dtype=int)
                expected=_blend_kernel(np,inputs[0][indices],inputs[1][indices],inputs[2],inputs[3],method,arc_angle)
                if not np.isfinite(result).all() or not np.allclose(result[indices],expected,rtol=1e-12,atol=1e-8):raise ValueError('GPU型变坐标自检不一致')
                return result
        except Exception:
            logging.getLogger(__name__).exception('GPU型变坐标计算失败，使用CPU重算该批次')
    return _blend_kernel(np,*inputs,method,arc_angle)

@lru_cache(maxsize=1)
def capabilities():
    info={'backend':'cpu','devices':[],'reason':'未安装可用的 CuPy/CUDA 运行时'}
    try:
        import cupy as cp
        for i in range(cp.cuda.runtime.getDeviceCount()):
            props=cp.cuda.runtime.getDeviceProperties(i)
            name=props['name'];name=name.decode() if isinstance(name,bytes) else str(name)
            info['devices'].append(dict(index=i,name=name,memory_mb=int(props['totalGlobalMem']//1024**2)))
        if info['devices']:
            from cupyx.scipy.signal import fftconvolve as gpu_convolve
            with cp.cuda.Device(0):
                got=cp.asnumpy(gpu_convolve(cp.ones((5,7)),cp.ones((3,3)),mode='same'))
                np.testing.assert_allclose(got,fftconvolve(np.ones((5,7)),np.ones((3,3)),mode='same'),atol=1e-10)
                xy=np.arange(64,dtype=float).reshape(32,2);weights=np.linspace(0,1,32);center=np.array([20.,-5.]);state=np.array([-35.,-35.,-.017])
                for method in ('bezier','arc'):
                    inputs=(xy,weights,center,state)
                    got=cp.asnumpy(_blend_kernel(cp,*[cp.asarray(v) for v in inputs],method,30.))
                    np.testing.assert_allclose(got,_blend_kernel(np,*inputs,method,30.),rtol=1e-12,atol=1e-8)
            info.update(backend='cuda',reason='CUDA 卷积与型变坐标自检通过；拓扑及最终DRC使用CPU')
    except Exception as exc:info['reason']=f'CPU 回退：{type(exc).__name__}: {exc}'
    return info


def convolve(a,b,mode='same',backend='auto'):
    # Small grids cost more to transfer than to compute. Serialize GPU work to
    # bound device memory, while independent CPU geometry jobs remain threaded.
    if backend!='cpu' and (backend=='cuda' or a.size+b.size>=262144) and capabilities()['backend']=='cuda':
        try:
            import cupy as cp
            from cupyx.scipy.signal import fftconvolve as gpu_convolve
            with _gpu_lock,cp.cuda.Device(0):
                free,_=cp.cuda.runtime.memGetInfo()
                need=8*16*np.prod(np.asarray(a.shape)+b.shape)
                if need>free*.5:raise MemoryError('GPU 卷积临时内存预算不足')
                return cp.asnumpy(gpu_convolve(cp.asarray(a),cp.asarray(b),mode=mode))
        except Exception:
            logging.getLogger(__name__).exception('GPU 计算未完成，使用 CPU 重算同一任务')
    return fftconvolve(a,b,mode=mode)

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
