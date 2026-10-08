"""Package the deliverable and verify its actual extracted contents locally."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from die_shift_studio import __version__


def main():
    revision=0
    name = f'Die_Shift_Analyze_{__version__}_Source'
    target = ROOT / 'dist' / (name + '.zip')
    target.parent.mkdir(exist_ok=True)
    files = [ROOT / p for p in ['README.md', 'main.py',
             'die_shift_model.py', 'requirements.txt', 'run.sh', 'RUN.bat', 'SELECT_PYTHON.bat', 'BUILD_WINDOWS.bat', 'TEST_WINDOWS.bat',
             'Die_Shift_Analyze.spec', 'DIE_SHIFT评估报告.md']]
    for folder in ['die_shift_studio', 'examples', 'tests']:
        files += [p for p in (ROOT / folder).rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.log'}]
    files += list((ROOT/'figures').glob(f'software_{__version__}_*.png'))
    for pattern in ('v040_*.png','v040_*.svg','v060_*.png','v060_*.svg','v070_*.png','v070_*.svg','V10_*.png','V10_*.svg','V11_*.png','V11_*.svg','V12_*.png','V12_*.svg','V13_*.png','V13_*.svg','V14_*.png','V14_*.svg','V15_*.png','V15_*.svg','V16_*.png','V16_*.svg','V17_*.png','V17_*.svg','0[1-5]_*.png','0[1-5]_*.svg'):
        files += list((ROOT/'figures').glob(pattern))
    files += list((ROOT/'results').glob('*_V17.json'))
    files += [ROOT/'results/performance_v040.json']
    files += [ROOT/'docs/V17_操作与验证说明.md']
    files += [ROOT/'tools'/p for p in ('create_examples.py','package_release.py','benchmark_preview.py','benchmark_v050.py','research_figures.py','v040_figures.py','v050_figures.py','v060_figures.py','klayout_probe_v060.py','create_fanout_example.py','v070_evidence.py','gui_v070_evidence.py','benchmark_v8.py','v8_figures.py','benchmark_v9.py','v9_figures.py','create_deformation_example.py','benchmark_v10.py','v10_figures.py','create_iteration_example.py','benchmark_v11.py','v11_figures.py','create_trace_example.py','benchmark_v12.py','v12_figures.py','benchmark_v13.py','benchmark_v14.py','v14_figures.py','create_pivot_example.py','validate_gd55_v14.py','benchmark_v15.py','v15_figures.py','validate_gd55_v15.py','validate_v16.py','v16_figures.py','benchmark_v17.py','v17_figures.py','create_directional_example.py')]
    # Ship only our portable sample jobs, never a user's saved projects.
    files += [ROOT/'Jobs'/(p.stem+'.dshift.json') for p in sorted((ROOT/'examples').glob('*.json'))]
    # Follow the actual Markdown references instead of relying on version globs.
    pending=[p for p in files if p.suffix=='.md'];seen=set()
    while pending:
        doc=pending.pop()
        if doc in seen:continue
        seen.add(doc)
        for link in re.findall(r'\]\(([^)]+)\)',doc.read_text(encoding='utf-8')):
            if link.startswith(('http:','https:','#')):continue
            dependency=(doc.parent/link).resolve()
            if dependency==ROOT/'docs/package_verification.json':continue
            if not dependency.is_relative_to(ROOT) or not dependency.is_file():
                raise ValueError(f'Missing or external report dependency: {doc}: {link}')
            files.append(dependency)
            if dependency.suffix=='.md':pending.append(dependency)
            if dependency.suffix=='.png' and dependency.with_suffix('.svg').is_file():files.append(dependency.with_suffix('.svg'))
    files=sorted(set(files))
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(files)}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for p in sorted(files):
            archive.write(p, f'{name}/{p.relative_to(ROOT)}')
        archive.writestr(name + '/SHA256SUMS.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    commands = [
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'],
        [sys.executable, 'tests/gui_smoke.py'],
        [sys.executable, 'tests/gui_v9.py'],
        [sys.executable, 'tests/gui_v10.py'],
        [sys.executable, 'tests/gui_v11.py'],
        [sys.executable, 'tests/gui_v12.py'],
        [sys.executable, 'tests/gui_v13.py'],
        [sys.executable, 'tests/gui_v14.py'],
        [sys.executable, 'tests/gui_v15.py'],
        [sys.executable, 'tests/gui_v16.py'],
        [sys.executable, 'tests/gui_v17.py'],
        [sys.executable, 'main.py', '--smoke-test', 'results/package_source_smoke.json'],
        [sys.executable, '-c', "import os,runpy,sys; os.environ['DIE_SHIFT_NATIVE_BACKEND']='desktop'; sys.argv=['main.py','--smoke-test','results/package_desktop_smoke.json']; runpy.run_path('main.py',run_name='__main__')"],
        [sys.executable, '-m', 'die_shift_studio', '--project', 'examples/示例C_多芯片晶圆接口.json',
         '--json', 'results/package_cli_check.json'],
    ]
    for sample in sorted((ROOT/'Jobs').glob('示例*.dshift.json')):
        commands.append([sys.executable,'-m','die_shift_studio','--project','Jobs/'+sample.name])
    attachment = next((p for p in [Path(os.environ.get('DIE_SHIFT_TEST_GDS', '')),
                                  Path.home()/'桌面/GD55_9-21.gds',
                                  Path.home()/'下载/GD55_9-21.gds'] if p.is_file()), None)
    if attachment is not None:
        commands.append([sys.executable, 'tools/validate_gd55_v14.py',
                         '--gds', str(attachment.resolve()), '--numerical-only'])
    if attachment is not None:
        commands.append([sys.executable,'tools/validate_gd55_v15.py','--gds',str(attachment.resolve()),'--detail-only'])
    records = []
    with tempfile.TemporaryDirectory(prefix='die_shift_release_') as temp:
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None
            archive.extractall(temp)
        extracted = Path(temp) / name
        for rel, expected in manifest.items():
            assert hashlib.sha256((extracted / rel).read_bytes()).hexdigest() == expected, rel
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        for command in commands:
            run = subprocess.run(command, cwd=extracted, env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            records.append({'command': command, 'returncode': run.returncode, 'output': run.stdout})
            print(run.stdout, end='', flush=True)
            if run.returncode:
                raise SystemExit('Extracted archive validation failed: '+str(command)+' returncode='+str(run.returncode))
    record = {'version': __version__, 'package_revision':revision,'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'method': 'Source-only ZIP; fresh extraction, file hashes, core tests, ten Qt suites and actual installed KLayout desktop backend, source startup and CLI; no frozen build',
              'environment': 'Existing Linux Python environment; not a clean dependency-install or Windows test',
              'file_count_before_record': len(manifest), 'status': 'PASS', 'runs': records}
    record_text = json.dumps(record, ensure_ascii=False, indent=2)
    (ROOT / 'docs' / 'package_verification.json').write_text(record_text, encoding='utf-8')
    # Only add the verification record; the verified implementation stays byte-identical.
    with zipfile.ZipFile(target, 'a', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name + '/docs/package_verification.json', record_text)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for rel, expected in manifest.items():
            assert hashlib.sha256(archive.read(name + '/' + rel)).hexdigest() == expected
        import posixpath
        for rel in manifest:
            if not rel.endswith('.md'):continue
            for link in re.findall(r'\]\(([^)]+)\)',archive.read(name+'/'+rel).decode('utf-8')):
                if link.startswith(('http:','https:','#')):continue
                dependency=posixpath.normpath(posixpath.join(name,posixpath.dirname(rel),link))
                assert dependency in archive.namelist(),dependency
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n')
    print(f'PACKAGE_PASS: {target} ({target.stat().st_size:,} bytes)\nSHA256 {digest}')


if __name__ == '__main__':
    main()
