"""Hardware-aware bounded concurrency; native geometry/FFT jobs release the GIL."""
import os
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
import multiprocessing

_local=threading.local()
_limit_lock=threading.Lock();_shared_limit=None

def resource_profile():
    cpu=os.cpu_count() or 1
    try:cpu=min(cpu,len(os.sched_getaffinity(0)))
    except (AttributeError,OSError):pass
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':cpu=min(cpu,max(1,int(quota)//int(period)))
    except (OSError,ValueError):pass
    try:
        import psutil
        mem=psutil.virtual_memory();available=mem.available;total=mem.total
    except ImportError:
        try:available=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE');total=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (ValueError,OSError,AttributeError):available=total=768*1024**2
    try:
        limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
        if limit!='max':total=min(total,int(limit));available=min(available,max(0,int(limit)-int(Path('/sys/fs/cgroup/memory.current').read_text())))
    except (OSError,ValueError):pass
    workers=max(1,min(12,max(1,cpu-2),int(available*.35)//(384*1024**2)))
    return dict(cpu=cpu,total_memory_mb=round(total/1024**2),available_memory_mb=round(available/1024**2),workers=workers,
                process_workers=max(1,min(4,workers,int(available*.35)//(768*1024**2))),
                policy='预留2逻辑CPU与65%可用内存；原生几何/FFT使用线程；大型独立原图DRC与RDL候选搜索使用spawn进程；小任务串行')


def parallel_map(fn,items,profile=None,progress=None,process=False):
    items=list(items);profile=profile or resource_profile();workers=min(profile['process_workers' if process else 'workers'],len(items))
    if getattr(_local,'nested',False):workers=1
    if workers<2:
        result=[]
        for item in items:
            result.append(fn(item))
            if progress:progress(len(result),len(items))
        return result
    global _shared_limit
    with _limit_lock:
        if _shared_limit is None:_shared_limit=threading.BoundedSemaphore(profile['workers'])
    def wrapped(item):
        with _shared_limit:
            _local.nested=True
            try:return fn(item)
            finally:_local.nested=False
    options=dict(max_workers=workers)
    if process:options['mp_context']=multiprocessing.get_context('spawn')
    else:options['thread_name_prefix']='die-shift'
    executor=(ProcessPoolExecutor if process else ThreadPoolExecutor)(**options)
    futures={};result=[None]*len(items)
    try:
        futures={executor.submit(fn if process else wrapped,item):i for i,item in enumerate(items)}
        for done,future in enumerate(as_completed(futures),1):
            result[futures[future]]=future.result()
            if progress:progress(done,len(items))
    except BaseException:
        for f in futures:f.cancel()
        raise
    finally:executor.shutdown(wait=True,cancel_futures=True)
    return result
