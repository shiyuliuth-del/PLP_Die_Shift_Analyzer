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
import stat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from die_shift_studio import __version__


def main():
    revision=0
    name = f'Die_Shift_Analyze_{__version__}_Source'
    target = ROOT / 'dist' / (name + '.zip')
    target.parent.mkdir(exist_ok=True)
    files = [ROOT / p for p in ['README.md','main.py','die_shift_model.py','requirements.txt','run.sh','RUN.bat','SELECT_PYTHON.bat','BUILD_WINDOWS.bat','TEST_WINDOWS.bat','Die_Shift_Analyze.spec',f'docs/{__version__}_使用说明.md','AGENTS.md','Agent.md','CONTRIBUTING.md','.gitignore','.gitattributes','.github/pull_request_template.md','docs/DESIGN_REQUIREMENTS.md','docs/Agents.md','results/width_V23.json','docs/BASELINE_1102.md','docs/KNOWN_ISSUES.md','docs/imports/1102_source_manifest.json','results/1102_edge_audit.json']]
    # Explicit source/data whitelist. No images, screenshots, old archives,
    # generated cache, user jobs or large historical report assets.
    for folder in ('die_shift_studio','examples','tests','tools'):
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix.lower() in {'.py','.json','.gds','.lym'} and '__pycache__' not in p.parts and p.name!='revision_figures.py']
    files += [ROOT/'Jobs'/(p.stem+'.dshift.json') for p in sorted((ROOT/'examples').glob('*.json'))]
    files += [ROOT/'results/baseline_interfaces_V19.json']
    files=sorted(set(files))
    image_suffixes={'.png','.jpg','.jpeg','.gif','.webp','.bmp','.tif','.tiff','.svg','.ico','.pdf'}
    assert not any(p.suffix.lower() in image_suffixes for p in files)
    manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(files)}
    def entry(path,directory=False):
        info=zipfile.ZipInfo(path,datetime.now().timetuple()[:6]);info.create_system=3
        info.external_attr=((stat.S_IFDIR|0o755)<<16)|0x10 if directory else ((stat.S_IFREG|0o644)<<16)|0x20
        info.compress_type=zipfile.ZIP_STORED;return info
    with zipfile.ZipFile(target,'w',zipfile.ZIP_STORED,allowZip64=False) as archive:
        folders={name+'/'}
        for p in files:
            parent=Path(name)/p.relative_to(ROOT).parent
            while str(parent)!='.':folders.add(parent.as_posix()+'/');parent=parent.parent
        for folder in sorted(folders):archive.writestr(entry(folder,True),b'')
        for p in files:archive.writestr(entry(f'{name}/{p.relative_to(ROOT)}'),p.read_bytes())
        archive.writestr(entry(name+'/SHA256SUMS.json'),json.dumps(manifest,ensure_ascii=False,indent=2))
    commands = [
        [sys.executable,'tools/check_contribution.py'],
        [sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py','-v'],
        [sys.executable,'tests/gui_smoke.py'],
        [sys.executable,'tests/gui_v14.py'],
        [sys.executable,'tests/gui_v20.py'],
        [sys.executable,'tests/gui_scheduler_v20.py'],
        [sys.executable,'tests/gui_v21.py'],
        [sys.executable,'tests/gui_repair.py'],
        [sys.executable,'main.py','--smoke-test','results/package_source_smoke.json'],
        [sys.executable,'-c',"import os,runpy,sys; os.environ['DIE_SHIFT_NATIVE_BACKEND']='desktop'; sys.argv=['main.py','--smoke-test','results/package_desktop_smoke.json']; runpy.run_path('main.py',run_name='__main__')"],
    ]
    for sample in sorted((ROOT/'Jobs').glob('示例*.dshift.json')):
        commands.append([sys.executable,'-m','die_shift_studio','--project','Jobs/'+sample.name])
    records = []
    with tempfile.TemporaryDirectory(prefix='die_shift_release_') as temp:
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None
            archive.extractall(temp)
        extracted = Path(temp) / name
        for rel, expected in manifest.items():
            assert hashlib.sha256((extracted / rel).read_bytes()).hexdigest() == expected, rel
        (extracted/'figures').mkdir(exist_ok=True);(extracted/'results').mkdir(exist_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', OPENBLAS_NUM_THREADS='1',PYTHONUTF8='1')
        for command in commands:
            run = subprocess.run(command, cwd=extracted, env=env, text=True,encoding='utf-8',errors='replace',
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            records.append({'command': command, 'returncode': run.returncode, 'output': run.stdout})
            print(run.stdout, end='', flush=True)
            if run.returncode:
                raise SystemExit('Extracted archive validation failed: '+str(command)+' returncode='+str(run.returncode))
    record = {'version': __version__, 'package_revision':revision,'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'method': 'Image-free source ZIP; fresh extraction, file hashes, all core tests, six Qt suites, actual installed KLayout desktop backend, source startup and sample CLI; no frozen build',
              'environment': 'Existing Linux Python environment; not a clean dependency-install or Windows test',
              'file_count_before_record': len(manifest), 'status': 'PASS', 'runs': records,
              'validation_scope':'Source/archive integrity and the executed checks; not a defect-free model or manufacturing signoff',
              'known_issues_document':'docs/KNOWN_ISSUES.md',
              'historical_v22_boundary_audit':json.loads((ROOT/'results/1102_edge_audit.json').read_text(encoding='utf-8'))}
    record_text = json.dumps(record, ensure_ascii=False, indent=2)
    (ROOT / 'docs' / 'package_verification.json').write_text(record_text, encoding='utf-8')
    # Only add the verification record; the verified implementation stays byte-identical.
    with zipfile.ZipFile(target,'a',zipfile.ZIP_STORED,allowZip64=False) as archive:
        archive.writestr(entry(name+'/docs/package_verification.json'),record_text)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for item in archive.infolist():
            assert item.compress_type==zipfile.ZIP_STORED
            assert Path(item.filename).suffix.lower() not in image_suffixes,item.filename
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
