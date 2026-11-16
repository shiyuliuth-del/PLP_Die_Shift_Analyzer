"""Real client/server processes, Excel->OAS and restart after a killed test server."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import json
import subprocess
import tempfile
import threading
from pathlib import Path
from dataclasses import asdict, replace
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import gdstk
from test_v38 import fixture
from RealDataAnalyse.excel import generate_example, import_files
from RealDataAnalyse.runner import Runner
from RealDataAnalyse.transport import Transport
from RealDataAnalyse.storage import Ledger


def project_fixture(folder):
    source = folder / 'source.gds'
    project = folder / 'case.dshift.json'
    engine, data, _ = fixture()
    roles = engine.roles
    p = replace(engine.p, single_bound_x_um=0, single_bound_y_um=0, bound_angle_deg=0)
    engine.close()
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    cell = lib.new_cell('TOP')
    for key, rows in data.layers.items():
        layer, datatype = map(int, key.split('/'))
        cell.add(*(gdstk.Polygon(xy, layer=layer, datatype=datatype) for xy in rows))
    lib.write_gds(source)
    project.write_text(json.dumps(dict(schema_version=5, gds_path=str(source), top_cell='TOP', roles=roles,
        layer_order=list(roles)[::-1], parameters=asdict(p), settings={'animation_enabled': False}, overlays={})), encoding='utf8')
    return project


def main():
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        project = project_fixture(folder)
        endpoint = folder / 'endpoint.json'
        t = Transport(endpoint, timeout=3)
        if os.environ.get('DIE_SHIFT_TEST_EXE'):
            server = subprocess.Popen([os.environ['DIE_SHIFT_TEST_EXE'], '--project', str(project), '--measured-output', '--ipc-endpoint', str(endpoint)])
            t.process = server
        else:
            t.launch(project)
            server = t.process
        try:
            info = t.wait_ready(threading.Event(), 180)
            assert info['session_identity'], info
            files = generate_example(folder, count=5, die_count=1, pitch=(1000, 1000))
            # Use nominal geometry for the transactional success path.
            from openpyxl import load_workbook
            book = load_workbook(files[0])
            for row in book.active.iter_rows(min_row=2, min_col=3, max_col=5):
                for c in row:
                    c.value = 0
            book.save(files[0])
            book.close()
            database = folder / 'run.sqlite'
            import_files(files, database, 1, 2, threading.Event())
            out = folder / 'lot.oas'
            kwargs = dict(database=str(database), endpoint=str(endpoint), project=str(project), output=str(out), workers=2, retries=2, timeout=120)
            if os.environ.get('REALDATA_TEST_EXE'):
                config = folder / 'run.json'
                config.write_text(json.dumps(kwargs), encoding='utf8')
                result = folder / 'result.json'
                subprocess.run([os.environ['REALDATA_TEST_EXE'], '--run', str(config), '--result', str(result)], check=True, timeout=300)
                assert json.loads(result.read_text(encoding='utf8'))['state'] == 'completed'
            else:
                Runner(**kwargs, transport=t).run()
            assert len(gdstk.read_oas(out).top_level()[0].references) == 5
            identity = t.request('info')['result']['session_identity']
            # A second imported journal simulates a new lot and loss of service.
            database2 = folder / 'restart.sqlite'
            import_files(files, database2, 1, 2, threading.Event())
            if not os.environ.get('REALDATA_TEST_EXE'):
                class CrashOnce(Transport):
                    crashed = False
                    submitted = 0
                    def request(self, command, *args, **kw):
                        reply = super().request(command, *args, **kw)
                        if command == 'batch.submit':
                            self.submitted += 1
                            if self.submitted == 2 and not self.crashed:
                                self.crashed = True
                                # Kill only this isolated test's server; production never kills.
                                server.kill()
                                server.wait(timeout=20)
                        return reply
                crash = CrashOnce(endpoint, timeout=2)
                try:
                    Runner(**dict(kwargs, database=str(database2), output=str(folder / 'restart.oas')), transport=crash).run()
                    assert crash.crashed and crash.process is not None
                    assert len(gdstk.read_oas(folder / 'restart.oas').top_level()[0].references) == 5
                    assert crash.request('info')['result']['session_identity'] == identity
                    l = Ledger(database2)
                    assert l.get('attempt') == 2 and l.counts() == {'committed': 5}
                    l.close()
                finally:
                    try:
                        crash.request('app.shutdown', retries=0)
                    except (OSError, EOFError, TimeoutError):
                        pass
                    if crash.process:
                        assert crash.process.wait(timeout=60) == 0
            else:
                t.request('app.shutdown')
                assert server.wait(timeout=60) == 0
                # Frozen client must find the sibling server, regardless of cwd.
                kwargs.update(database=str(database2), output=str(folder / 'restart.oas'))
                config.write_text(json.dumps(kwargs), encoding='utf8')
                subprocess.run([os.environ['REALDATA_TEST_EXE'], '--run', str(config), '--result', str(result)], cwd=folder, check=True, timeout=300)
                assert len(gdstk.read_oas(folder / 'restart.oas').top_level()[0].references) == 5
                t.request('app.shutdown')
        finally:
            try:
                t.request('app.shutdown', retries=0)
            except Exception:
                pass
            if server.poll() is None:
                assert server.wait(timeout=60) == 0
        print('GUI_V44_PROCESS_PASS: Excel join, real local IPC, all OAS placements, durable recovery, same session identity after relaunch')


if __name__ == '__main__':
    main()
