"""Several live sibling servers: unique automatic endpoints and exact project binding."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RealDataAnalyse.instances import connect
from RealDataAnalyse.transport import Transport, default_endpoint
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rda-instances-') as name:
    root=Path(name);old=os.environ.get('LOCALAPPDATA');os.environ['LOCALAPPDATA']=str(root/'state')
    projects=[];processes=[];transports=[];streams=[]
    command=[os.environ['DIE_SHIFT_TEST_EXE']] if os.environ.get('DIE_SHIFT_TEST_EXE') else [sys.executable,str(ROOT/'main.py')]
    def start(project):
        stream=open(root/f'process-{len(processes)}.log','w',encoding='utf8');streams.append(stream)
        process=subprocess.Popen(command+['--project',str(project),'--measured-output'],stdout=stream,stderr=subprocess.STDOUT)
        processes.append(process)
        before={t.endpoint for t in transports};end=time.monotonic()+120
        while time.monotonic()<end:
            if process.poll() is not None:raise AssertionError('server exited during launch')
            for path in default_endpoint().parent.glob('endpoint*.json'):
                if path in before:continue
                t=Transport(path,timeout=2)
                try:info=t.request('info',retries=0)['result']
                except (OSError,EOFError,TimeoutError,ValueError):continue
                if info.get('ready') and Path(info['project'])==project:
                    transports.append(t);return t,info
            time.sleep(.1)
        raise AssertionError('new instance did not become ready')
    try:
        for i in range(2):
            folder=root/f'Project {i+1}';shutil.copytree(Path(os.environ.get('REALDATA_TEST_CASE',ROOT/'Jobs/GD55')),folder)
            projects.append(folder/'GD55.dshift.json')
        first,a=start(projects[0]);second,b=start(projects[1])
        assert first.endpoint!=second.endpoint and a['instance_id']!=b['instance_id']
        selected,info,launched=connect(projects[0],second.endpoint,threading.Event(),lambda _:None,30)
        assert not launched and selected.endpoint==first.endpoint and info['instance_id']==a['instance_id']
        assert second.request('info')['result']['project']==str(projects[1])
        third,c=start(projects[0]);assert c['instance_id']!=a['instance_id']
        try:connect(projects[0],second.endpoint,threading.Event(),lambda _:None,30)
        except ValueError as exc:assert '多个实例' in str(exc)
        else:raise AssertionError('ambiguous same-project instances silently selected')
        chosen,info,launched=connect(projects[0],third.endpoint,threading.Event(),lambda _:None,30)
        assert chosen.endpoint==third.endpoint and info['instance_id']==c['instance_id'] and not launched
        for t,project in zip(transports,[projects[0],projects[1],projects[0]]):
            info=t.request('info')['result'];assert info['project']==str(project) and not info['batch'] and not info['running']
    finally:
        for t in transports:
            try:t.request('app.shutdown',retries=0)
            except (OSError,EOFError):pass
        for process in processes:
            assert process.wait(timeout=90)==0
        for stream in streams:stream.close()
        if old is None:os.environ.pop('LOCALAPPDATA',None)
        else:os.environ['LOCALAPPDATA']=old
print('REALDATA_INSTANCES_PASS: three simultaneous servers, unique default endpoints, exact binding, ambiguous selection rejected, no retarget/duplicate jobs, clean exits')
