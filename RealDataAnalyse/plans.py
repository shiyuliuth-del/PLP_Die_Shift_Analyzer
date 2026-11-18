"""Durable ordered plans with immutable configuration, input snapshots and owned IPC."""
from contextlib import closing, nullcontext
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

from die_shift_studio.jobs import atomic_save
from .excel import import_files
from .instances import connect
from .projects import inside, template_file
from .runner import Runner
from .storage import Lease, sha256, configure_logging
from .transport import default_endpoint

TERMINAL = ('completed', 'error')


def capture(config, stop):
    """Capture data now; failed captures remain reviewable red rows."""
    snapshot = copy.deepcopy(config)
    snapshot['inputs'] = []
    try:
        root = Path(config['folder']).resolve()
        project = inside(root, config['project'])
        snapshot['engineering'] = json.loads(project.read_text(encoding='utf8'))
        source = inside(root, snapshot['engineering']['gds_path'])
        paths = [source, *(Path(p['path']).resolve() for p in config['files'] if p['enabled'])]
        template = template_file(root,config.get('template'))
        if template:
            snapshot['template'] = str(template)
            paths.append(template)
        for path in dict.fromkeys(paths):
            inside(root, path)
            snapshot['inputs'].append(dict(path=str(path), sha256=sha256(path, stop), size=path.stat().st_size))
        snapshot['source'] = str(source)
    except InterruptedError:
        raise
    except Exception as exc:
        snapshot['capture_error'] = str(exc)
    return snapshot


def validate(snapshot, output, check_hash=False, stop=None):
    if snapshot.get('capture_error'):
        raise ValueError(snapshot['capture_error'])
    root = Path(snapshot['folder']).resolve()
    if not root.is_dir():
        raise ValueError('项目文件夹不存在')
    inside(root, snapshot['project'])
    target = inside(root, output)
    if target.suffix.lower() != '.oas':
        raise ValueError('输出须为项目内的.oas文件')
    if target.exists() and not target.is_file():
        raise ValueError('输出位置不是普通文件')
    controls = snapshot['controls']
    if not 1 <= controls['dies'] <= 10000 or not 1 <= controls['workers'] <= 64 or not 128 <= controls['memory'] <= 1048576 or not 0 <= controls['retries'] <= 10 or not 0 <= controls['timeout'] <= 86400:
        raise ValueError('任务资源参数超出范围')
    selected = [p for p in snapshot['files'] if p['enabled']]
    if len(selected) != controls['dies'] or len({p['path'] for p in selected}) != len(selected):
        raise ValueError('所选Excel须与DIE数量一致，且不得重复')
    for item in selected:
        if Path(item['path']).suffix.lower() != '.xlsx':
            raise ValueError('当前适配器仅支持.xlsx')
        profile = snapshot['profile'] if snapshot.get('shared_mapping', True) else item.get('profile', snapshot['profile'])
        if not profile.get('sheet') or not isinstance(profile.get('columns'), dict) or type(profile.get('header_row')) is not int or profile['header_row'] < 1:
            raise ValueError('工作表、表头行或列映射缺失')
    if not snapshot.get('engineering') or not snapshot.get('inputs'):
        raise ValueError('没有完整的工程/输入快照')
    for item in snapshot['inputs']:
        path = inside(root, item['path'])
        if not path.is_file():
            raise ValueError(f'输入文件不存在：{path.name}')
        if check_hash and sha256(path, stop) != item['sha256']:
            raise ValueError(f'输入文件已改变：{path.name}；请重新创建计划')
    return target


