"""Small, offline CUDA driver backend. No CuPy, SDK DLLs or compiler on the target."""
import ctypes as C
import os,sys
from contextlib import contextmanager,ExitStack
from functools import lru_cache
from pathlib import Path
from threading import RLock
import numpy as np

I=C.c_int;U=C.c_uint;S=C.c_size_t;P=C.c_void_p;D=C.c_uint64
ptr=C.POINTER
_SIGNATURES={
    'cuInit':(U,), 'cuDriverGetVersion':(ptr(I),), 'cuDeviceGetCount':(ptr(I),),
    'cuDeviceGet':(ptr(I),I), 'cuDeviceGetName':(C.c_char_p,I,I),
    'cuDeviceTotalMem_v2':(ptr(S),I), 'cuDeviceGetAttribute':(ptr(I),I,I),
    'cuDevicePrimaryCtxRetain':(ptr(P),I), 'cuDevicePrimaryCtxRelease_v2':(I,),
    'cuCtxPushCurrent_v2':(P,), 'cuCtxPopCurrent_v2':(ptr(P),),
    'cuModuleLoadData':(ptr(P),P), 'cuModuleUnload':(P,), 'cuModuleGetFunction':(ptr(P),P,C.c_char_p),
    'cuMemGetInfo_v2':(ptr(S),ptr(S)), 'cuMemAlloc_v2':(ptr(D),S), 'cuMemFree_v2':(D,),
    'cuMemcpyHtoD_v2':(D,P,S), 'cuMemcpyDtoH_v2':(P,D,S),
    'cuLaunchKernel':(P,U,U,U,U,U,U,U,P,ptr(P),ptr(P)), 'cuGetErrorName':(I,ptr(C.c_char_p)),
}

