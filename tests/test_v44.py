"""Independent input, recovery, ownership and transactional output contracts."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from RealDataAnalyse.excel import generate_example, import_files, DEFAULT_PROFILE
from RealDataAnalyse.storage import Ledger, Lease, sha256
from RealDataAnalyse.runner import Runner
from RealDataAnalyse.transport import Transport, RemoteError


class FakeService:
    def __init__(self, root, fail=None):
        self.root = root
        self.fail = fail
        self.batch_id = None
        self.owner = None
        self.begin_count = 0
        self.launched = 0
        self.alive_value = True
        self.rows = {}
        self.tasks = {}
        self.seen = {}
        self.lock = threading.RLock()
        self.identity = 'frozen-engineering-identity'

    def alive(self):
        return self.alive_value

    def launch(self, project):
        self.launched += 1
        self.alive_value = True

    def wait_ready(self, stop, timeout):
        return self.request('info')['result']

    def wait_task(self, task_id, stop, timeout):
        value = self.tasks[task_id]
        if isinstance(value, Exception):
            raise value
        return value

    def request(self, command, args=None, **kwargs):
        args = args or {}
        with self.lock:
            if not self.alive_value:
                raise ConnectionError('isolated server crashed')
            if command == 'info':
                return dict(result=dict(ready=True, project=str(self.root / 'case.json'), source=str(self.root / 'source.gds'),
                    session_identity=self.identity, die_count=2, batch=self.batch_id, batch_owner=self.owner, running=[]))
            if command == 'output.configure':
                self.output = Path(args['output_directory'])
                return dict(status='completed')
            if command == 'compute.cancel':
                self.batch_id = None
                return dict(status='completed')
            if command == 'batch.begin':
                self.begin_count += 1
                self.rows = {}
                self.batch_id = str(self.begin_count)
                self.owner = args['client_id']
                self.filename = args['filename']
                return dict(result=dict(batch_id=self.batch_id, threads=2))
            if command == 'batch.submit':
                if self.fail == 'crash' and self.begin_count == 1 and len(self.rows) >= 1:
                    self.alive_value = False
                    self.batch_id = None
                    raise ConnectionError('isolated server crashed')
                cell = args['cell']
                if cell in self.rows:
                    raise AssertionError('duplicate cell')
                self.rows[cell] = args
                task = f'{self.batch_id}:{cell}'
                result = dict(cell=cell, connection_failure_count=0, fixed_rdl_drc_checked=False)
                if self.fail == 'geometry':
                    result = RemoteError(dict(status='error', error=dict(type='ValueError', message='未连接动区')))
                self.tasks[task] = result
                return dict(status='accepted', task_id=task)
            if command == 'batch.end':
                import shapely
                from die_shift_studio.oas_stream import Store
                store = Store(self.output, .001, 1024**2)
                try:
                    for cell, row in self.rows.items():
                        store.add(cell, {'1/0': [shapely.box(0, 0, 10, 10).wkb]}, row['position_um'], (0, 0))
                    receipt = store.write(self.output / self.filename)
                finally:
                    store.close()
                self.batch_id = None
                self.tasks['end'] = receipt
                return dict(status='accepted', task_id='end')
            raise AssertionError(command)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = generate_example(self.root, count=7, die_count=2)
        self.db = self.root / 'run.sqlite'
        self.stop = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def read(self):
        return import_files(self.files, self.db, 2, 4, self.stop)

    def edit(self, file, action):
        from openpyxl import load_workbook
        book = load_workbook(file)
        action(book.active)
        book.save(file)
        book.close()

    def runner(self, fake=None, **kwargs):
        return Runner(self.db, self.root / 'ipc.json', self.root / 'case.json', self.root / 'out.oas',
                      workers=3, retries=1, timeout=10, transport=fake or FakeService(self.root), **kwargs)

    def test_join_sorted_ids_zero_and_centers(self):
        self.edit(self.files[0], lambda s: setattr(s['H2'], 'value', 123.5))
        self.edit(self.files[0], lambda s: setattr(s['I2'], 'value', -42))
        self.assertEqual(self.read()['packages'], 7)
        l = Ledger(self.db)
        row = json.loads(l.db.execute('select payload from packages where seq=1').fetchone()[0])
        self.assertEqual(row['dies'][0]['rotation_center_um'], [123.5, -42])
        self.assertEqual(row['dies'][0]['dx_um'], 0)
        self.assertNotIn('rotation_center_um', row['dies'][1])
        l.close()

    def test_missing_die_does_not_publish_partial_import(self):
        self.edit(self.files[0], lambda s: s.delete_rows(4))
        with self.assertRaisesRegex(ValueError, '缺少部分DIE'):
            self.read()
        self.assertFalse(self.db.exists())

    def test_import_closes_all_sqlite_handles_before_publication(self):
        import sqlite3
        connections = []
        original = sqlite3.connect
        class Tracked(sqlite3.Connection):
            closed = False
            def close(self):
                self.closed = True
                super().close()
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs, factory=Tracked)
            connections.append(connection)
            return connection
        with patch('sqlite3.connect', connect):
            self.read()
        self.assertTrue(connections)
        self.assertTrue(all(connection.closed for connection in connections))

    def test_duplicate_rejected(self):
        self.edit(self.files[0], lambda s: setattr(s['A3'], 'value', s['A2'].value))
        with self.assertRaisesRegex(ValueError, '重复封装'):
            self.read()

    def test_conflicting_positions_rejected(self):
        self.edit(self.files[1], lambda s: setattr(s['F3'], 'value', 99))
        with self.assertRaisesRegex(ValueError, '坐标冲突'):
            self.read()

    def test_blank_is_not_zero(self):
        self.edit(self.files[0], lambda s: setattr(s['C3'], 'value', None))
        with self.assertRaisesRegex(ValueError, '空白不当作0'):
            self.read()

    def test_formulas_rejected(self):
        self.edit(self.files[0], lambda s: setattr(s['C3'], 'value', '=1+1'))
        with self.assertRaisesRegex(ValueError, '公式'):
            self.read()

    def test_wrong_die_and_partial_center_rejected(self):
        self.edit(self.files[0], lambda s: setattr(s['H2'], 'value', 2))
        with self.assertRaises(ValueError):
            self.read()

    def test_custom_header_mapping(self):
        for f in self.files:
            self.edit(f, lambda s: setattr(s['A1'], 'value', '封装编号'))
        profile = json.loads(json.dumps(DEFAULT_PROFILE))
        profile['columns']['package_id'] = '封装编号'
        self.assertEqual(import_files(self.files, self.db, 2, 2, self.stop, profile=profile)['packages'], 7)

    def test_real_oas_all_packages_exact_placements(self):
        import gdstk
        self.read()
        self.runner().run()
        top = gdstk.read_oas(self.root / 'out.oas').top_level()[0]
        self.assertEqual(len(top.references), 7)
        self.assertEqual(set(tuple(r.origin) for r in top.references), {(i % 6 * 12000, i // 6 * 12000) for i in range(7)})
        l = Ledger(self.db)
        self.assertEqual(l.counts(), {'committed': 7})
        self.assertEqual(l.get('state'), 'completed')
        l.close()

    def test_crash_restart_replays_every_package(self):
        self.read()
        fake = FakeService(self.root, fail='crash')
        self.runner(fake).run()
        self.assertEqual(fake.launched, 1)
        self.assertEqual(fake.begin_count, 2)
        self.assertEqual(len(fake.rows), 7)

    def test_client_respects_service_allocated_lanes(self):
        import time
        self.read()
        class OneLane(FakeService):
            active = 0
            peak = 0
            def request(self, command, *args, **kwargs):
                response = super().request(command, *args, **kwargs)
                if command == 'batch.begin':
                    response['result']['threads'] = 1
                return response
            def wait_task(self, task, *args):
                with self.lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                try:
                    time.sleep(.02)
                    return super().wait_task(task, *args)
                finally:
                    with self.lock:
                        self.active -= 1
        fake = OneLane(self.root)
        self.runner(fake).run()
        self.assertEqual(fake.peak, 1)

    def test_geometry_failure_preserves_old_output(self):
        self.read()
        out = self.root / 'out.oas'
        out.write_bytes(b'old verified output')
        with self.assertRaises(RemoteError):
            self.runner(FakeService(self.root, fail='geometry')).run()
        self.assertEqual(out.read_bytes(), b'old verified output')
        l = Ledger(self.db)
        self.assertEqual(l.get('state'), 'blocked')
        self.assertGreater(l.counts().get('error', 0), 0)
        l.close()

    def test_failed_batch_releases_database_even_with_retained_traceback(self):
        import psutil
        self.read()
        held = []
        try:
            self.runner(FakeService(self.root, fail='geometry')).run()
        except RemoteError as exc:
            held.append(exc)
        self.assertTrue(held)
        self.assertNotIn(str(self.db), {f.path for f in psutil.Process().open_files()})

    def test_other_client_not_cancelled(self):
        self.read()
        fake = FakeService(self.root)
        fake.batch_id, fake.owner = 'others', 'different-owner'
        with self.assertRaisesRegex(ValueError, '其他客户端'):
            self.runner(fake).run()
        self.assertEqual(fake.batch_id, 'others')

    def test_saved_uncommitted_batch_is_replayed(self):
        self.read()
        fake = FakeService(self.root)
        l = Ledger(self.db)
        fake.batch_id, fake.owner = 'old', l.get('run_id')
        l.close()
        self.runner(fake).run()
        self.assertEqual(len(fake.rows), 7)

    def test_identity_change_blocks_resume(self):
        self.read()
        l = Ledger(self.db)
        l.set('session_identity', 'previous-geometry')
        l.close()
        with self.assertRaisesRegex(ValueError, '工程几何'):
            self.runner().run()

    def test_rename_before_db_commit_recovers(self):
        self.read()
        self.runner().run()
        l = Ledger(self.db)
        l.set('state', 'running')
        with l.db:
            l.db.execute("update packages set state='computed'")
        l.close()
        fake = FakeService(self.root)
        self.runner(fake).run()
        self.assertEqual(fake.begin_count, 0)

    def test_external_output_change_not_overwritten(self):
        self.read()
        self.runner().run()
        (self.root / 'out.oas').write_bytes(b'external change')
        with self.assertRaisesRegex(ValueError, '文件缺失或校验不符'):
            self.runner().run()
        self.assertEqual((self.root / 'out.oas').read_bytes(), b'external change')

    def test_stop_during_final_verification_keeps_old_output(self):
        self.read()
        out = self.root / 'out.oas'
        out.write_bytes(b'previous output')
        runner = self.runner(stop=self.stop)
        from die_shift_studio.oas_validation import verify_crc
        def stop_after_crc(path, **kwargs):
            good = verify_crc(path)
            self.stop.set()
            return good
        with patch('die_shift_studio.oas_validation.verify_crc', stop_after_crc):
            with self.assertRaises(InterruptedError):
                runner.run()
        self.assertEqual(out.read_bytes(), b'previous output')

    def test_lease_prevents_double_run(self):
        with Lease(self.db):
            with self.assertRaises(RuntimeError):
                Lease(self.db)

    def test_unrelated_sqlite_is_not_modified(self):
        import sqlite3
        db = sqlite3.connect(self.db)
        db.execute('create table unrelated(value text)')
        db.commit()
        db.close()
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, '任务数据库'):
            Ledger(self.db)
        self.assertEqual(self.db.read_bytes(), before)

    def test_retry_uses_identical_operation_id(self):
        t = Transport(self.root / 'endpoint')
        ids = []
        def rpc(command, args, key):
            ids.append(key)
            if len(ids) == 1:
                raise TimeoutError('lost response after acceptance')
            return dict(status='completed')
        with patch.object(t, 'rpc', rpc):
            t.request('batch.begin', {}, retries=1)
        self.assertEqual(ids[0], ids[1])

    def test_busy_retries_without_geometry_failure(self):
        t = Transport(self.root / 'endpoint')
        calls = []
        def rpc(*args):
            calls.append(args)
            return dict(status='error', error=dict(type='Busy')) if len(calls) == 1 else dict(status='accepted', task_id='a')
        with patch.object(t, 'rpc', rpc):
            self.assertEqual(t.request('batch.submit', {}, retries=1)['task_id'], 'a')
        self.assertEqual(len(calls), 2)

    def test_deadline_also_bounds_authentication(self):
        import time
        p = self.root / 'endpoint'
        p.write_text('{}')
        t = Transport(p, timeout=.01, capacity=1)
        with patch('RealDataAnalyse.transport.call', side_effect=lambda *a: time.sleep(1)):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                t.rpc('ping')
            self.assertLess(time.monotonic() - start, .9)
            with self.assertRaises(TimeoutError):
                t.rpc('ping')


if __name__ == '__main__':
    unittest.main()
