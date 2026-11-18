"""Isolated process lanes for independent, large measured lots."""
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
import multiprocessing
import os
import queue

from .resources import cancellation_scope, cancel_check, serial_scope

_session = None
_stop = None
_status = None
_memory = 0


def _initialize(inputs, stop, status, memory_mb):
    global _session, _stop, _status, _memory
    from .measured_core import Session
    _stop, _status, _memory = stop, status, memory_mb * 1024**2
    _session = Session(*inputs)


def _compute(rows, policy, slot):
    import psutil
    parent = psutil.Process(os.getppid())
    last_check = -1

    def progress(percent, _message):
        nonlocal last_check
        cancel_check()
        _status[slot] = int(percent)
        if percent >= last_check + 5:
            last_check = percent
            used = 0
            for process in parent.children(recursive=True):
                try:
                    used += process.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            if used > _memory:
                raise MemoryError('独立计算进程超过批次共享内存预算')

    with cancellation_scope(_stop), serial_scope():
        _, snapshot, details = _session.run(rows,progress,preview=False,output_policy=policy)
        cancel_check()
        return snapshot.layers,details


class BatchWorkers:
    def __init__(self, session, count, memory_mb):
        context = multiprocessing.get_context('spawn')
        self.stop = context.Event()
        self.status = context.Array('i',count,lock=False)
        self.slots = queue.SimpleQueue()
        for slot in range(count):
            self.slots.put(slot)
        # Only in-memory frozen project data goes to our own worker processes.
        # No IPC request or on-disk sidecar is deserialized as Python objects.
        inputs = (session.data,session.case,session.roles,session.motions,
                  session.p,session.settings,session.cache)
        self.pool = ProcessPoolExecutor(count,mp_context=context,initializer=_initialize,
                                        initargs=(inputs,self.stop,self.status,memory_mb))

    def solve(self, rows, policy, progress):
        slot = self.slots.get_nowait()
        self.status[slot] = 0
        try:
            future = self.pool.submit(_compute,rows,policy,slot)
            while True:
                try:
                    return future.result(timeout=.25)
                except FutureTimeout:
                    if future.done():
                        return future.result()
                    try:
                        progress(self.status[slot],'独立进程重构与DRC')
                    except InterruptedError:
                        self.stop.set()
                        # All queued/running child functions share this event.
                        # Let the child reach its cancel point before batch close.
                        future.cancel()
                        raise
        except BrokenProcessPool as exc:
            raise TimeoutError('独立计算进程意外退出，批次未提交') from exc
        finally:
            self.slots.put(slot)

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True,cancel_futures=True)