class API:
    def __init__(self,library=None):
        if library is None:
            if sys.platform=='win32':
                path=Path(os.environ['SystemRoot'])/'System32'/'nvcuda.dll'
                library=C.WinDLL(str(path),winmode=0x800)
            else:library=C.CDLL('libcuda.so.1')
        self.lib=library
        for name,args in _SIGNATURES.items():
            fn=getattr(library,name);fn.argtypes=args;fn.restype=I
        self.call('cuInit',0)
        self.version=self.integer('cuDriverGetVersion')

    def call(self,name,*args):
        status=getattr(self.lib,name)(*args)
        if status:
            label=C.c_char_p();self.lib.cuGetErrorName(status,C.byref(label))
            detail=label.value.decode('ascii','replace') if label.value else str(status)
            if status==2:raise MemoryError(name+' · '+detail)
            raise RuntimeError(name+' · '+detail)

    def integer(self,name,*args):
        result=I();self.call(name,C.byref(result),*args);return result.value

    def devices(self):
        devices=[]
        for ordinal in range(self.integer('cuDeviceGetCount')):
            dev=self.integer('cuDeviceGet',ordinal);name=C.create_string_buffer(256);size=S()
            self.call('cuDeviceGetName',name,len(name),dev)
            self.call('cuDeviceTotalMem_v2',C.byref(size),dev)
            arch=tuple(self.integer('cuDeviceGetAttribute',a,dev) for a in (75,76))
            devices.append(dict(index=ordinal,device=dev,name=name.value.decode('utf8','replace'),
                                memory_mb=size.value//2**20,arch=arch))
        return devices

class Batch:
    """Own every temporary device allocation for precisely one operation."""
    def __init__(self,runtime,stack):
        self.runtime=runtime;self.api=runtime.api;self.stack=stack

    def allocate(self,nbytes):
        address=D();self.api.call('cuMemAlloc_v2',C.byref(address),max(1,nbytes))
        self.stack.callback(self.api.call,'cuMemFree_v2',address)
        return address

    def upload(self,values):
        host=np.ascontiguousarray(values);address=self.allocate(host.nbytes)
        if host.nbytes:self.api.call('cuMemcpyHtoD_v2',address,host.ctypes.data,host.nbytes)
        return address

    def result(self,address,shape):
        host=np.empty(shape,dtype=np.float64)
        if host.nbytes:self.api.call('cuMemcpyDtoH_v2',host.ctypes.data,address,host.nbytes)
        if not np.isfinite(host).all():raise ArithmeticError('CUDA output is not finite')
        return host

    def launch(self,name,n,*args):
        if not n:return
        if name not in self.runtime.functions:
            fn=P();self.api.call('cuModuleGetFunction',C.byref(fn),self.runtime.module,name.encode())
            self.runtime.functions[name]=fn
        args=[I(v) if isinstance(v,int) else C.c_double(v) if isinstance(v,float) else v for v in args]
        addresses=(P*len(args))(*(C.addressof(v) for v in args))
        self.api.call('cuLaunchKernel',self.runtime.functions[name],(n+127)//128,1,1,128,1,1,0,None,addresses,None)

class Runtime:
    def __init__(self,api,device):
        self.api=api;self.device=device;self.lock=RLock();self.module=P();self.context=P();self.functions={}
        self.api.call('cuDevicePrimaryCtxRetain',C.byref(self.context),device['device'])
        try:
            with self.active():
                ptx=Path(__file__).with_name('cuda_kernels.ptx').read_bytes()
                self.api.call('cuModuleLoadData',C.byref(self.module),C.cast(C.c_char_p(ptx),P))
        except BaseException:
            self.close();raise

    @contextmanager
    def active(self):
        with self.lock:
            self.api.call('cuCtxPushCurrent_v2',self.context)
            try:yield
            finally:self.api.call('cuCtxPopCurrent_v2',C.byref(P()))

    @contextmanager
    def batch(self,budget):
        with self.active(),ExitStack() as release:
            free=S();total=S();self.api.call('cuMemGetInfo_v2',C.byref(free),C.byref(total))
            if budget>free.value*.45:raise MemoryError('该批次超过可用显存预算')
            yield Batch(self,release)

    def close(self):
        with self.lock:
            try:
                if self.module:
                    with self.active():self.api.call('cuModuleUnload',self.module)
            finally:
                self.module=P();self.functions.clear()
                if self.context:
                    self.api.call('cuDevicePrimaryCtxRelease_v2',self.device['device']);self.context=P()

    @staticmethod
    def table(centers,states):
        return np.column_stack((np.broadcast_to(centers,(len(states),2)),states[:,:2],
                                np.cos(states[:,2]),np.sin(states[:,2]),states[:,2])).astype('float64',copy=False)

    def rigid(self,points,owners,centers,states):
        n=len(points);table=self.table(centers,states)
        with self.batch(n*36+table.nbytes) as b:
            src=b.upload(np.asarray(points,dtype='float64'));own=b.upload(np.asarray(owners,dtype='int32'))
            out=b.allocate(n*16);b.launch('rigid',n,n,src,own,b.upload(table),out)
            return b.result(out,(n,2))

    def poses(self,vertices,center,states):
        n=len(vertices)*len(states);table=self.table(center,states)
        with self.batch((n+len(vertices))*16+table.nbytes) as b:
            src=b.upload(np.asarray(vertices,dtype='float64'));out=b.allocate(n*16)
            b.launch('poses',n,n,len(vertices),src,b.upload(table),out)
            return b.result(out,(len(states),len(vertices),2))

    def blend(self,points,weights,center,state,method,angle):
        n=len(points);table=self.table(center,np.asarray(state).reshape(1,3))
        gamma=float(np.deg2rad(angle))*(1 if state[2]>=0 else -1)
        cot=float(1/np.tan(gamma/2)) if method=='arc' else 0.
        with self.batch(n*40+table.nbytes) as b:
            out=b.allocate(n*16)
            b.launch('blend',n,n,b.upload(points),b.upload(weights),b.upload(table),int(method=='arc'),gamma,cot,out)
            return b.result(out,(n,2))

    def convolution(self,a,b,mode):
        # Linear convolution, including SciPy's asymmetric "same" crop for even footprints.
        a=np.asarray(a,dtype=np.float64);b=np.asarray(b,dtype=np.float64)
        h,w=np.add(a.shape,b.shape)-1;h,w=int(h),int(w)
        if mode=='full':shape=(h,w);x0=y0=0
        elif mode=='same':shape=a.shape;x0=(b.shape[1]-1)//2;y0=(b.shape[0]-1)//2
        elif mode=='valid':
            if not (all(x>=y for x,y in zip(a.shape,b.shape)) or all(x<=y for x,y in zip(a.shape,b.shape))):
                raise ValueError('valid requires one operand to contain the other')
            shape=tuple(abs(x-y)+1 for x,y in zip(a.shape,b.shape));y0=(h-shape[0])//2;x0=(w-shape[1])//2
        else:raise ValueError('unknown convolution mode')
        nx=1<<(w-1).bit_length();ny=1<<(h-1).bit_length();n=nx*ny;size=int(np.prod(shape))
        if n>=2**31:raise MemoryError('FFT grid exceeds index range')
        tw=twiddles(max(nx,ny))
        with self.batch(48*n+tw.nbytes+a.nbytes+b.nbytes+size*8) as work:
            roots=work.upload(tw)
            def fft(buf,inverse):
                for vertical,axis in enumerate((nx,ny)):
                    span=2
                    while span<=axis:
                        work.launch('fft_step',n//2,nx,ny,span,vertical,inverse,roots,buf)
                        span*=2
            inputs=[]
            for values in (a,b):
                v=work.allocate(n*16)
                work.launch('fft_load',n,nx,ny,values.shape[1],values.shape[0],work.upload(values),v)
                fft(v,0);inputs.append(v)
            product=work.allocate(n*16);work.launch('fft_product',n,nx,ny,*inputs,product);fft(product,1)
            out=work.allocate(size*8);work.launch('fft_crop',size,nx,ny,shape[1],shape[0],x0,y0,product,out)
            return work.result(out,shape)

@lru_cache(maxsize=4)
def twiddles(n):
    return np.exp((-2j*np.pi/n)*np.arange(max(1,n//2),dtype='float64'))
