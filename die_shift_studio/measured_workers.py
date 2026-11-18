"""Isolated process lanes for independent, large measured lots."""
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
import multiprocessing
import queue
import threading
import time

from .resources import cancellation_scope, cancel_check, serial_scope

_session = None
_stop = None
_status = None
_over_budget = None


def _initialize(inputs, stop, status, over_budget):
    global _session, _stop, _status, _over_budget
    from .measured_core import Session
    _stop, _status, _over_budget = stop, status, over_budget
    _session = Session(*inputs)


def _compute(rows, policy, slot):
    def progress(percent, _message):
        cancel_check()
        _status[slot] = 20+int(percent)*4//5
        if _over_budget.is_set():
            raise MemoryError('独立计算进程超过批次共享内存预算')

    with cancellation_scope(_stop), serial_scope():
        _, snapshot, details = _session.run(rows,progress,preview=False,output_policy=policy)
        cancel_check()
        progress(100,'重构与DRC完成')
        return snapshot.layers,details


class BatchWorkers:
    def __init__(self, session, count, memory_mb):
        import psutil
        self.session = session
        self.count = count
        self.parent = psutil.Process()
        self.memory_bytes = memory_mb*1024**2
        self.context = multiprocessing.get_context('spawn')
        self.stop = self.context.Event()
        self.over_budget = self.context.Event()
        self.status = self.context.Array('i',count,lock=False)
        self.slots = queue.SimpleQueue()
        for slot in range(count):
            self.slots.put(slot)
        self.init_lock = threading.Lock()
        self.pool = None
        self.budget_lock = threading.Lock()
        self.last_sample = 0.

    def executor(self, progress):
        while not self.init_lock.acquire(timeout=.1):
            cancel_check()
        try:
            cancel_check()
            if self.pool is None:
                # Prepare the frozen nominal routes once, before spawning.
                # RoutePlan serialization drops mutable pose caches. Workers
                # retain private GEOS/KLayout objects and can recompute misses.
                self.session.prepare(lambda pct,text: progress(int(pct)//5,text))
                cancel_check()
                s = self.session
                inputs = (s.data,s.case,s.roles,s.motions,s.p,s.settings,s.cache,s.plan_pool)
                self.pool = ProcessPoolExecutor(self.count,mp_context=self.context,initializer=_initialize,
                                                initargs=(inputs,self.stop,self.status,self.over_budget))
            return self.pool
        finally:
            self.init_lock.release()

    def monitor_memory(self):
        import psutil
        now = time.monotonic()
        if now-self.last_sample<.5 or not self.budget_lock.acquire(blocking=False):
            return
        try:
            self.last_sample=now
            total=0
            for process in self.parent.children(recursive=True):
                try:
                    total+=process.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            if total>self.memory_bytes:
                self.over_budget.set()
                raise MemoryError('独立计算进程超过批次共享内存预算')
        finally:
            self.budget_lock.release()

    def solve(self, rows, policy, progress):
        pool = self.executor(progress)
        slot = self.slots.get_nowait()
        self.status[slot] = 20
        try:
            future = pool.submit(_compute,rows,policy,slot)
            while True:
                try:
                    return future.result(timeout=.25)
                except FutureTimeout:
                    if future.done():
                        return future.result()
                    self.monitor_memory()
                    progress(self.status[slot],'独立进程重构与DRC')
        except InterruptedError:
            # The controller closes the pool only after cooperative cancellation.
            self.stop.set()
            raise
        except BrokenProcessPool as exc:
            raise TimeoutError('独立计算进程意外退出，批次未提交') from exc
        finally:
            self.slots.put(slot)

    def close(self):
        self.stop.set()
        if self.pool is not None:
            self.pool.shutdown(wait=True,cancel_futures=True)
