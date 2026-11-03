"""Check the development tree or independently verify a Windows-built release ZIP."""
import argparse
from datetime import datetime,timezone
import hashlib,json,os,shutil,struct,subprocess,sys,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio import __version__

def check_source():
    """Stage a clean test checkout. Test helpers and projects do not enter the release."""
    gui=['gui_smoke','gui_v14','gui_v20','gui_scheduler_v20','gui_v21','gui_repair',
         'gui_v24','gui_v25','gui_v26','gui_v27','gui_v28','gui_v29','gui_v30']
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',OPENBLAS_NUM_THREADS='1',PYTHONUTF8='1')
    subprocess.run([sys.executable,'tools/check_contribution.py'],cwd=ROOT,env=env,check=True)
    records=[]
    with tempfile.TemporaryDirectory(prefix='die-shift-source-qa-') as name:
        stage=Path(name)
        selected=subprocess.check_output(['git','ls-files','-z','--','die_shift_studio','tests','tools','Jobs','examples',
                   'main.py','die_shift_model.py','RUN.bat','requirements.txt','Die_Shift_Analyze.spec'],cwd=ROOT)
        for value in selected.split(b'\0'):
            if not value:continue
            relative=Path(os.fsdecode(value));dst=stage/relative;dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/relative,dst)
        (stage/'results').mkdir();(stage/'figures').mkdir()
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
                raise RuntimeError('Source regression failed: '+str(args))
            record=dict(args=args,env=extra,status='PASS',summary=result.stdout.strip().splitlines()[-3:])
            if '--project' in args:
                data=json.loads((stage/args[-1]).read_text(encoding='utf8'))
                assert data['extreme_factors']['checked'] and data['extreme_factors']['package_states']==data['discrete_states']
                record['discrete_states']=data['discrete_states']
            records.append(record)
    out=dict(version=__version__,status='PASS',tested_at_utc=datetime.now(timezone.utc).isoformat(),
             scope='Clean source checkout, local platform. Windows executable verification is recorded separately.',
             platform=sys.platform,gui_suites=len(gui),runs=records)
    (ROOT/'docs/package_verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def check_artifact(archive,verification):
    meta=json.loads(verification.read_text(encoding='utf8'))
    if not meta.get('single_file') or meta['version']!=__version__ or not meta['existing_job_preserved']:
        raise ValueError('Incorrect Windows build report.')
    if (len(meta['checks'])!=2 or not meta.get('source_bat_smoke') or meta['icons']<6 or
            not all(x['platform']=='win32' and x['frozen'] and x['status']=='PASS' for x in meta['checks'])):
        raise ValueError('Incomplete native Windows validation.')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest!=meta['zip_sha256'] or archive.with_suffix('.zip.sha256').read_text().split()[0]!=digest:
        raise ValueError('Unexpected release ZIP digest.')
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist()!=['Die_Shift_Analyze.exe'] or bundle.testzip() is not None:
            raise ValueError('Only the standalone application belongs in the release.')
        exe=bundle.read('Die_Shift_Analyze.exe')
        if exe[:2]!=b'MZ' or hashlib.sha256(exe).hexdigest()!=meta['exe_sha256']:
            raise ValueError('Executable differs from the Windows-tested build.')
        pe=struct.unpack_from('<I',exe,60)[0]
        if (exe[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',exe,pe+4)[0]!=0x8664 or
                struct.unpack_from('<H',exe,pe+24+68)[0]!=2):
            raise ValueError('Expected a Windows x64 GUI executable.')
    print('RELEASE_PASS',archive.name,digest)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    sub.add_parser('verify-source')
    test=sub.add_parser('verify-windows');test.add_argument('zip',type=Path);test.add_argument('validation',type=Path)
    args=parser.parse_args()
    if args.mode=='verify-source':check_source()
    else:check_artifact(args.zip,args.validation)
