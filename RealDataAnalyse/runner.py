"""Transactional batch orchestration with persistent receipts and whole-batch replay."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
import json
import os
import shutil
import hashlib
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
                 retries=3, timeout=0, stop=None, progress=lambda _: None, transport=None, trace=None):
        self.database = Path(database).resolve()
        self.output = Path(output).absolute()
        if self.output.suffix.lower() != '.oas' or self.output.is_symlink():
            raise ValueError('输出必须为普通 .oas 文件')
        self.output = self.output.resolve()
        self.managed_output = self.output.parent.name.lower() == 'output'
        self.cache = self.output.parent.parent / 'Cache' if self.managed_output else self.output.parent / 'Cache'
        self.project = str(Path(project).resolve())
        self.endpoint = str(Path(endpoint).resolve())
        if not 1 <= workers <= 64 or not 128 <= memory_mb <= 1048576 or not 0 <= retries <= 10 or timeout < 0:
            raise ValueError('并发、内存、重试或超时设置超出范围')
        self.workers, self.memory_mb, self.retries, self.timeout = workers, memory_mb, retries, timeout
        self.stop = stop if stop is not None else threading.Event()
        self.progress = progress
        self.transport = transport or Transport(endpoint, timeout=min(15, timeout) if timeout else 15, trace=trace)
        self.ledger = None
        self.active_progress = {}
        self.progress_lock = threading.Lock()
        self.last_progress = 0
        self.completed_count = 0

    def note(self, message, level='INFO'):
        self.ledger.event(message, level)
        self.progress(message)

    def prepare(self):
        self.progress(dict(text='连接预演软件 · 核对工程与任务身份', current=0, total=1))
        t = self.transport
        try:
            info = t.request('info', stop=self.stop, retries=1)['result']
        except (OSError, EOFError, TimeoutError, json.JSONDecodeError):
            if t.alive():
                raise RuntimeError('服务无响应但进程仍活着；无法安全重启，保留任务等待人工处理')
            self.note('服务已退出，尝试启动同目录 Die Shift Analyze', 'WARNING')
            t.launch(self.project)
            info = t.wait_ready(self.stop, self.timeout, progress=self.progress) if isinstance(t, Transport) else t.wait_ready(self.stop, self.timeout)
        if not info.get('ready'):
            info = t.wait_ready(self.stop, self.timeout, progress=self.progress) if isinstance(t, Transport) else t.wait_ready(self.stop, self.timeout)
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
        if self.managed_output and Path(info.get('source') or '').resolve().is_relative_to(self.output.parent):
            raise ValueError('服务图纸位于Output内，拒绝移动输入；请重新组织项目输入')
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
        until = time.monotonic() + (min(self.timeout, 120) if self.timeout else 120)
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
        def report(update):
            with self.progress_lock:
                self.active_progress[seq] = update
                now = time.monotonic()
                if now - self.last_progress < .2:
                    return
                self.last_progress = now
                fraction = sum(min(99, max(0, u.get('current', 0))) / 100 for u in self.active_progress.values())
                text = f'批次{attempt} · 完成{self.completed_count}/{self.total} · 并行{len(self.active_progress)} · 封装{seq} {update.get("text", "计算中")}'
                self.progress(dict(text=text, current=int((self.completed_count + fraction) * 100), total=self.total * 100))
        if isinstance(self.transport, Transport):
            result = self.transport.wait_task(task, stop, self.timeout, progress=report)
        else:
            result = self.transport.wait_task(task, stop, self.timeout)
        self.validate_pose(result, payload['cell'])
        return task, result

    @staticmethod
    def validate_pose(result, cell):
        if result.get('cell') != cell:
            raise ValueError('封装回执名称不匹配')
        for key in ('connection_failure_count', 'drc_count', 'error_rectangles', 'omitted_region_count', 'failed_packages'):
            value=result.get(key,0)
            if type(value) is not int or value<0:raise ValueError('问题回执计数无效：'+key)
        if result.get('fixed_rdl_drc_checked') is not True:
            raise ValueError('服务未完成当前姿态DRC，不能把未知问题数当作零')
        if result.get('connection_failure_count',0) and (result.get('partial_output') is not True or not result.get('omitted_region_count')):
            raise ValueError('连接失败但未确认移除失败位置，保留记录等待正确服务回执')
        failed=int(bool(result.get('connection_failure_count',0)))
        if result.get('failed_packages',0)!=failed:
            raise ValueError('FAIL封装边框回执与连接失败状态不符，请使用配套新版本')
        if failed and (not isinstance(result.get('failure_layer'),dict) or result['failure_layer'].get('name')!='FAIL'):
            raise ValueError('断开封装缺少FAIL命名层回执')
        if (result.get('drc_count',0) or result.get('connection_failure_count',0)) and not result.get('error_rectangles'):
            raise ValueError('存在DRC或连接问题但没有ERROR矩形，拒绝静默遗漏')

    def batch(self, attempt):
        ledger, t = self.ledger, self.transport
        run = ledger.get('run_id')
        self.run_id = run
        stage_name = f'RDA_{run}_{attempt}.oas'
        directory = self.cache / 'Batches' / run
        directory.mkdir(parents=True, exist_ok=True)
        stage = directory / stage_name
        if stage.exists():
            raise FileExistsError('批次暂存名已经存在，拒绝覆盖')
        ledger.set('stage', str(stage))
        t.request('output.configure', {'output_directory': str(directory), 'max_threads': self.workers, 'max_memory_mb': self.memory_mb}, stop=self.stop)
        begin = t.request('batch.begin', {'filename': stage_name, 'client_id': run, 'output_policy': 'omit_failed_regions'}, request_id=uuid.uuid5(uuid.UUID(run), f'begin:{attempt}').hex, stop=self.stop)['result']
        batch = begin['batch_id']
        lanes = max(1, min(self.workers, int(begin['threads'])))
        ledger.set('batch', batch)
        if begin.get('output_policy') != 'omit_failed_regions':
            raise ValueError('预演软件不支持局部问题输出与ERROR层，请使用配套新版本')
        ledger.set('issue_packages', 0)
        ledger.set('error_rectangles', 0)
        ledger.set('failed_packages', 0)
        # A second client could open another project after prepare() but before
        # batch.begin. Once the batch owns the service, project switching is
        # locked: verify that frozen identity again before submitting any copper.
        locked = t.request('info', stop=self.stop)['result']
        if (locked.get('batch') != batch or locked.get('batch_owner') != run
                or locked.get('session_identity') != ledger.get('session_identity')
                or Path(locked.get('project') or '').resolve() != Path(self.project)):
            raise ValueError('批次开始时工程身份或归属已变化，未提交任何封装；请确认正确工程后重试')
        ledger.set('state', 'running')
        with ledger.db:
            ledger.db.execute("update packages set state='pending',task=null,result=null")
        self.note(f'批次{attempt}：{ledger.get("count")}封装；请求并发{self.workers}，服务实际线程{begin["threads"]}')
        abort = threading.Event()
        error = None
        pending = {}
        cursor = ledger.db.execute('select seq,payload from packages order by seq')
        completed = 0
        self.total = ledger.get('count')
        self.completed_count = 0
        self.active_progress.clear()
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
                    with self.progress_lock:
                        self.active_progress.pop(seq, None)
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
                        self.completed_count = completed
                        self.progress(dict(text=f'批次{attempt} · 完成{completed}/{self.total} · 待回执{len(pending)} · 尚未最终提交', current=completed, total=self.total))
                        has_issues=bool(result.get('drc_count',0) or result.get('connection_failure_count',0))
                        with ledger.db:
                            ledger.db.execute("update packages set state=?,task=?,result=? where seq=?", ('computed_with_issues' if has_issues else 'computed', task, dumps(result), seq))
                        ledger.set('failed_packages',ledger.get('failed_packages',0)+result.get('failed_packages',0))
                        if has_issues:
                            ledger.set('issue_packages',ledger.get('issue_packages',0)+1)
                            ledger.set('error_rectangles',ledger.get('error_rectangles',0)+result['error_rectangles'])
                            self.note(f'封装{seq}保留正常部分：DRC {result.get("drc_count",0)}项，连接失败 {result.get("connection_failure_count",0)}项；ERROR矩形 {result["error_rectangles"]}个', 'WARNING')
                        if completed == 1 or completed % 20 == 0 or completed == ledger.get('count'):
                            self.note(f'计算完成 {completed}/{ledger.get("count")}；等待整批文件提交')
        if self.stop.is_set():
            raise InterruptedError('用户停止；保留输入与任务回执，继续时补算未提交批次')
        if error:
            raise error
        self.progress(dict(text='OAS整理 · 合并TopCell、写入校验尾部', current=0, total=100))
        reply = t.request('batch.end', {'batch_id': batch}, request_id=uuid.uuid5(uuid.UUID(run), f'end:{attempt}').hex, stop=self.stop)
        if isinstance(t, Transport):
            receipt = t.wait_task(reply['task_id'], self.stop, self.timeout, progress=lambda u: self.progress(dict(u, text='OAS写入 · ' + u['text'])))
        else:
            receipt = t.wait_task(reply['task_id'], self.stop, self.timeout)
        if receipt.get('cells') != ledger.get('count') or Path(receipt['path']).resolve() != stage:
            raise ValueError('OAS完成回执与请求数量或路径不符')
        if not isinstance(receipt.get('error_layer'),dict) or receipt['error_layer'].get('name') != 'ERROR':
            raise ValueError('OAS完成回执没有ERROR命名层')
        if receipt.get('error_packages',0) != ledger.get('issue_packages',0) or receipt.get('error_rectangles',0) != ledger.get('error_rectangles',0):
            raise ValueError('OAS的ERROR标记数量与逐封装回执不一致')
        self.validate_failure_receipt(receipt)
        from die_shift_studio.oas_validation import verify_crc
        self.progress(dict(text='OAS验证 · 流式CRC校验', current=0, total=1))
        if not verify_crc(stage, stop=self.stop):
            raise ValueError('实际OAS的CRC校验失败')
        self.progress(dict(text='OAS验证 · 流式SHA-256校验', current=0, total=1))
        digest = sha256(stage, self.stop)
        # Write-ahead commit record: recover a crash between rename and DB update.
        ledger.set('publish', dict(stage=str(stage), sha256=digest, receipt=receipt))
        self.publish()

    def validate_failure_receipt(self, receipt):
        failed=self.ledger.get('failed_packages')
        if failed is None:
            # Legacy pending commits must not publish a disconnected package
            # without FAIL after upgrade. This bounded-memory audit is only for
            # old journals; new runs maintain the count during computation.
            with closing(self.ledger.db.execute('select result from packages')) as cursor:
                for row in cursor:
                    if self.stop.is_set():raise InterruptedError('已停止核对旧任务')
                    try:result=json.loads(row[0])
                    except (TypeError,ValueError):raise ValueError('旧任务缺少完整FAIL回执，请新建任务；原文件已保留') from None
                    if not isinstance(result,dict) or result.get('connection_failure_count',0):
                        raise ValueError('旧断开任务没有FAIL约定，请新建任务；原文件已保留')
            failed=0
        if type(failed) is not int or failed<0 or not isinstance(receipt,dict):
            raise ValueError('FAIL任务记录无效；原文件已保留')
        if type(receipt.get('failed_packages',0)) is not int or receipt.get('failed_packages',0)!=failed:
            raise ValueError('OAS的FAIL封装数量与逐封装回执不一致')
        if failed and (not isinstance(receipt.get('failure_layer'),dict) or receipt['failure_layer'].get('name')!='FAIL'):
            raise ValueError('OAS完成回执缺少FAIL命名层')

    def publish(self):
        ledger = self.ledger
        record = ledger.get('publish')
        if not record:
            return False
        self.validate_failure_receipt(record.get('receipt'))
        if self.stop.is_set():
            raise InterruptedError('已停止发布；验证后的暂存文件保留供继续')
        self.progress(dict(text='最终提交 · 校验新旧文件与原子替换', current=0, total=1))
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
            if self.managed_output and self.output.exists():
                archive = self.cache / 'History' / ledger.get('run_id') / self.output.name
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    temporary = archive.with_suffix('.copying')
                    shutil.copyfile(self.output, temporary)
                    if sha256(temporary, self.stop) != previous:
                        raise ValueError('旧结果在归档期间发生变化，未覆盖目标')
                    os.replace(temporary, archive)
                if sha256(archive, self.stop) != previous:
                    raise ValueError('旧结果归档校验不符，未覆盖目标')
                record['archived_outputs'] = {str(self.output): dict(path=str(archive), sha256=previous)}
                ledger.set('publish', record)
            if (sha256(self.output, self.stop) if self.output.is_file() else None) != previous:
                raise ValueError('目标在提交期间被修改，未覆盖目标')
            os.replace(stage, self.output)
        else:
            raise ValueError('已完成批次文件缺失或校验不符，请保留日志检查磁盘')
        if self.managed_output:
            # Legacy partials and ancillary files are retained in Cache, never
            # deleted. Repeat safely after a crash between rename and receipt.
            archive = self.cache / 'History' / ledger.get('run_id') / 'OtherOutputFiles'
            for extra in self.output.parent.iterdir():
                if extra == self.output:
                    continue
                archive.mkdir(parents=True, exist_ok=True)
                destination = archive / extra.name
                if destination.exists():
                    destination = archive / (uuid.uuid4().hex + '_' + extra.name)
                os.replace(extra, destination)
                self.note(f'旧Output附属文件归档：{extra.name} → {destination}')
        with ledger.db:
            ledger.db.execute("update packages set state=case when state in ('computed_with_issues','committed_with_issues') then 'committed_with_issues' else 'committed' end")
        ledger.set('state', 'completed')
        self.note(f'完成：{ledger.get("count")}封装，{self.output}，SHA-256 {digest}')
        self.progress(dict(text=f'完成 · {ledger.get("count")}封装 · 含问题{ledger.get("issue_packages",0)}封装 · ERROR矩形{ledger.get("error_rectangles",0)}个 · FAIL {ledger.get("failed_packages",0)}封装 · OAS已提交', current=1, total=1))
        return True

    def run(self):
        if not self.database.is_file():
            raise FileNotFoundError('任务数据库不存在')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        locks = self.cache / 'Locks'
        locks.mkdir(parents=True, exist_ok=True)
        key = str(self.output.parent if self.managed_output else self.output)
        lock = locks / hashlib.sha256(os.path.normcase(key).encode()).hexdigest()
        task_root = next((parent.parent for parent in self.database.parents if parent.name == 'Tasks'), self.database.parent)
        task_locks = task_root / 'Cache' / 'Locks'
        task_locks.mkdir(parents=True, exist_ok=True)
        task_lock = task_locks / ('task-' + hashlib.sha256(os.path.normcase(str(self.database)).encode()).hexdigest())
        with Lease(task_lock), Lease(lock):
            self.ledger = Ledger(self.database)
            ledger = self.ledger
            try:
                if not ledger.get('count'):
                    raise ValueError('数据库没有完整导入的封装')
                if self.managed_output:
                    protected = [Path(self.project), self.database]
                    protected.extend(Path(item['path']) for item in ledger.get('sources', []))
                    try:
                        project_data = json.loads(Path(self.project).read_text(encoding='utf8'))
                        source = Path(project_data.get('gds_path', ''))
                        protected.append(source if source.is_absolute() else Path(self.project).parent / source)
                    except (OSError, ValueError):
                        pass  # Service validates the actual source before compute.
                    if any(path.resolve().is_relative_to(self.output.parent) for path in protected):
                        raise ValueError('输入或任务记录位于Output内；请移至项目输入目录后创建任务')
                config = dict(endpoint=self.endpoint, project=self.project, output=str(self.output))
                if ledger.get('config') and ledger.get('config') != config:
                    raise ValueError('继续任务须使用原入口、工程和输出路径')
                if not ledger.get('config'):
                    ledger.set('previous_output', sha256(self.output, self.stop) if self.output.is_file() else None)
                    # Config is the initialization commit marker; cancellation
                    # during the previous-file hash must leave this retryable.
                    ledger.set('config', config)
                if self.publish():
                    return {'state': 'completed', 'output': str(self.output), 'issue_packages': ledger.get('issue_packages',0), 'error_rectangles': ledger.get('error_rectangles',0), 'failed_packages': ledger.get('failed_packages',0)}
                for retry in range(self.retries + 1):
                    if self.stop.is_set():
                        raise InterruptedError('用户停止')
                    attempt = ledger.get('attempt', 0) + 1
                    ledger.set('attempt', attempt)
                    try:
                        self.prepare()
                        self.batch(attempt)
                        return {'state': 'completed', 'output': str(self.output), 'issue_packages': ledger.get('issue_packages',0), 'error_rectangles': ledger.get('error_rectangles',0), 'failed_packages': ledger.get('failed_packages',0)}
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
