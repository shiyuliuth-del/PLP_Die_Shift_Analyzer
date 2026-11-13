"""Actual mouse menus/scroll/cancel events, source edit persistence and overlay cleanup."""
import os,sys,time,tempfile,json,threading
from pathlib import Path
from dataclasses import asdict,replace
import numpy as np,gdstk
from matplotlib.backend_bases import MouseEvent
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_v38 import fixture
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.reconstruction_edits import key
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1400,950);w.show();errors=[];w.error=lambda s:errors.append(str(s))
# Native all-pose analysis is checked separately. These tests exercise actual
# frame geometry and Qt input while keeping background yield scheduling idle.
w.reconstruction_yield.refresh=lambda *a:None
w.width_yield.refresh=lambda *a:None

def wait(timeout=100):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.viewport_timer.isActive(),w.transition.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending)):break
  time.sleep(.003)
 else:raise AssertionError(('timeout',w.runtime_view.toPlainText()[-5000:]))
 assert not errors,errors

def event(name,point,button=1,step=0):
 x,y=w.ax.transData.transform(point);e=MouseEvent(name,w.canvas,x,y,button=button,step=step)
 w.canvas.callbacks.process(name,e);app.processEvents();return e

def menu(title,point):
 event('button_press_event',point,3);event('button_release_event',point,3)
 widget=next(q for q in app.topLevelWidgets() if isinstance(q,W.QMenu) and q.isVisible())
 action=next(a for a in widget.actions() if a.text()==title);action.trigger();widget.close();wait()

try:
 e,data,c=fixture();roles=e.roles;p=e.p;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for name,rows in data.layers.items():
  layer,dt=map(int,name.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 source=folder/'case.gds';lib.write_gds(source)
 project=dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings={'animation_enabled':False},overlays={'reconstruction':True})
 path=folder/'source.dshift.json';path.write_text(json.dumps(project));w.load_project(path);wait()
 if not w.reconstruction_toggle.isChecked():w.reconstruction_toggle.setChecked(True);wait()
 w.request_preview();wait();q=w.current_frame['reconstruction']['pieces'][0];plan=q['route_plan']
 w.view_settings=replace(w.view_settings,reconstruction_intervals=((*key(plan),100.,500.),),reconstruction_forced=(('5/0',0),))
 w.invalidate_reconstruction_edit();w.request_preview();wait();w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw()
 q=w.current_frame['reconstruction']['pieces'][0];point=q['moved_cut'].centroid.coords[0]
 limits=(w.ax.get_xlim(),w.ax.get_ylim());event('button_press_event',point)
 assert w.cut_drag.state is not None
 event('scroll_event',point,'down',-1);event('button_release_event',point);wait()
 assert w.view_settings.reconstruction_angles and np.allclose(limits,(w.ax.get_xlim(),w.ax.get_ylim()))
 angle=w.view_settings.reconstruction_angles[0][-1];assert angle>np.pi/2
 q=w.current_frame['reconstruction']['pieces'][0];point=q['moved_cut'].centroid.coords[0]
 event('button_press_event',point);event('motion_notify_event',(point[0]+30,point[1]));event('button_release_event',(point[0]+30,point[1]));wait()
 assert w.view_settings.reconstruction_angles[0][-1]==angle
 q=w.current_frame['reconstruction']['pieces'][0];menu('撤销旋转',q['moved_cut'].centroid.coords[0]);assert not w.view_settings.reconstruction_angles
 menu('增加动区',(300.,0.));assert len(w.current_frame['reconstruction']['pieces'])==2
 w.manual_states=np.array([[15.,10.,.005]]);w.request_preview();wait()
 assert all(q['valid'] for q in w.current_frame['reconstruction']['pieces'])
 q=w.current_frame['reconstruction']['pieces'][0];menu('删除动区',q['bridge'].representative_point().coords[0]);assert len(w.current_frame['reconstruction']['pieces'])==1
 q=w.current_frame['reconstruction']['pieces'][0];menu('删除动区',q['bridge'].representative_point().coords[0]);assert not w.current_frame['reconstruction']['pieces']
 assert ('5/0',0) in w.view_settings.reconstruction_ignored
 menu('增加动区',(300.,0.));assert w.current_frame['reconstruction']['pieces'];assert not w.view_settings.reconstruction_ignored
 save=folder/'edited.dshift.json';w.write_project(save);expected=w.view_settings.reconstruction_key();w.load_project(save);wait();assert w.view_settings.reconstruction_key()==expected
 # Cancelling a queued delivery cannot publish a new state after rollback.
 before=w.current_frame;applied=[];started=threading.Event()
 def work(progress):
  started.set()
  for i in range(1000):progress(i%100,'测试取消');time.sleep(.003)
  return 'wrong'
 w.start_worker(work,applied.append,interactive=True)
 while not started.is_set():app.processEvents();time.sleep(.001)
 app.processEvents();assert w.task_control.button.isVisible()==w.progress.isVisible()
 w.task_control.button.click();wait();assert not applied and w.current_frame is before
 assert not w.progress.isVisible() and not w.task_control.button.isVisible()
 # Repeated hover/disable must clear line, point, pulse and cached framebuffer.
 for _ in range(3):
  w.reconstruction_toggle.setChecked(True);w.request_preview();wait()
  q=w.current_frame['reconstruction']['pieces'][0];point=q['moved_cut'].centroid.coords[0]
  event('motion_notify_event',point);w.reconstruction_toggle.setChecked(False);app.processEvents();w.plot_layout(preserve=True)
  o=w.reconstruction_overlay
  assert not any(a.get_visible() for a in (o.cuts,o.second_cuts,o.points,o.second_points,o.problem_points))
  assert not o.pulse_timer.isActive()
 print('GUI_V41_PASS: drag-wheel rotations, retained angles, menus, serial zones, Jobs, cancel rollback, hidden overlay/pulse cleanup')
finally:w.close();app.processEvents();tmp.cleanup()