class Plans:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            if tables and 'plan_meta' not in tables:
                raise ValueError('不是计划任务数据库，未修改文件')
            if tables:
                version = db.execute('select version from plan_meta').fetchone()
                if not version or version[0] != 1:
                    raise ValueError('不支持的计划任务数据库版本')
            db.execute('pragma journal_mode=WAL')
            db.execute('pragma synchronous=FULL')
            db.executescript('''
                create table if not exists plan_meta(version integer);
                create table if not exists plans(
                    seq integer primary key autoincrement,id text unique,folder text not null,snapshot text not null,
                    output text not null,output_key text not null,database_path text not null,
                    status text not null default 'pending',progress real not null default 0,
                    started real,ended real,message text not null default '',severity text not null default '',owned_instance text,owned_pid integer);
                create index if not exists plan_output on plans(output_key);
                create table if not exists plan_events(created real,id text,status text,message text);
            ''')
            if 'owned_birth' not in {r[1] for r in db.execute('pragma table_info(plans)')}:
                db.execute('alter table plans add column owned_birth real')
            version = db.execute('select version from plan_meta').fetchone()
            if version and version[0] != 1:
                raise ValueError('不支持的计划任务数据库版本')
            if not version:
                db.execute('insert into plan_meta values(1)')
            db.commit()

    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('pragma synchronous=FULL')
        return db

    def add(self, snapshot):
        plan_id = uuid.uuid4().hex
        root = Path(snapshot.get('folder') or self.path.parent).resolve()
        output = root/'Output'/f'{root.name}.oas'
        database = root/'Tasks'/'Plans'/plan_id/'run.sqlite'
        with Lease(str(self.path)+'.execution'):
            with closing(self.db()) as db, db:
                db.execute('insert into plans(id,folder,snapshot,output,output_key,database_path) values(?,?,?,?,?,?)',
                       (plan_id, str(root), json.dumps(snapshot, ensure_ascii=False, allow_nan=False), str(output), os.path.normcase(str(output)), str(database)))
            self._recheck()
        return plan_id

    def get(self, plan_id):
        with closing(self.db()) as db:
            row = db.execute('select * from plans where id=?', (plan_id,)).fetchone()
        if row is None:
            raise KeyError('计划任务不存在')
        result = dict(row); result['snapshot'] = json.loads(result['snapshot'])
        return result

    def rows(self, page=0):
        with closing(self.db()) as db:
            count = db.execute('select count(*) from plans').fetchone()[0]
            rows = [dict(row) for row in db.execute('select seq,id,folder,output,status,progress,started,ended,message,severity from plans order by seq limit 200 offset ?', (page*200,))]
        return count, rows

    def update(self, plan_id, **fields):
        allowed = {'status','progress','started','ended','message','severity','owned_instance','owned_pid','owned_birth'}
        if set(fields) - allowed:
            raise ValueError('未知计划字段')
        with closing(self.db()) as db, db:
            db.execute('update plans set '+','.join(k+'=?' for k in fields)+' where id=?', (*fields.values(), plan_id))
            if 'status' in fields:
                db.execute('insert into plan_events values(?,?,?,?)', (time.time(), plan_id, fields['status'], fields.get('message', '')))

    def remove(self, plan_id):
        with Lease(str(self.path)+'.execution'):
            if self.get(plan_id)['status'] == 'running':
                raise ValueError('不能删除运行中的计划')
            with closing(self.db()) as db, db:
                db.execute('delete from plans where id=?', (plan_id,))
            self._recheck()

    def check(self, plan_id):
        row = self.get(plan_id)
        if row['status'] in ('completed', 'running', 'error', 'paused'):
            return row['severity']
        severity = ''; message = '待执行；运行时校验Excel数据'
        try:
            target = validate(row['snapshot'], row['output'])
            with closing(self.db()) as db:
                conflict = db.execute('select seq from plans where output_key=? and id<>? limit 1', (os.path.normcase(str(target)), plan_id)).fetchone()
            if conflict:
                severity = 'warning'; message = f'与第{conflict[0]}行共用输出；顺序执行，旧结果归档Cache'
            elif target.exists() and not Path(row['database_path']).is_file():
                severity = 'warning'; message = '输出已存在；成功后旧结果归档Cache，再发布新结果'
        except Exception as exc:
            severity = 'error'; message = str(exc)
        self.update(plan_id, severity=severity, message=message, status='blocked' if severity == 'error' else 'pending')
        return severity

    def recheck(self, owned=False):
        with (nullcontext() if owned else Lease(str(self.path)+'.execution')):
            self._recheck()

    def _recheck(self):
        with closing(self.db()) as db:
            ids = [row[0] for row in db.execute("select id from plans where status in ('pending','blocked')")]
        for plan_id in ids:
            self.check(plan_id)

    def retry(self, plan_id):
        with Lease(str(self.path)+'.execution'):
            row = self.get(plan_id)
            if row['status'] not in ('error', 'paused'):
                raise ValueError('只可重试失败或暂停的计划')
            self.update(plan_id, status='pending', severity='', message='等待继续', ended=None)


