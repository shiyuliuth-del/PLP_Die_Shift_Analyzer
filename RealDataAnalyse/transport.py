"""Bounded local IPC deadlines and installation-local process recovery."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from die_shift_studio.local_ipc import call


def installation():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]


def sibling_app():
    root = installation()
    app = root / ('Die_Shift_Analyze.exe' if getattr(sys, 'frozen', False) else 'main.py')
    if not app.is_file():
        raise FileNotFoundError(f'未找到同目录程序 {app}；不会搜索其他系统目录')
    return app


def default_endpoint():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'state')) / 'DieShiftAnalyze' / 'ipc' / 'endpoint.json'


class RemoteError(RuntimeError):
    def __init__(self, response):
        self.response = response
        self.kind = response.get('error', {}).get('type', 'RemoteError')
        super().__init__(json.dumps(response, ensure_ascii=False))


class Transport:
    def __init__(self, endpoint, timeout=15, capacity=128):
        self.endpoint = Path(endpoint).resolve()
        self.timeout = timeout
        self.slots = threading.BoundedSemaphore(capacity)
        self.process = None
        self.descriptor = None

    def rpc(self, command, args=None, request_id=None):
        # Client() authentication itself has no timeout. Bound it too; stuck
        # daemon workers are capped and never keep GUI shutdown alive.
        if not self.slots.acquire(timeout=self.timeout):
            raise TimeoutError('IPC连接槽已耗尽；服务可能无响应')
        result = queue.Queue(maxsize=1)
        def invoke():
            try:
                descriptor = json.loads(self.endpoint.read_text(encoding='utf8'))
                self.descriptor = descriptor
                result.put((True, call(self.endpoint, command, args, self.timeout, request_id)))
            except Exception as exc:
                result.put((False, exc))
            finally:
                self.slots.release()
        threading.Thread(target=invoke, name='rda-ipc', daemon=True).start()
        try:
            ok, value = result.get(timeout=self.timeout + .5)
        except queue.Empty:
            raise TimeoutError('IPC连接、认证或响应超时') from None
        if not ok:
            raise value
        return value

    def request(self, command, args=None, request_id=None, stop=None, retries=5):
        key = request_id or uuid.uuid4().hex
        for attempt in range(retries + 1):
            if stop is not None and stop.is_set():
                raise InterruptedError('用户停止')
            try:
                response = self.rpc(command, args, key)
            except (OSError, EOFError, TimeoutError):
                if attempt == retries:
                    raise
            else:
                if response.get('status') != 'error':
                    return response
                if response.get('error', {}).get('type') != 'Busy' or attempt == retries:
                    raise RemoteError(response)
            delay = min(2., .2 * 2**attempt)
            if stop is not None:
                if stop.wait(delay):
                    raise InterruptedError('用户停止')
            else:
                time.sleep(delay)
        raise AssertionError('unreachable')

    def alive(self):
        import psutil
        descriptor = self.descriptor
        if self.endpoint.exists():
            try:
                descriptor = json.loads(self.endpoint.read_text(encoding='utf8'))
            except (OSError, ValueError):
                pass
        if descriptor:
            try:
                p = psutil.Process(descriptor['pid'])
                # Legacy descriptors without a birth time are conservatively alive.
                return p.is_running() and p.status() != psutil.STATUS_ZOMBIE and (
                    'process_created' not in descriptor or abs(p.create_time() - descriptor['process_created']) < .01)
            except psutil.NoSuchProcess:
                return False
            except (psutil.AccessDenied, KeyError):
                return True
        return self.process is not None and self.process.poll() is None

    def launch(self, project):
        if self.alive():
            raise RuntimeError('Die Shift Analyze进程仍在；等待安全恢复，不重复启动或强杀')
        app = sibling_app()
        project = Path(project).resolve()
        if not project.is_file():
            raise FileNotFoundError('重启需要已保存的工程文件')
        self.endpoint.parent.mkdir(parents=True, exist_ok=True)
        command = [str(app)] if app.suffix.lower() == '.exe' else [sys.executable, str(app)]
        command += ['--project', str(project), '--measured-output', '--ipc-endpoint', str(self.endpoint)]
        logs = installation() / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 'RealDataAnalyse-child.log').open('ab') as stream:
            self.process = subprocess.Popen(command, cwd=app.parent, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return self.process.pid

    def wait_ready(self, stop, timeout=900):
        until = time.monotonic() + timeout
        last = None
        while time.monotonic() < until:
            if stop.is_set():
                raise InterruptedError('已停止等待工程')
            try:
                response = self.request('info', stop=stop, retries=0)
                info = response['result']
                if info.get('ready'):
                    return info
                if info.get('last_error'):
                    raise RemoteError(dict(error=info['last_error']))
                last = '工程仍在准备'
            except (OSError, EOFError, TimeoutError) as exc:
                last = str(exc)
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError(f'程序启动退出 {self.process.returncode}；见RealDataAnalyse-child.log') from exc
            stop.wait(.3)
        raise TimeoutError(f'等待工程ready超时：{last}')

    def wait_task(self, task_id, stop, timeout=900):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            task = self.request('task.get', {'task_id': task_id}, stop=stop)['result']
            if task['status'] == 'completed':
                return task['result']
            if task['status'] in ('error', 'cancelled'):
                raise RemoteError(task)
            if stop.wait(.25):
                raise InterruptedError('用户停止')
        raise TimeoutError(f'任务 {task_id} 超时；软件可能仍在安全点前运行')
