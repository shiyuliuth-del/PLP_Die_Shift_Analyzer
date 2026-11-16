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
 from die_shift_studio.gds_merge import winding_outline
 import shapely
 cell.add(gdstk.rectangle((900,0),(901,1),layer=99),gdstk.Polygon(winding_outline(shapely.box(920,0,940,20).difference(shapely.box(925,5,926,6))),layer=99))
 source=folder/'case.oas';lib.write_oas(source)
 project=folder/'source.dshift.json';project.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,
   layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,safe_box_budget=32),overlays={})))
 w.load_project(project);until(lambda:w.data is not None and w.worker is None and not w._tasks)
 assert w.data.path.endswith('.oas')
 assert all('补块' not in tabs.tabText(i) for tabs in w.findChildren(W.QTabWidget) for i in range(tabs.count()))
 w.fields['minimum_area_um2'].setValue(2);until(lambda:w.worker is None and not w._tasks)
 assert len(w.data.layers['99/0'])==1 and abs(shapely.Polygon(w.data.layers['99/0'][0]).area-400)<1e-8
 from die_shift_studio.gds_shapes import shape_nodes,node_fingerprint
 kind,xy,_=shape_nodes(w.data,'99/0',0)
 w.apply_node_edits([dict(layer='99/0',index=0,original=node_fingerprint(kind,xy),nodes=(xy+[2,0]).tolist())])
 until(lambda:w.worker is None and not w._tasks)
 w.write_project(project);w.load_project(project);until(lambda:w.worker is None and not w._tasks)
 assert shapely.Polygon(w.data.layers['99/0'][0]).bounds[0]==922
 w.fields['minimum_area_um2'].setValue(0);until(lambda:w.worker is None and not w._tasks)
 assert len(w.data.layers['99/0'])==2 and shapely.Polygon(w.data.layers['99/0'][1]).bounds[0]==922
 # Press a tilted blade away from its path intersection. Drag distance must
 # start at that press, not jump by the blade's pre-existing lateral offset.
 w.reconstruction_toggle.setChecked(True)
 until(lambda:w.current_frame is not None and w.current_frame.get('reconstruction',{}).get('constraints_complete') and not w._tasks and not w.preview_pending)
 from dataclasses import replace
 from die_shift_studio.reconstruction_edits import key,rotate
 from matplotlib.backend_bases import MouseEvent
 q=w.current_frame['reconstruction']['pieces'][0];plan=q['route_plan']
 settings=replace(w.view_settings,reconstruction_intervals=((*key(plan),100.,500.),))
 w.view_settings=rotate(settings,q,0,.4);w.invalidate_reconstruction_edit();w.request_preview()
 until(lambda:not w._tasks and not w.preview_pending)
 q=w.current_frame['reconstruction']['pieces'][0];pressed=q['moved_cut'].interpolate(.2,normalized=True).coords[0]
 w.ax.set_xlim(50,150);w.ax.set_ylim(-50,50);w.plot_layout(preserve=True);w.canvas.draw()
 def event(name,point):
  x,y=w.ax.transData.transform(point);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button=1))
 event('button_press_event',pressed);assert w.cut_drag.state is not None
 before=w.cut_drag.state['piece']['interval'][0]
 event('motion_notify_event',(pressed[0]+4,pressed[1]));w.cut_drag._tick()
 assert abs(w.cut_drag.state['part']['a']-before-4)<.05,w.cut_drag.state['part']
 w.cut_drag.cancel();until(lambda:not w._tasks)
 w.reconstruction_toggle.setChecked(False);until(lambda:not w._tasks and not w.preview_pending)
 w.requested_ipc_endpoint=folder/'endpoint.json';w.measured_output.toggle.setChecked(True)
 until(lambda:w.measured_output.ready)
 assert w.remote_locked and w.project_path==project and project.is_file()
 assert rpc('info')['result']['die_count']==1
 rpc('output.configure',{'output_directory':str(folder),'max_threads':2,'max_memory_mb':512})
 rows=[dict(die=1,dx_um=4,dy_um=2,angle_deg=.05,rotation_center_um=[17,-12])]
 result=task(rpc('preview.submit',{'dies':rows}));assert not result['connection_failure_count']
 np.testing.assert_allclose(w.current_frame['states'],[[4,2,np.deg2rad(.05)]])
 np.testing.assert_allclose(w.engine.contact.bound.centers,[[17,-12]])
 assert not w.run_button.isEnabled() and not w.preview_tabs.isEnabled()
 result=task(rpc('export.gds',{'filename':'measured.gds'}));assert Path(result['path']).is_file()
 batch=rpc('batch.begin',{'filename':'batch.oas'})['result']
 tasks=[]
 for i in range(6):tasks.append(rpc('batch.submit',{'batch_id':batch['batch_id'],'cell':'P'+str(i),'position_um':[i*1100,0],'dies':rows}))
 for response in tasks:task(response)
 result=task(rpc('batch.end',{'batch_id':batch['batch_id']}));assert result['cells']==6
 lib=gdstk.read_oas(folder/'batch.oas');top=lib.top_level()[0];assert top.name=='TopCell' and len(top.references)==6
 assert len(lib.cells)<6*7+1,'unchanged layer cells not reused'
 # Backend failure and cancellation must have explicit terminal statuses and
 # cannot replace an older output. Queue rejection is retryable, not silence.
 from unittest.mock import patch
 old=folder/'protected.oas';old.write_bytes(b'keep-old');shown=w.current_frame['states'].copy()
 batch=rpc('batch.begin',{'filename':old.name})['result']
 with patch.object(w.measured_output.session,'run',side_effect=ValueError('injected geometry failure')):
  failed=rpc('batch.submit',{'batch_id':batch['batch_id'],'cell':'failed','position_um':[0,0],'dies':rows})['task_id']
  until(lambda:w.measured_output.jobs[failed]['status']=='error')
 end=rpc('batch.end',{'batch_id':batch['batch_id']})['task_id'];until(lambda:w.measured_output.jobs[end]['status']=='error')
 assert old.read_bytes()==b'keep-old'
 rpc('output.configure',{'max_threads':1});batch=rpc('batch.begin',{'filename':old.name})['result']
 saved=json.loads(project.read_text())
 assert not saved['overlays']['reconstruction'] and saved['parameters']['rotation_center']=='center'
 def slow(*_,**kwargs):
  # progress is the second positional argument; cancellation checks live in it.
  progress=_[1]
  while True:progress(25,'cancel regression');time.sleep(.01)
 with patch.object(w.measured_output.session,'run',side_effect=slow):
  queued=[rpc('batch.submit',{'batch_id':batch['batch_id'],'cell':'cancel'+str(i),'position_um':[i*10,0],'dies':rows})['task_id'] for i in range(4)]
  busy=pool.submit(call,folder/'endpoint.json','batch.submit',{'batch_id':batch['batch_id'],'cell':'overflow','position_um':[0,0],'dies':rows})
  until(busy.done);assert busy.result()['error']['type']=='Busy'
  rpc('compute.cancel');until(lambda:not w.measured_output.stopping)
 assert all(w.measured_output.jobs[key]['status']=='cancelled' for key in queued)
 assert old.read_bytes()==b'keep-old' and not list(folder.glob('.dsa-batch-*'))
 np.testing.assert_array_equal(w.current_frame['states'],shown)
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