def materialize(row, stop):
    snapshot = row['snapshot']
    root = Path(snapshot['folder']).resolve()
    workspace = inside(root, Path('Cache')/'Plans'/row['id'])
    workspace.mkdir(parents=True, exist_ok=True)
    source = next(item for item in snapshot['inputs'] if item['path'] == snapshot['source'])
    cache = inside(root, 'Cache/Inputs'); cache.mkdir(parents=True, exist_ok=True)
    cached = cache/(source['sha256']+Path(source['path']).suffix.lower())
    if not cached.exists():
        fd, name = tempfile.mkstemp(prefix='.input-', dir=cache)
        try:
            with os.fdopen(fd, 'wb') as out, open(source['path'], 'rb') as inp:
                while chunk := inp.read(1024*1024):
                    if stop.is_set(): raise InterruptedError('已停止输入快照')
                    out.write(chunk)
                out.flush(); os.fsync(out.fileno())
            if sha256(name, stop) != source['sha256']:
                raise ValueError('图纸在快照期间发生变化')
            try: os.link(name, cached)
            except FileExistsError: pass
        finally: Path(name).unlink(missing_ok=True)
    if sha256(cached, stop) != source['sha256']:
        raise ValueError('项目内图纸快照校验失败')
    local_source = workspace/('source'+Path(source['path']).suffix.lower())
    if not local_source.exists():
        try: os.link(cached, local_source)
        except OSError: shutil.copyfile(cached, local_source)
    if sha256(local_source, stop) != source['sha256']:
        raise ValueError('任务图纸快照被修改')
    project = workspace/'engineering.dshift.json'
    if not project.exists():
        data = copy.deepcopy(snapshot['engineering'])
        data['gds_path'] = local_source.name
        data.pop('saved_results', None)
        atomic_save(project, data)
    return project


