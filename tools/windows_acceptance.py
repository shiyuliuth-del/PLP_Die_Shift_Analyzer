"""Validate the isolated one-file Windows application, then archive only that EXE."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def normal_launch(exe,cwd,title):
    import psutil
    user=ctypes.windll.user32
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    start=time.monotonic();process=subprocess.Popen([str(exe)],cwd=cwd)
    try:
        while process.poll() is None and time.monotonic()-start<150:
            pids={process.pid,*(child.pid for child in psutil.Process(process.pid).children(recursive=True))}
            found=[]
            @callback_type
            def visit(hwnd,_):
                pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                if pid.value in pids and user.IsWindowVisible(hwnd):
                    text=ctypes.create_unicode_buffer(256);user.GetWindowTextW(hwnd,text,len(text))
                    if text.value==title:found.append(hwnd);return False
                return True
            user.EnumWindows(visit,0)
            if found:
                elapsed=time.monotonic()-start;user.PostMessageW(found[0],0x0010,0,0)
                assert process.wait(timeout=60)==0
                return elapsed
            time.sleep(.1)
        raise RuntimeError(f'Window {title!r} not observed, exit={process.poll()}')
    finally:
        if process.poll() is None:
            for child in psutil.Process(process.pid).children(recursive=True):child.kill()
            process.kill();process.wait()

def main():
    if os.name!='nt':raise SystemExit('The release acceptance must run on Windows.')
    import pefile
    import sys
    sys.path.insert(0,str(ROOT))
    from die_shift_studio import __version__
    dist=ROOT/'dist';binary=dist/'Die_Shift_Analyze.exe'
    with pefile.PE(str(binary)) as pe:
        assert pe.OPTIONAL_HEADER.Subsystem==2,'Windows GUI subsystem required'
        resources={x.id:x for x in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        icons=len(resources[3].directory.entries);assert 14 in resources and icons>=6
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);install=base/'芯片 工程';install.mkdir();cwd=base/'different cwd';cwd.mkdir()
        exe=install/binary.name;shutil.copy2(binary,exe)
        elapsed=normal_launch(exe,cwd,'Die Shift Analyze '+__version__)
        assert sorted(x.name for x in install.iterdir())==sorted([exe.name,'Jobs','logs'])
        marker=install/'Jobs'/'用户文件.json';marker.write_bytes(b'{}')
        shutil.rmtree(install/'logs')
        records=[]
        for i in range(2):
            out=base/f'smoke-{i}.json';env=dict(os.environ);env.pop('DIE_SHIFT_SMOKE_EXAMPLES',None)
            if i:env['DIE_SHIFT_SMOKE_EXAMPLES']=str(ROOT/'examples')
            subprocess.run([str(exe),'--smoke-test',str(out)],env=env,cwd=cwd,timeout=300,check=True)
            record=json.loads(out.read_text(encoding='utf8'));records.append(record)
            assert record['status']=='PASS' and record['frozen'] and record['platform']=='win32',record
            assert Path(record['jobs']).samefile(install/'Jobs') and (install/'logs').is_dir()
            assert marker.read_bytes()==b'{}' and not list(cwd.iterdir())
        assert records[0]['standalone'] and records[1]['deformation_routes']==2
    target=dist/f'Die_Shift_Analyze_{__version__}_Windows_x64.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as z:z.write(binary,binary.name)
    with zipfile.ZipFile(target) as z:
        assert z.namelist()==[binary.name] and z.testzip() is None
    digest=file_hash(target)
    target.with_suffix('.zip.sha256').write_bytes(f'{digest}  {target.name}\n'.encode())
    info=dict(version=__version__,os=platform.platform(),python=platform.python_version(),commit=os.environ.get('GITHUB_SHA'),
              icons=icons,visible_native_window_seconds=elapsed,exe_sha256=file_hash(binary),zip_sha256=digest,
              single_file=True,existing_job_preserved=True,checks=records)
    (dist/'windows_verification.json').write_text(json.dumps(info,indent=2,ensure_ascii=False),encoding='utf8')
    print('WINDOWS_RELEASE_PASS',target.name,digest,flush=True)

if __name__=='__main__':main()
