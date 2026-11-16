"""Transactional batch orchestration with persistent receipts and whole-batch replay."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .storage import Ledger, Lease, dumps, sha256
from .transport import Transport, RemoteError


class StopToken:
    """A peer failure or a user stop interrupts every outstanding client waiter."""
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < until:
            self.events[0].wait(min(.05, max(0, until - time.monotonic())))
        return self.is_set()


class Runner:
    def __init__(self, database, endpoint, project, output, workers=4, memory_mb=2048,
                 retries=3, timeout=900, stop=None, progress=lambda _: None, transport=None):
        self.database = Path(database).resolve()
        self.output = Path(output).absolute()
        if self.output.suffix.lower() != '.oas' or self.output.is_symlink():
            raise ValueError('输出必须为普通 .oas 文件')
        self.output = self.output.resolve()
        self.project = str(Path(project).resolve())
        self.endpoint = str(Path(endpoint).resolve())
        if not 1 <= workers <= 64 or not 128 <= memory_mb <= 1048576 or not 0 <= retries <= 10 or timeout < 10:
            raise ValueError('并发、内存、重试或超时设置超出范围')
        self.workers, self.memory_mb, self.retries, self.timeout = workers, memory_mb, retries, timeout
        self.stop = stop if stop is not None else threading.Event()
        self.progress = progress
        self.transport = transport or Transport(endpoint, timeout=min(15, timeout))
        self.ledger = None

    def note(self, message, level='INFO'):
        self.ledger.event(message, level)
        self.progress(message)

    def prepare(self):
        t = self.transport
        try:
            info = t.request('info', stop=self.stop, retries=1)['result']
        except (OSError, EOFError, TimeoutError):
            if t.alive():
                raise RuntimeError('服务无响应但进程仍活着；无法安全重启，保留任务等待人工处理')
            self.note('服务已退出，尝试启动同目录 Die Shift Analyze', 'WARNING')
            t.launch(self.project)
            info = t.wait_ready(self.stop, self.timeout)
        if not info.get('ready'):
            info = t.wait_ready(self.stop, self.timeout)
        if Path(info.get('project') or '').resolve() != Path(self.project):
            raise ValueError('已打开的软件工程与本任务不符；请先用调试界面打开正确工程')
        identity = info.get('session_identity')
        if not identity:
            raise ValueError('可靠批量恢复需要V44或以上服务；旧版可用手动命令调试')
        if info['die_count'] != self.ledger.get('die_count'):
            raise ValueError('Excel DIE数量与冻结工程不符')
        old = self.ledger.get('session_identity')
        if old and old != identity:
            raise ValueError('工程几何、参数或软件版本已改变；禁止将旧任务混入新工程，请新建任务')
        if self.output == Path(info.get('source') or '').resolve():
            raise ValueError('输出不得覆盖源图纸')
        self.ledger.set('session_identity', identity)
        self.ledger.set('server_info', info)
        if info.get('batch'):
            if info.get('batch_owner') != self.ledger.get('run_id'):
                raise ValueError('服务正在处理其他客户端的批次；不会取消该批次')
            self.note('接管本任务未提交批次，安全取消后完整补算', 'WARNING')
            self.cancel_owned()
        elif info.get('running'):
            raise ValueError('服务有其他活动任务，请完成或手动取消后继续')
        return info

    def cancel_owned(self):
        t = self.transport
        info = t.request('info', retries=0)['result']
        if not info.get('batch'):
            return
        if info.get('batch_owner') != self.ledger.get('run_id'):
            raise ValueError('不能取消其他客户端批次')
        t.request('compute.cancel', retries=1)
        until = time.monotonic() + min(self.timeout, 120)
        while time.monotonic() < until:
            info = t.request('info', retries=0)['result']
            if not info.get('batch') and not info.get('running'):
                return
            time.sleep(.25)
        raise TimeoutError('取消仍未到安全点；任务保留，不能开启下一批')

    def _one(self, seq, payload, batch, attempt, abort):
        if abort.is_set() or self.stop.is_set():
            raise InterruptedError('停止提交')
        key = uuid.uuid5(uuid.UUID(self.run_id), f'{attempt}:{seq}').hex
        stop = StopToken(self.stop, abort)
        # SQLite belongs to coordinator; worker receives immutable data only.
        response = self.transport.request('batch.submit', dict(payload, batch_id=batch), request_id=key, stop=stop)
        if response.get('status') != 'accepted':
            raise RemoteError(response)
        task = response['task_id']
        result = self.transport.wait_task(task, stop, self.timeout)
        if result.get('cell') != payload['cell'] or result.get('connection_failure_count', 0):
            raise RemoteError(dict(error=dict(type='GeometryFailure', message='封装结果不匹配或存在未连接动区'), result=result))
        return task, result

    def batch(self, attempt):
        ledger, t = self.ledger, self.transport
        run = ledger.get('run_id')
        self.run_id = run
        stage_name = f'RDA_{run}_{attempt}.oas'
        stage = self.output.parent / stage_name
        if stage.exists():
            raise FileExistsError('批次暂存名已经存在，拒绝覆盖')
        ledger.set('stage', str(stage))
        t.request('output.configure', {'output_directory': str(self.output.parent), 'max_threads': self.workers, 'max_memory_mb': self.memory_mb}, stop=self.stop)
        begin = t.request('batch.begin', {'filename': stage_name, 'client_id': run}, request_id=uuid.uuid5(uuid.UUID(run), f'begin:{attempt}').hex, stop=self.stop)['result']
        batch = begin['batch_id']
        lanes = max(1, min(self.workers, int(begin['threads'])))
        ledger.set('batch', batch)
        ledger.set('state', 'running')
        with ledger.db:
            ledger.db.execute("update packages set state='pending',task=null,result=null")
        self.note(f'批次{attempt}：{ledger.get("count")}封装；请求并发{self.workers}，服务实际线程{begin["threads"]}')
        abort = threading.Event()
        error = None
        pending = {}
        cursor = ledger.db.execute('select seq,payload from packages order by seq')
        completed = 0
        # Only O(workers) Futures; never enqueue the entire lot in Python.
        # An interrupted SELECT can retain a Windows file handle through the
        # exception traceback even after Connection.close(). Finalize it here.
        with closing(cursor), ThreadPoolExecutor(max_workers=lanes, thread_name_prefix='rda-package') as pool:
            exhausted = False
            while pending or not exhausted:
                if self.stop.is_set():
                    abort.set()
                    exhausted = True
                while not exhausted and not abort.is_set() and len(pending) < lanes:
                    row = cursor.fetchone()
                    if row is None:
                        exhausted = True
                        break
                    seq, text = row
                    with ledger.db:
                        ledger.db.execute("update packages set state='running' where seq=?", (seq,))
                    pending[pool.submit(self._one, seq, json.loads(text), batch, attempt, abort)] = seq
                if not pending:
                    break
                done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
                for future in done:
                    seq = pending.pop(future)
                    try:
                        task, result = future.result()
                    except Exception as exc:
                        error = error or exc
                        abort.set()
                        exhausted = True
                        with ledger.db:
                            ledger.db.execute("update packages set state='error',result=? where seq=?", (str(exc), seq))
                        self.note(f'封装序号{seq}失败：{exc}', 'ERROR')
                    else:
                        completed += 1
                        with ledger.db:
                            ledger.db.execute("update packages set state='computed',task=?,result=? where seq=?", (task, dumps(result), seq))
                        if completed == 1 or completed % 20 == 0 or completed == ledger.get('count'):
                            self.note(f'计算完成 {completed}/{ledger.get("count")}；等待整批文件提交')
        if self.stop.is_set():
            raise InterruptedError('用户停止；保留输入与任务回执，继续时补算未提交批次')
        if error:
            raise error
        reply = t.request('batch.end', {'batch_id': batch}, request_id=uuid.uuid5(uuid.UUID(run), f'end:{attempt}').hex, stop=self.stop)
        receipt = t.wait_task(reply['task_id'], self.stop, self.timeout)
        if receipt.get('cells') != ledger.get('count') or Path(receipt['path']).resolve() != stage:
            raise ValueError('OAS完成回执与请求数量或路径不符')
        from die_shift_studio.oas_validation import verify_crc
        if not verify_crc(stage, stop=self.stop):
            raise ValueError('实际OAS的CRC校验失败')
        digest = sha256(stage, self.stop)
        # Write-ahead commit record: recover a crash between rename and DB update.
        ledger.set('publish', dict(stage=str(stage), sha256=digest, receipt=receipt))
        self.publish()

    def publish(self):
        ledger = self.ledger
        record = ledger.get('publish')
        if not record:
            return False
        if self.stop.is_set():
            raise InterruptedError('已停止发布；验证后的暂存文件保留供继续')
        stage = Path(record['stage'])
        digest = record['sha256']
        if self.output.is_file() and sha256(self.output, self.stop) == digest:
            pass  # previous rename succeeded before the journal commit
        elif stage.is_file() and sha256(stage, self.stop) == digest:
            previous = ledger.get('previous_output')
            current = sha256(self.output, self.stop) if self.output.is_file() else None
            if current != previous or self.output.is_symlink():
                raise ValueError('目标文件被其他程序修改，保留新旧输出等待人工处理')
            if self.stop.is_set():
                raise InterruptedError('已停止发布；旧输出未替换')
            os.replace(stage, self.output)
        else:
            raise ValueError('已完成批次文件缺失或校验不符，请保留日志检查磁盘')
        with ledger.db:
            ledger.db.execute("update packages set state='committed'")
        ledger.set('state', 'completed')
        self.note(f'完成：{ledger.get("count")}封装，{self.output}，SHA-256 {digest}')
        return True

    def run(self):
        if not self.database.is_file():
            raise FileNotFoundError('任务数据库不存在')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with Lease(self.database), Lease(self.output):
            self.ledger = Ledger(self.database)
            ledger = self.ledger
            try:
                if not ledger.get('count'):
                    raise ValueError('数据库没有完整导入的封装')
                config = dict(endpoint=self.endpoint, project=self.project, output=str(self.output))
                if ledger.get('config') and ledger.get('config') != config:
                    raise ValueError('继续任务须使用原入口、工程和输出路径')
                if not ledger.get('config'):
                    ledger.set('config', config)
                    ledger.set('previous_output', sha256(self.output, self.stop) if self.output.is_file() else None)
                if self.publish():
                    return {'state': 'completed', 'output': str(self.output)}
                for retry in range(self.retries + 1):
                    if self.stop.is_set():
                        raise InterruptedError('用户停止')
                    attempt = ledger.get('attempt', 0) + 1
                    ledger.set('attempt', attempt)
                    try:
                        self.prepare()
                        self.batch(attempt)
                        return {'state': 'completed', 'output': str(self.output)}
                    except InterruptedError:
                        raise
                    except (OSError, EOFError, TimeoutError, RemoteError) as exc:
                        self.note(f'第{attempt}批次未提交：{exc}', 'WARNING')
                        if ledger.get('publish'):
                            raise  # never overwrite a pending publication record
                        # Deterministic input/geometry failures need correction.
                        if isinstance(exc, RemoteError) and exc.kind not in ('Busy', 'MemoryError', 'TimeoutError'):
                            raise
                        if retry >= self.retries:
                            raise
                        if isinstance(exc, RemoteError) and exc.kind == 'MemoryError':
                            self.workers = max(1, self.workers // 2)
                        self.note(f'等待后补算整批（剩余自动重试{self.retries - retry}次）', 'WARNING')
                        if self.stop.wait(min(5, retry + 1)):
                            raise InterruptedError('用户停止')
                raise RuntimeError('自动恢复耗尽')
            except Exception as exc:
                ledger.set('state', 'paused' if isinstance(exc, InterruptedError) else 'blocked')
                self.note(f'已保留任务，可修复后继续：{exc}', 'ERROR')
                try:
                    self.cancel_owned()
                except Exception as cleanup:
                    self.note(f'安全取消未确认：{cleanup}；不强杀原生计算', 'WARNING')
                raise
            finally:
                ledger.close()
