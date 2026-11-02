"""Admission for independent preview jobs; reserve resources for the UI.

A process pool inside a job shares the same budget. Unknown load is handled
conservatively, never as an idle machine. Sampling never blocks the GUI.
"""
import threading
from contextlib import contextmanager
from .resources import resource_profile
_local=threading.local()


def load_snapshot():
    profile=resource_profile()
    try:
        import psutil
        cpu=float(psutil.cpu_percent(interval=None))
    except (ImportError,OSError):cpu=None
    return dict(profile,cpu_percent=cpu)


def admission(active,estimated_mb=256,snapshot=None):
    p=snapshot or load_snapshot();free=p['available_memory_mb'];cpu=p.get('cpu_percent')
    cap=2 if p['cpu']>=6 and free>=max(2048,estimated_mb*3) and cpu is not None and cpu<80 else 1
    start=active<cap
    # Keep an interactive lane available, give the foreground job the
    # larger share. The global CPU budget is shared by concurrent jobs.
    def share(total):
        if p['cpu']<6 or total<2:return max(1,total)
        extra=max(1,total//3)
        return extra if active else total-extra
    return dict(start=start,capacity=cap,threads=share(p['workers']),processes=share(p['process_workers']),load=p,
                reason='资源充足，独立任务并行' if start and active else '开始后台任务' if start else '当前负载较高或已有任务占用预算，排队等待')


@contextmanager
def job_budget(threads,processes):
    previous=getattr(_local,'budget',None);_local.budget=(threads,processes)
    try:yield
    finally:_local.budget=previous


def constrain(profile):
    budget=getattr(_local,'budget',None)
    if budget:
        profile=dict(profile);profile['workers']=min(profile['workers'],budget[0]);profile['process_workers']=min(profile['process_workers'],budget[1])
    return profile
