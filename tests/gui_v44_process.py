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


def frozen_run(command, result, folder, **kwargs):
    completed = subprocess.run(command, timeout=300, **kwargs)
    if completed.returncode:
        evidence = []
        for path in [result, *sorted((folder / 'logs').glob('*.log'))]:
            if path.is_file():
                evidence.append(path.name + '\n' + path.read_text(encoding='utf8', errors='replace')[-16000:])
        raise AssertionError(f'Frozen client exited {completed.returncode}\n' + '\n'.join(evidence))


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
            kwargs = dict(database=str(database), endpoint=str(endpoint), project=str(project), output=str(out),
                          workers=2, retries=2, timeout=120, create_cells=False)
            if os.environ.get('REALDATA_TEST_EXE'):
                config = folder / 'run.json'
                config.write_text(json.dumps(kwargs), encoding='utf8')
                result = folder / 'result.json'
                frozen_run([os.environ['REALDATA_TEST_EXE'], '--run', str(config), '--result', str(result)], result, folder)
                assert json.loads(result.read_text(encoding='utf8'))['state'] == 'completed'
            else:
                Runner(**kwargs, transport=t).run()
            layout = gdstk.read_oas(out)
            assert {cell.name for cell in layout.cells} == {'TopCell'}
            assert not layout.top_level()[0].references
            assert len({tuple(p.bounding_box()[0]) for p in layout.top_level()[0].polygons}) >= 5
            kwargs['create_cells'] = True
            identity = t.request('info')['result']['session_identity']
            if not os.environ.get('REALDATA_TEST_EXE'):
                # Abruptly exit the isolated client after a durable receipt,
                # leaving the running server and its owned batch untouched.
                interrupted = folder/'client-exit.sqlite'
                import_files(files,interrupted,1,1,threading.Event())
                interrupted_output = folder/'client-exit.oas'
                interrupted_output.write_bytes(b'previous file')
                recovery = dict(kwargs,database=str(interrupted),output=str(interrupted_output),workers=1)
                kill_client = """
import json, os, sys
from RealDataAnalyse.runner import Runner
def crash(update):
    if isinstance(update, dict) and update.get('current') and not update.get('published'):
        os._exit(91)
Runner(**json.loads(sys.argv[1]), progress=crash).run()
"""
                process = subprocess.run([sys.executable,'-c',kill_client,json.dumps(recovery)],cwd=ROOT,timeout=180)
                assert process.returncode == 91
                assert interrupted_output.read_bytes() == b'previous file'
                ledger = Ledger(interrupted)
                try:
                    saved_progress = ledger.get('display_progress')
                    assert saved_progress[0] > 0 and ledger.get('state') == 'running'
                finally:
                    ledger.close()
                marks = []
                Runner(**recovery,progress=marks.append).run()
                counters = [mark for mark in marks if isinstance(mark,dict)]
                assert counters[0]['current'] == saved_progress[0] and counters[-1]['published']
                assert len(gdstk.read_oas(interrupted_output).top_level()[0].references) == 5
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
                frozen_run([os.environ['REALDATA_TEST_EXE'], '--run', str(config), '--result', str(result)], result, folder, cwd=folder)
                assert len(gdstk.read_oas(folder / 'restart.oas').top_level()[0].references) == 5
                # app.shutdown acknowledges cancellation, not process exit.
                # Wait for both onefile processes before removing their EXEs/logs.
                import psutil
                descriptor = json.loads(endpoint.read_text(encoding='utf8'))
                child = psutil.Process(descriptor['pid'])
                processes = [child]
                parent = child.parent()
                if parent and Path(parent.exe()).resolve() == Path(os.environ['DIE_SHIFT_TEST_EXE']).resolve():
                    processes.append(parent)
                t.request('app.shutdown')
                _, alive = psutil.wait_procs(processes, timeout=60)
                assert not alive, 'Restarted frozen server has not safely exited'
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
