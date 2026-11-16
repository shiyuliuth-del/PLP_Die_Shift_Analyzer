"""Real local IPC transport + Qt ownership, cancellation and streamed output."""
import os,sys,tempfile,json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np,gdstk
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.local_ipc import call
from test_v38 import fixture

app=W.QApplication([]);temp=tempfile.TemporaryDirectory();folder=Path(temp.name);w=MainWindow(jobs_dir=folder/'Jobs');w.show()
errors=[];original_error=w.error
def error(text):
 errors.append(str(text))
 if w.remote_locked:w.measured_output.fail_transition(str(text))
w.error=error;pool=ThreadPoolExecutor(8)
def until(predicate,limit=120):
 end=time.monotonic()+limit
 while time.monotonic()<end:
  app.processEvents()
  if predicate():return
  time.sleep(.005)
 raise AssertionError('TIMEOUT '+w.runtime_view.toPlainText()[-2500:])
def rpc(command,params=None):
 future=pool.submit(call,folder/'endpoint.json',command,params or {})
 until(future.done);response=future.result();assert response['status']!='error',response
 return response
def task(response):
 key=response['task_id'];until(lambda:w.measured_output.jobs[key]['status'] in ('completed','error','cancelled'))
 result=rpc('task.get',{'task_id':key})['result'];assert result['status']=='completed',result
 return result['result']
try:
 e,data,c=fixture();roles=e.roles;p=e.p;e.close();lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for key,rows in data.layers.items():
  layer,datatype=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=datatype) for xy in rows))
 source=folder/'case.oas';lib.write_oas(source)
 project=folder/'source.dshift.json';project.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,
   layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,safe_box_budget=32),overlays={})))
 w.load_project(project);until(lambda:w.data is not None and w.worker is None and not w._tasks)
 assert w.data.path.endswith('.oas')
 assert all('补块' not in w.controls.tabText(i) for i in range(w.controls.count()))
 w.requested_ipc_endpoint=folder/'endpoint.json';w.measured_output.toggle.setChecked(True)
 until(lambda:w.measured_output.ready)
 assert w.remote_locked and w.project_path==project and project.is_file()
 assert rpc('info')['result']['die_count']==1
 rpc('output.configure',{'output_directory':str(folder),'max_threads':2,'max_memory_mb':512})
 rows=[dict(die=1,dx_um=4,dy_um=2,angle_deg=0,rotation_center_um=[0,0])]
 result=task(rpc('preview.submit',{'dies':rows}));assert not result['connection_failure_count']
 np.testing.assert_allclose(w.current_frame['states'],[[4,2,0]])
 assert not w.run_button.isEnabled() and not w.preview_tabs.isEnabled()
 result=task(rpc('export.gds',{'filename':'measured.gds'}));assert Path(result['path']).is_file()
 rpc('batch.begin',{'filename':'batch.oas'})
 tasks=[]
 for i in range(6):tasks.append(rpc('batch.submit',{'cell':'P'+str(i),'position_um':[i*1100,0],'dies':rows}))
 for response in tasks:task(response)
 result=task(rpc('batch.end'));assert result['cells']==6
 lib=gdstk.read_oas(folder/'batch.oas');top=lib.top_level()[0];assert top.name=='TopCell' and len(top.references)==6
 assert len(lib.cells)<6*7+1,'unchanged layer cells not reused'
 rpc('compute.cancel');until(lambda:not w.measured_output.stopping)
 task(rpc('session.prepare'));assert rpc('info')['result']['ready']
 rpc('project.close');assert rpc('info')['result']['project'] is None
 task(rpc('project.open',{'path':str(project)}));assert w.measured_output.ready
 w.measured_output.toggle.setChecked(False);until(lambda:not w.measured_output.stopping)
 assert not w.remote_locked and w.run_button.isEnabled() and not (folder/'endpoint.json').exists()
 assert not errors,errors
 assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-3000:]
 print('GUI_V43_PASS: authenticated local IPC, assessment-first, locks, pivots, GDS, concurrent OAS cells, cancel, project switching and release')
finally:
 if w.measured_output.active():w.measured_output.toggle.setChecked(False)
 until(lambda:not w.measured_output.running and not w.measured_output.stopping)
 w.close();until(lambda:not w._tasks);app.processEvents();pool.shutdown();temp.cleanup()
