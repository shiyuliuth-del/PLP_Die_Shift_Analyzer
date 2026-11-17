"""Installation-local projects with atomic saves and safe job names."""
import json,os,re,sys,tempfile
from pathlib import Path

def installation_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent

def job_path(directory,name):
    name=name.strip()
    if not name or len(name)>80 or name in ('.','..') or re.search(r'[\\/:*?"<>|\x00-\x1f]',name) or name.endswith(('.', ' ')):
        raise ValueError('工程名称须为1～80个字符，不能包含路径分隔符或文件名禁用字符')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('该名称属于系统保留名称，请换一个名称')
    return Path(directory).resolve()/name/(name+'.dshift.json')

def list_jobs(directory):
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True);out=[]
    for path in sorted([*folder.glob('*.json'),*folder.glob('*/*.json')],key=lambda p:p.name.casefold()):
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data,dict) and 'gds_path' in data and 'parameters' in data:
                out.append((path.name.removesuffix('.json').removesuffix('.dshift'),path.resolve()))
        except (OSError,ValueError):continue
    chosen={}
    for name,path in out:
        if name not in chosen or path.parent!=folder.resolve():chosen[name]=path
    return sorted(chosen.items(),key=lambda q:q[0].casefold())

def atomic_save(path,project):
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True);temp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.'+path.name+'.',suffix='.tmp',delete=False) as f:
            temp=Path(f.name);json.dump(project,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp is not None and temp.exists():temp.unlink()


def local_source(project,source):
    """Copy an immutable input into the project, never overwrite different copper."""
    import hashlib,shutil
    project=Path(project).resolve();source=Path(source).resolve();project.parent.mkdir(parents=True,exist_ok=True)
    if source.parent==project.parent:return source.name
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    target=project.parent/source.name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
        target=project.parent/(source.stem+'-'+digest[:16]+source.suffix)
    if not target.exists():
        fd,name=tempfile.mkstemp(dir=project.parent,prefix='.source-');os.close(fd)
        try:
            shutil.copyfile(source,name)
            with open(name,'rb') as f:os.fsync(f.fileno())
            os.replace(name,target)
        finally:
            if os.path.exists(name):os.unlink(name)
    return target.name


def resolve_project_path(path):
    path=Path(path).expanduser().resolve()
    if path.is_dir():
        candidates=list(path.glob('*.dshift.json')) or list(path.glob('*.json'))
        if len(candidates)!=1:raise ValueError('项目文件夹须包含唯一工程JSON')
        return candidates[0]
    return path
