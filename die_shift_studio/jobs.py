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
    return Path(directory).resolve()/(name+'.dshift.json')

def list_jobs(directory):
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True);out=[]
    for path in sorted(folder.glob('*.json'),key=lambda p:p.name.casefold()):
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data,dict) and 'gds_path' in data and 'parameters' in data:
                out.append((path.name.removesuffix('.json').removesuffix('.dshift'),path.resolve()))
        except (OSError,ValueError):continue
    return out

def atomic_save(path,project):
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True);temp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.'+path.name+'.',suffix='.tmp',delete=False) as f:
            temp=Path(f.name);json.dump(project,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp is not None and temp.exists():temp.unlink()
