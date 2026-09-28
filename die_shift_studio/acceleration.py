"""Optional CUDA convolution; CPU is always a complete, equivalent fallback."""
import logging
from functools import lru_cache
from threading import Lock
import numpy as np
from scipy.signal import fftconvolve

_gpu_lock=Lock()

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
            info.update(backend='cuda',reason='CUDA 浮点卷积自检通过')
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
