"""Real GD55 identified cuts: axis alignment, independent mouse handles and rotation."""
import os,sys,time,json,tempfile
from pathlib import Path
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.gui import MainWindow,W
from qt_shutdown import close_window
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1600,1000);w.show();errors=[];w.error=lambda s:errors.append(str(s))
# Full-grid/core rule checks are separate. Here test actual source/renderer/Qt,
# avoiding unrelated asynchronous yield work while dragging identified handles.
w.reconstruction_yield.refresh=lambda *a:None;w.width_yield.refresh=lambda *a:None

def wait(limit=180):
 end=time.monotonic()+limit
 while time.monotonic()<end:
  app.processEvents()
  if not any((w._tasks,w.worker,w.preview_workers,w.preview_pending,w.constraint_feedback.worker,w.constraint_feedback.pending,w.transition.timer.isActive(),w.filter_job,w.coverage_timer.isActive(),w.motion_worker,w.motion_pending)):break
  time.sleep(.005)
 else:raise AssertionError(w.runtime_view.toPlainText()[-6000:])
 assert not errors,errors
 assert 'Traceback' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-6000:]
def event(name,p,step=0):
 x,y=w.ax.transData.transform(p);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button='up' if step else 1,step=step));app.processEvents()
try:
 project=json.loads((ROOT/'Jobs/GD55/GD55.dshift.json').read_text());project['gds_path']=str(ROOT/'Jobs/GD55/GD55_1105.gds');project.pop('saved_results',None)
 # The V49 drag fixture targets two cuts selected by the automatic policy.
 project['extreme_view']=dict(mode='focus',filter_problems=False);project['settings'].update(animation_enabled=False,reconstruction_die_mode='auto');project['overlays']={'reconstruction':False};path=folder/'case.dshift.json';path.write_text(json.dumps(project))
 w.load_project(path);wait();w.run_evaluation();wait();w.problem_filter.setCurrentIndex(0);wait();w.reconstruction_toggle.setChecked(True);wait()
 sources=w.geometry_cache.layers['5/0']['geometry']
 for target in ((2003.,1368.),(-101.8,-726.)):
  index=min(range(len(sources)),key=lambda i:sources[i].distance(shapely.Point(target)))
  pieces=[q for q in w.current_frame['reconstruction']['pieces'] if q['layer']=='5/0' and q['index']==index]
  assert pieces,(target,index)
  q=pieces[0];cut=q['moved_cut'];other=q['extra_cut'];assert cut.distance(other)>.1
  plan=q['route_plan'];tangent=plan.tangent(sum(q['interval'])/2)
  expected=np.array([1.,0.]) if target[0]>1000 else np.array([1.,1.])/np.sqrt(2)
  assert abs(tangent@expected)>.98,(target,plan.route.wkt)
  for side in (0,1):
   q=next(q for q in w.current_frame['reconstruction']['pieces'] if q['layer']=='5/0' and q['index']==index and q.get('route_plan'))
   cut=q['moved_cut'] if side==0 else q['extra_cut'];point=np.asarray(cut.interpolate(.5,normalized=True).coords[0])
   w.set_view(point,350,220);w.plot_layout(preserve=True);w.canvas.draw()
   event('button_press_event',point);assert w.cut_drag.state is not None,(target,side,cut.wkt)
   before=w.cut_drag.state['piece']['interval'];plan=w.cut_drag.state['piece']['route_plan']
   cursor=point+plan.tangent(before[side])*(3 if side==0 else -3)
   event('motion_notify_event',cursor);w.cut_drag._tick()
   after=w.cut_drag.state['part'];assert abs(after['a' if side else 'b']-before[1-side])<.01
   assert abs(after['b' if side else 'a']-before[side])>.1
   event('scroll_event',cursor,step=1);w.canvas.draw();assert w.cut_drag.state is not None
   event('button_release_event',cursor);wait()
  if os.environ.get('DSA_QA_SCREENSHOT'):w.grab().save(os.environ['DSA_QA_SCREENSHOT']+str(index)+'.png')
 print('GUI_V49_GD55_PASS both reported source routes follow actual bar direction; both independent handles drag and wheel-refresh',flush=True)
finally:close_window(app,w);tmp.cleanup()
