"""SH22 production-size import and real IPC/output on representative poses."""
import csv
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gdstk
import psutil
from die_shift_studio.local_ipc import call
from die_shift_studio.oas_validation import verify_crc
from RealDataAnalyse.projects import load_folder
from RealDataAnalyse.storage import sha256
from RealDataAnalyse.transport import Transport


def run_case(root):
    folder = root / 'SH22'
    shutil.copytree(Path(os.environ.get('SH22_TEST_CASE', ROOT/'Jobs/SH22')), folder)
    config = load_folder(folder)
    endpoint = root / 'endpoint.json'
    result = root / 'result.json'
    command = ([os.environ['REALDATA_TEST_EXE']] if os.environ.get('REALDATA_TEST_EXE')
               else [sys.executable, str(ROOT/'RealDataAnalyse/main.py')])
    paths = [Path(row['path']) for row in config['files']]
    input_hash = {p: sha256(p) for p in paths}
    database = Path(config['database'])
    import_args = dict(paths=list(map(str, paths)), database=str(database), die_count=2, workers=0,
                       measurement=config['measurement'], project=config['project'], endpoint=str(endpoint))

    def client(mode, arguments):
        request = root / 'config.json'
        request.write_text(json.dumps(arguments), encoding='utf8')
        completed = subprocess.run(command+[mode, str(request), '--result', str(result)], timeout=300)
        response = json.loads(result.read_text(encoding='utf8'))
        assert completed.returncode == 0, response
        return response

    try:
        start = time.monotonic()
        imported = client('--import-data', import_args)
        assert imported['packages'] == 400 and imported['skipped_packages'] == 0, imported
        assert imported['die_names'] == ['101', '102']
        print(f'SH22_FULL_IMPORT_PASS: 400 packages / 800 rows in {time.monotonic()-start:.2f}s', flush=True)

        # Two sites fit the bypass. One DIE at the exact X limit in site 0:2
        # leaves that entire package on the normal reconstruction path.
        keys = {(0, 0), (0, 2), (0, 3)}
        selected = []
        for path in paths:
            dest = path.with_name('validation.csv')
            if dest in selected:
                continue
            selected.append(dest)
            with path.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.reader(f)
                heading = next(reader)
                rows = []
                for row in reader:
                    if tuple(map(int, row[:2])) in keys and float(row[3]) > 0:
                        rows.append(row)
                assert len(rows) == len(keys)
            with dest.open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(heading)
                writer.writerows(rows)
        sample = database.with_name('sample.sqlite')
        imported = client('--import-data', dict(import_args, database=str(sample), paths=list(map(str, selected))))
        assert imported['packages'] == 3
        info = call(endpoint, 'info')['result']
        output = Path(config['output'])
        published = client('--run', dict(database=str(sample), project=config['project'], endpoint=str(endpoint),
                                         output=str(output), workers=0, memory_mb=0, retries=0,
                                         bypass_limits=[2.22209,2,.2]))
        assert published['state'] == 'completed' and verify_crc(output)
        assert list(output.parent.iterdir()) == [output]
        lib = gdstk.read_oas(output)
        dbu = lib.precision / lib.unit
        top, = lib.top_level()
        nominal_refs = [ref for ref in top.references if ref.cell_name == 'G_NOMINAL']
        refs = {ref.cell_name: ref for ref in top.references if ref.cell_name != 'G_NOMINAL'}
        assert len(nominal_refs) == 2 and len(refs) == 1
        assert nominal_refs[0].cell is nominal_refs[1].cell
        source = gdstk.read_gds(folder/'sh22.gds')
        original = source.top_level()[0].get_polygons(layer=128,datatype=0)
        nominal = nominal_refs[0].cell.get_polygons(layer=128,datatype=0)
        assert len(original) == len(nominal) == 2
        assert not gdstk.boolean(original,nominal,'xor',precision=dbu)
        with closing(sqlite3.connect(sample)) as db:
            for identity, payload, result in db.execute('select package,payload,result from packages'):
                package = json.loads(payload)
                receipt = json.loads(result)
                should_bypass = identity.split(':')[1] != '2'
                assert receipt.get('use_nominal',False) is should_bypass
                assert receipt['fixed_rdl_drc_checked'] is not should_bypass
                expected = [p-b for p,b in zip(package['position_um'], info['board_bounds_um'])]
                candidates = nominal_refs if should_bypass else [refs[package['cell']]]
                assert sum(all(abs(a-b)<=dbu for a,b in zip(ref.origin,expected)) for ref in candidates)==1
            actual_centers = {}
            for package, die, payload in db.execute('select package,die,payload from measurements'):
                actual_centers.setdefault(package, {})[die] = json.loads(payload)['actual_center_um']
            for package_id, payload in db.execute('select package,payload from packages'):
                key = json.loads(payload)['cell']
                if key not in refs:
                    continue
                cell = refs[key]
                outlines = cell.get_polygons(layer=128, datatype=0)
                expected = list(actual_centers[package_id].values())
                import shapely
                actual = [list(shapely.Polygon(outline.points).centroid.coords[0]) for outline in outlines]
                assert len(actual) == len(expected) == 2
                for center in expected:
                    assert any(max(abs(a-b) for a,b in zip(center, point)) < 0.002 for point in actual), (expected, actual)
        assert all(sha256(path) == digest for path,digest in input_hash.items())
    finally:
        if endpoint.exists():
            transport = Transport(endpoint)
            if transport.alive():
                descriptor = json.loads(endpoint.read_text(encoding='utf8'))
                child = psutil.Process(descriptor['pid'])
                processes = [child]
                parent = child.parent()
                frozen_server = os.environ.get('DIE_SHIFT_TEST_EXE')
                if frozen_server and parent and Path(parent.exe()).resolve() == Path(frozen_server).resolve():
                    processes.append(parent)
                transport.request('app.shutdown', retries=0)
                _, alive = psutil.wait_procs(processes, timeout=60)
                assert not alive, '配套预演服务及冻结启动进程须完整退出'
    print('SH22_POSITIONED_OAS_PASS: real measured DIE centers, varied poses, CRC, isolated Output and safe exit', flush=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='sh22-measured-') as temp:
        run_case(Path(temp).resolve())
