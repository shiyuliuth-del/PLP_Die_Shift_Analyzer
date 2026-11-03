"""Validate a locally built Windows EXE in scratch space. Publish no binary."""
import ctypes
from ctypes import wintypes
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import shutil
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]

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
    sys.path.insert(0,str(ROOT))
    from die_shift_studio import __version__
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True,type=Path)
    source=parser.parse_args().source_root.resolve()
    binary=source/'dist'/'Die_Shift_Analyze.exe'
    from PyInstaller.archive.readers import CArchiveReader
    archive=CArchiveReader(str(binary));pyz=next(n for n in archive.toc if n.lower().endswith('.pyz'))
    modules=archive.open_embedded_archive(pyz).toc
    banned={'cupy','cupyx','cupy_backends','torch','tensorflow','nvidia','jax','jaxlib'}
    assert not {m.split('.')[0] for m in modules}.intersection(banned)
    assert any(n.replace('\\','/').endswith('die_shift_studio/cuda_kernels.ptx') for n in archive.toc)
    report=json.loads(next((source/'build').rglob('size-report.json')).read_text(encoding='utf8'))
    assert not report['unexpected_cuda_dlls'] and report['exe_bytes']==binary.stat().st_size
    assert 0<report['exe_bytes']<256*2**20,report
    print('WINDOWS_SIZE_BYTES',report['exe_bytes'],'LARGEST_INPUTS',report['largest_inputs'][:5],flush=True)
    with pefile.PE(str(binary)) as pe:
        assert pe.FILE_HEADER.Machine==0x8664 and pe.OPTIONAL_HEADER.Subsystem==2,'Windows x64 GUI subsystem required'
        resources={x.id:x for x in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        icons=len(resources[3].directory.entries);assert 14 in resources and icons>=6
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);install=base/'芯片 工程';install.mkdir();cwd=base/'different cwd';cwd.mkdir()
        exe=install/binary.name;shutil.copy2(binary,exe)
        elapsed=normal_launch(exe,cwd,'Die Shift Analyze '+__version__)
        assert sorted(x.name for x in install.iterdir())==sorted([exe.name,'Jobs','logs'])
        out=base/'gpu.json'
        subprocess.run([str(exe),'--gpu-info',str(out)],cwd=cwd,timeout=90,check=True)
        gpu=json.loads(out.read_text(encoding='utf8'))
        assert gpu['backend'] in ('cuda','cpu') and 'CuPy' not in gpu['reason'] and 'ImportError' not in gpu['reason'],gpu
        if gpu['backend']=='cuda':assert gpu['provider']=='NVIDIA Driver' and gpu['operations']
        print('FROZEN_GPU_PROBE',json.dumps(gpu,ensure_ascii=True),flush=True)
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
        # Exercise the single BAT through cmd.exe with a spaced, Unicode argument.
        source_out=base/'源码 launch.json'
        no_samples=base/'no samples'
        env=dict(os.environ,DIE_SHIFT_SMOKE_EXAMPLES=str(no_samples),DIE_SHIFT_PYTHON=sys.executable)
        subprocess.run(['cmd','/d','/c','call','RUN.bat','--smoke-test',str(source_out)],cwd=source,env=env,check=True,timeout=30)
        until=time.monotonic()+90
        while not source_out.is_file() and time.monotonic()<until:time.sleep(.15)
        source_report=json.loads(source_out.read_text(encoding='utf8'))
        assert source_report['status']=='PASS' and not source_report['frozen']
        assert records[0]['standalone'] and records[1]['deformation_routes']==2
    print('WINDOWS_SOURCE_BUILD_PASS',__version__,platform.platform(),
          f'normal window {elapsed:.2f}s, {icons} icon sizes, source BAT and isolated EXE verified; no binary published',flush=True)

if __name__=='__main__':main()
