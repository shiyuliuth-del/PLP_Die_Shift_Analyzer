"""Bundled GD55: the actual client imports XLSX and drives its sibling server."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from die_shift_studio.local_ipc import call
from die_shift_studio.oas_validation import verify_crc
import gdstk
import psutil


def main():
    with tempfile.TemporaryDirectory(prefix='rda-gd55-') as temp:
        folder = Path(temp)
        case = folder / 'GD55 inputs'
        source_case = Path(os.environ.get('REALDATA_TEST_CASE', str(ROOT / 'cases/GD55')))
        shutil.copytree(source_case, case)
        source = case / 'GD55_1105.gds'
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        project = case / 'GD55.dshift.json'
        endpoint = folder / 'endpoint.json'
        database = folder / 'measurements.sqlite'
        result_path = folder / 'result.json'
        import_config = folder / 'import.json'
        import_config.write_text(json.dumps(dict(paths=[str(p) for p in sorted(case.glob('*.xlsx'))],
            database=str(database), die_count=3, workers=4)), encoding='utf8')
        command = [os.environ['REALDATA_TEST_EXE']] if os.environ.get('REALDATA_TEST_EXE') else [sys.executable, str(ROOT/'RealDataAnalyse/main.py')]
        imported = subprocess.run(command + ['--import-data', str(import_config), '--result', str(result_path)], cwd=folder, timeout=120)
        result = json.loads(result_path.read_text(encoding='utf8'))
        assert imported.returncode == 0 and result.get('packages') == 12 and result.get('dies') == 3, result
        run_config = folder / 'run.json'
        output = folder / 'GD55_lot.oas'
        run_config.write_text(json.dumps(dict(database=str(database), endpoint=str(endpoint), project=str(project),
            output=str(output), workers=4, memory_mb=2048, retries=2, timeout=900)), encoding='utf8')
        processes = []
        try:
            completed = subprocess.run(command + ['--run', str(run_config), '--result', str(result_path)], cwd=folder, timeout=1200)
            result = json.loads(result_path.read_text(encoding='utf8'))
            assert completed.returncode == 0 and result['state'] == 'completed', result
            descriptor = json.loads(endpoint.read_text(encoding='utf8'))
            server = psutil.Process(descriptor['pid'])
            processes = [server]
            parent = server.parent()
            if os.environ.get('DIE_SHIFT_TEST_EXE') and parent and Path(parent.exe()).resolve() == Path(os.environ['DIE_SHIFT_TEST_EXE']).resolve():
                processes.append(parent)
            info = call(endpoint, 'info')['result']
            assert info['ready'] and info['die_count'] == 3
            assert verify_crc(output)
            layout = gdstk.read_oas(output)
            top = layout.top_level()[0]
            assert top.name == 'TopCell' and len(top.references) == 12
            for i, ref in enumerate(sorted(top.references, key=lambda r: r.cell_name)):
                expected = ((i % 4) * 12000 - info['board_bounds_um'][0], (i // 4) * 12000 - info['board_bounds_um'][1])
                assert tuple(ref.origin) == expected, (ref.cell_name, ref.origin, expected)
                assert ref.cell.get_polygons()
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            print('GD55_ACTUAL_CLIENT_PASS: imported all three bundled XLSX in the client process, 12 packages, OAS CRC/independent read/placements, unchanged source', flush=True)
        finally:
            if endpoint.exists():
                if not processes:
                    descriptor = json.loads(endpoint.read_text(encoding='utf8'))
                    try:
                        processes = [psutil.Process(descriptor['pid'])]
                    except psutil.NoSuchProcess:
                        pass
                call(endpoint, 'app.shutdown')
                until = time.monotonic() + 90
                while time.monotonic() < until:
                    alive = []
                    for process in processes:
                        try:
                            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                                alive.append(process)
                        except psutil.NoSuchProcess:
                            pass
                    if not alive:
                        break
                    time.sleep(.1)
                else:
                    raise AssertionError('GD55 server did not safely exit')


if __name__ == '__main__':
    main()
