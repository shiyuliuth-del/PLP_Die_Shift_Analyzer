"""Separate application process: CLI startup, IPC preview and real exit codes."""
import os,sys,tempfile,json,time,subprocess
from pathlib import Path
from dataclasses import asdict,replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import gdstk
from test_v38 import fixture
from die_shift_studio.local_ipc import call
from die_shift_studio.ipc_client import wait

with tempfile.TemporaryDirectory() as tmp:
 folder=Path(tmp);endpoint=folder/'ipc.json';source=folder/'source.gds';project=folder/'case.dshift.json'
 e,data,_=fixture();roles=e.roles;p=replace(e.p,single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=0);e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for key,rows in data.layers.items():
  layer,datatype=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=datatype) for xy in rows))
 lib.write_gds(source)
 project.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=list(roles)[::-1],parameters=asdict(p),settings={'animation_enabled':False},overlays={})))
 def spawn(path,name):
  command=[os.environ['DIE_SHIFT_TEST_EXE']] if os.environ.get('DIE_SHIFT_TEST_EXE') else [sys.executable,str(ROOT/'main.py')]
  log=(folder/name).open('wb');process=subprocess.Popen(command+(['--project',str(path)] if path else [])+['--measured-output','--ipc-endpoint',str(endpoint)],cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT)
  return process,log
 process,log=spawn(None,'empty.log')
 try:
  end=time.monotonic()+120
  while time.monotonic()<end:
   assert process.poll() is None,(folder/'empty.log').read_text(errors='replace')
   try:
    reply=call(endpoint,'info')
    if reply.get('status')=='completed':break
   except (OSError,EOFError,ValueError):pass
   time.sleep(.1)
  else:raise AssertionError('empty IPC ready timeout')
  assert reply['result']['project'] is None and not reply['result']['ready']
  assert not reply['result']['running']
  assert call(endpoint,'app.shutdown')['status']=='completed'
  assert process.wait(timeout=60)==0
 finally:
  if process.poll() is None:
   call(endpoint,'app.shutdown');process.wait(timeout=60)
  log.close()
 process,log=spawn(project,'good.log')
 try:
  end=time.monotonic()+120
  while time.monotonic()<end:
   assert process.poll() is None,(folder/'good.log').read_text(errors='replace')
   try:
    if call(endpoint,'info')['result']['ready']:break
   except (OSError,EOFError,ValueError):pass
   time.sleep(.1)
  else:raise AssertionError('IPC ready timeout')
  reply=call(endpoint,'preview.submit',{'dies':[dict(die=1,dx_um=2,dy_um=3,angle_deg=.02,rotation_center_um=[17,-12])]})
  assert reply['status']=='accepted',reply
  done=wait(endpoint,reply['task_id'],timeout=120);assert done['status']=='completed',done
  config=call(endpoint,'output.configure',{'output_directory':str(folder),'max_threads':1});assert config['status']=='completed',config
  export=call(endpoint,'export.gds',{'filename':'actual.gds','allow_failed':True});assert export['status']=='accepted',export
  done=wait(endpoint,export['task_id'],timeout=120);assert done['status']=='completed',done
  assert gdstk.read_gds(folder/'actual.gds').top_level()
  batch=call(endpoint,'batch.begin',{'filename':'actual.oas','allow_failed':True});assert batch['status']=='completed',batch
  batch_id=batch['result']['batch_id']
  submitted=call(endpoint,'batch.submit',{'batch_id':batch_id,'cell':'sample','position_um':[500,700],'dies':[dict(die=1,dx_um=0,dy_um=0,angle_deg=0)]});assert submitted['status']=='accepted',submitted
  done=wait(endpoint,submitted['task_id'],timeout=120);assert done['status']=='completed',done
  finished=call(endpoint,'batch.end',{'batch_id':batch_id});assert finished['status']=='accepted',finished
  done=wait(endpoint,finished['task_id'],timeout=120);assert done['status']=='completed',done
  assert len(gdstk.read_oas(folder/'actual.oas').top_level()[0].references)==1
  assert call(endpoint,'app.shutdown')['status']=='completed'
  assert process.wait(timeout=60)==0,(folder/'good.log').read_text(errors='replace')
  assert not endpoint.exists()
 finally:
  if process.poll() is None:
   try:call(endpoint,'app.shutdown')
   except (OSError,EOFError):pass
   process.wait(timeout=60)
  log.close()
 process,log=spawn(folder/'missing.dshift.json','bad.log')
 try:assert process.wait(timeout=60)==1
 finally:log.close()
 assert not endpoint.exists()
 multi=gdstk.Library();multi.new_cell('A').add(gdstk.rectangle((0,0),(1,1)));multi.new_cell('B').add(gdstk.rectangle((0,0),(2,2)))
 multi.write_oas(folder/'multi.oas');ambiguous=folder/'ambiguous.dshift.json'
 ambiguous.write_text(json.dumps(dict(schema_version=5,gds_path=str(folder/'multi.oas'),parameters={},settings={})))
 process,log=spawn(ambiguous,'ambiguous.log')
 try:assert process.wait(timeout=60)==1
 finally:log.close()
 assert not endpoint.exists()
 assert 'Traceback' not in (folder/'good.log').read_text(errors='replace')
 print('GUI_V43_PROCESS_PASS: new application process, nondefault pivot, GDS and OAS output, clean IPC shutdown, failed startup exits without modal dialog')