class PlanRunner:
    def __init__(self, plans, stop=None, progress=lambda _: None, trace=None):
        self.plans = plans
        self.stop = stop if stop is not None else threading.Event()
        self.progress = progress
        self.trace = trace

    def run_one(self, row):
        snapshot = row['snapshot']; plan_id = row['id']; controls = snapshot['controls']
        project = None; transport = None; owned = row.get('owned_instance')
        owned_pid = row.get('owned_pid'); owned_birth = row.get('owned_birth')
        batch_owner = None
        last_update = [0.]
        last_number = [0.]
        def report(value, base=25, span=60):
            if isinstance(value, dict):
                text = value.get('text','')
                fraction = value.get('current',0)/max(1,value.get('total',1))
            else: text, fraction = str(value), None
            if text.startswith(('OAS', '最终提交')): base, span = 85, 14
            number = last_number[0] if fraction is None else min(99., base + span*max(0., min(1.,fraction)))
            last_number[0] = number
            now = time.monotonic()
            if now-last_update[0] >= .2:
                last_update[0] = now
                self.plans.update(plan_id, progress=number, message=text)
            self.progress(dict(text=f'计划第{row["seq"]}行 · {text}', current=number, total=100))
        self.plans.update(plan_id, status='running', started=time.time(), ended=None, severity='', message='校验计划快照')
        try:
            # A previous process may have published the OAS just before losing
            # the queue acknowledgement. Recover that receipt without recomputing.
            database = Path(row['database_path'])
            if database.is_file():
                with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
                    meta = {k: json.loads(v) for k,v in db.execute('select key,value from meta')}
                record = meta.get('publish')
                if record and Path(row['output']).is_file() and sha256(row['output'], self.stop) == record['sha256']:
                    return Runner(database, meta['config']['endpoint'], meta['config']['project'], row['output'], stop=self.stop, trace=self.trace,template=snapshot.get('template')).run()
            validate(snapshot, row['output'], check_hash=True, stop=self.stop)
            configure_logging(Path(snapshot['folder']))
            if self.trace: self.trace.set_root(snapshot['folder'])
            project = materialize(row, self.stop)
            if not database.exists():
                selected = [item for item in snapshot['files'] if item['enabled']]
                profiles = None if snapshot.get('shared_mapping',True) else {item['path']: item.get('profile',snapshot['profile']) for item in selected}
                import_files([item['path'] for item in selected], database, controls['dies'], controls['workers'], self.stop,
                             lambda message: report(message,0,15), snapshot['profile'], profiles)
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
                batch_owner = json.loads(db.execute("select value from meta where key='run_id'").fetchone()[0])
                sources = json.loads(db.execute("select value from meta where key='sources'").fetchone()[0])
            expected = {item['path']: item['sha256'] for item in snapshot['inputs']}
            if any(expected.get(item['path']) != item['sha256'] for item in sources):
                raise ValueError('Excel在计划校验与导入之间发生变化；请重新创建计划')
            endpoint = default_endpoint().parent/('endpoint-plan-'+plan_id+'.json')
            def launched_callback(value):
                nonlocal transport, owned_pid, owned_birth, owned
                import psutil
                owned = None
                transport = value; owned_pid = value.process.pid
                owned_birth = psutil.Process(owned_pid).create_time()
                self.plans.update(plan_id, owned_pid=owned_pid, owned_birth=owned_birth, owned_instance=None)
            transport, info, launched = connect(project, endpoint, self.stop, lambda message: report(message,15,10),
                                                 controls['timeout'], self.trace, exact=True, on_launch=launched_callback)
            if launched or transport.owns_descriptor(owned_pid, owned_birth):
                owned = info['instance_id']; self.plans.update(plan_id, owned_instance=owned)
            return Runner(database, endpoint, project, row['output'], controls['workers'], controls['memory'], controls['retries'],
                   controls['timeout'], self.stop, report, transport=transport, trace=self.trace,
                   template=snapshot.get('template'), expected_template_sha256=expected.get(snapshot.get('template'))).run()
        finally:
            if transport is not None and (owned or owned_pid):
                end = time.monotonic()+120
                info = None
                while time.monotonic() < end:
                    try:
                        info = transport.request('info', retries=0)['result']
                        break
                    except (OSError, EOFError, TimeoutError, json.JSONDecodeError):
                        if transport.process is not None and transport.process.poll() is not None:
                            break
                        if not transport.alive():break
                        time.sleep(.2)
                if info and (info.get('instance_id') == owned if owned else (owned_birth is not None and transport.owns_descriptor(owned_pid, owned_birth))):
                    if info.get('batch') and info.get('batch_owner') != batch_owner:
                        raise RuntimeError('实例有其他客户端批次，计划队列停止，未关闭实例')
                    if info.get('running') and not info.get('batch') and not info.get('preparing_task'):
                        raise RuntimeError('实例仍有归属未确认的任务，计划队列停止，未关闭实例')
                    transport.request('app.shutdown', retries=0)
                    while transport.alive() and time.monotonic()<end:time.sleep(.1)
                    if transport.process is not None and transport.process.poll() is None:
                        transport.process.wait(timeout=max(.1,end-time.monotonic()))
                    if transport.alive():raise RuntimeError('自动实例尚未安全退出，计划队列停止，未强杀进程')
                elif transport.alive():
                    raise RuntimeError('自动实例启动或归属未确认，计划队列停止，未发送关闭指令')

    def reconcile_archives(self, row):
        from .storage import Ledger
        with closing(Ledger(row['database_path'])) as ledger:
            archives = (ledger.get('publish') or {}).get('archived_outputs', {})
        if not archives:
            return
        with closing(self.plans.db()) as db:
            previous = [dict(item) for item in db.execute("select id,output,database_path from plans where status='completed' and id<>?", (row['id'],))]
        for item in previous:
            archived = archives.get(item['output'])
            if not archived or not Path(item['database_path']).is_file():
                continue
            with closing(Ledger(item['database_path'])) as ledger:
                digest = (ledger.get('publish') or {}).get('sha256')
            if digest == archived['sha256']:
                with closing(self.plans.db()) as db, db:
                    db.execute('update plans set output=?,message=? where id=?', (archived['path'], '历史结果已归档Cache；Output保留最新结果', item['id']))

    def run(self):
        totals = dict(completed=0, failed=0, skipped=0)
        with Lease(str(self.plans.path)+'.execution'):
            # This lock proves no previous queue executor remains alive.
            with closing(self.plans.db()) as db, db:
                db.execute("update plans set status='paused',severity='warning',message='上次执行中断，可继续' where status='running'")
                last_seq = db.execute('select coalesce(max(seq),0) from plans').fetchone()[0]
            self.plans.recheck(owned=True)
            seq = 0
            while not self.stop.is_set():
                with closing(self.plans.db()) as db:
                    item = db.execute('select id,seq from plans where seq>? and seq<=? order by seq limit 1', (seq,last_seq)).fetchone()
                if item is None: break
                plan_id, seq = item
                row = self.plans.get(plan_id)
                if row['status'] == 'completed': continue
                if row['status'] == 'error' or row['severity'] == 'error':
                    totals['skipped'] += 1; continue
                try:
                    summary=self.run_one(row) or {}
                    self.reconcile_archives(row)
                except InterruptedError as exc:
                    self.plans.update(plan_id, status='paused', severity='warning', ended=time.time(), message=str(exc))
                    break
                except Exception as exc:
                    self.plans.update(plan_id, status='error', severity='error', ended=time.time(), message=str(exc))
                    totals['failed'] += 1
                    self.progress(str(exc))
                    if '队列停止' in str(exc): break
                else:
                    self.plans.update(plan_id, status='completed', severity='', progress=100, ended=time.time(), message=f'OAS已校验并提交；含问题{summary.get("issue_packages",0)}封装，ERROR矩形{summary.get("error_rectangles",0)}个，FAIL {summary.get("failed_packages",0)}封装')
                    totals['completed'] += 1
        state = 'paused' if self.stop.is_set() else ('completed_with_errors' if totals['failed'] or totals['skipped'] else 'completed')
        return dict(state=state, **totals)
