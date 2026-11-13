"""Build reproducible source-only Windows release archives and validate the source tree."""
import argparse
from datetime import datetime,timezone
import hashlib,io,json,os,shutil,subprocess,sys,tarfile,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio import __version__

def check_source():
    """Stage a clean test checkout. Test helpers and projects do not enter the release."""
    gui=['gui_smoke','gui_v14','gui_scheduler_v20','gui_repair',
         'gui_v24','gui_v25','gui_v26','gui_v27','gui_v28','gui_v29','gui_v30','gui_v32','gui_v33','gui_v34','gui_v35','gui_v36','gui_v38','gui_v39','gui_v40','gui_v41','gui_branch_integration']
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',OPENBLAS_NUM_THREADS='1',PYTHONUTF8='1',PYTHONFAULTHANDLER='1')
    subprocess.run([sys.executable,'tools/check_contribution.py'],cwd=ROOT,env=env,check=True)
    records=[]
    with tempfile.TemporaryDirectory(prefix='die-shift-source-qa-') as name:
        stage=Path(name)
        selected=subprocess.check_output(['git','ls-files','-z','--','die_shift_studio','tests','tools','Jobs','examples',
                   'main.py','die_shift_model.py','RUN.bat','BUILD.bat','requirements.txt','Die_Shift_Analyze.spec'],cwd=ROOT)
        for value in selected.split(b'\0'):
            if not value:continue
            relative=Path(os.fsdecode(value));dst=stage/relative;dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/relative,dst)
        (stage/'results').mkdir();(stage/'figures').mkdir()
        runtime_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((stage/'die_shift_studio').glob('*.py')))).hexdigest()
        commands=[(['-m','unittest','discover','-s','tests','-p','test_*.py','-v'],{})]
        commands.extend(([f'tests/{fn}.py'],{}) for fn in gui)
        commands.extend([(['main.py','--smoke-test','results/default.json'],{}),
                         (['main.py','--smoke-test','results/desktop.json'],{'DIE_SHIFT_NATIVE_BACKEND':'desktop'})])
        for job in sorted((stage/'Jobs').glob('示例*.dshift.json')):
            commands.append((['-m','die_shift_studio','--project','Jobs/'+job.name,'--json','results/'+job.stem+'.json'],{}))
        for args,extra in commands:
            result=subprocess.run([sys.executable,*args],cwd=stage,env={**env,**extra},encoding='utf8',errors='replace',
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
            print(result.stdout,flush=True)
            if result.returncode or 'Traceback (most recent call last)' in result.stdout or '未处理异常' in result.stdout:
                raise RuntimeError(f'Source regression failed (exit {result.returncode}): {args}')
            record=dict(args=args,env=extra,status='PASS',summary=result.stdout.strip().splitlines()[-3:])
            if '--project' in args:
                data=json.loads((stage/args[-1]).read_text(encoding='utf8'))
                assert data['extreme_factors']['checked'] and data['extreme_factors']['package_states']==data['discrete_states']
                record['discrete_states']=data['discrete_states']
            records.append(record)
    out=dict(version=__version__,status='PASS',tested_at_utc=datetime.now(timezone.utc).isoformat(),
             scope='Clean source checkout, local platform. Windows executable verification is recorded separately.',
             platform=sys.platform,verified_runtime_sha256=runtime_hash,gui_suites=len(gui),runs=records)
    (ROOT/'docs/package_verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

SOURCE_FILES={'main.py','RUN.bat','BUILD.bat','Die_Shift_Analyze.spec','requirements.txt'}

def tagged_sources(ref):
    # Read Git objects so checkout EOL settings cannot change the tested ZIP.
    tree=subprocess.check_output(['git','-c','core.autocrlf=false','-c','core.eol=lf',
                                  'archive','--format=tar',ref],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(tree),mode='r:') as tar:
        files={info.name:tar.extractfile(info).read() for info in tar if info.isfile()}
    kernels={'die_shift_studio/cuda_kernels.cu','die_shift_studio/cuda_kernels.ptx'}
    root_files={name for name in files if '/' not in name}
    if root_files!=SOURCE_FILES or any(
            name not in SOURCE_FILES|kernels and not (name.startswith('die_shift_studio/') and name.endswith('.py'))
            for name in files):
        raise ValueError('The source export must contain only the application and the two Windows entry points.')
    required={'die_shift_studio/__init__.py','die_shift_studio/gui.py','die_shift_studio/app_icon.py',
              'die_shift_studio/native_ops.py','die_shift_studio/klayout_worker.py'}
    if not (required|kernels).issubset(files):raise ValueError('A required runtime module is missing')
    return files

def make_source(out):
    files=tagged_sources('HEAD')
    version_file='die_shift_studio/__init__.py'
    if files[version_file].decode().splitlines()!=(ROOT/version_file).read_text(encoding='utf8').splitlines():
        raise ValueError('Source version must be committed before packaging.')
    out.mkdir(parents=True,exist_ok=True)
    target=out/f'Die_Shift_Analyze_{__version__}_Source.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_STORED,allowZip64=False) as z:
        for rel,data in sorted(files.items()):
            info=zipfile.ZipInfo(target.stem+'/'+rel,date_time=(1980,1,1,0,0,0))
            info.create_system=3;info.external_attr=(0o100644<<16)|0x20
            z.writestr(info,data)
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None and z.namelist()==[target.stem+'/'+p for p in sorted(files)]
        for rel,data in files.items():assert z.read(target.stem+'/'+rel)==data,rel
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_bytes(f'{digest}  {target.name}\n'.encode())
    print('SOURCE_RELEASE_PASS',target,len(files),'files',digest)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    sub.add_parser('verify-source')
    build=sub.add_parser('make-source');build.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    if args.mode=='verify-source':check_source()
    else:make_source(args.output)
