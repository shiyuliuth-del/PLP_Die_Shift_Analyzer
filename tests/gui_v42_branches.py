"""Real mouse regression for merged branch domains and reversible crossing."""
import os,sys,time,tempfile,json,threading
from pathlib import Path
from unittest.mock import patch
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
  if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending)):break
  time.sleep(.003)
 else:raise AssertionError(('timeout',w.runtime_view.toPlainText()[-5000:]))
 assert not errors,errors
 assert '[ERROR]' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-5000:]

def event(name,point,button=1,step=0):
 x,y=w.ax.transData.transform(point);e=MouseEvent(name,w.canvas,x,y,button=button,step=step)
 w.canvas.callbacks.process(name,e);app.processEvents();return e

def menu(title,point):
 event('button_press_event',point,3);event('button_release_event',point,3)
 widget=next(q for q in app.topLevelWidgets() if isinstance(q,W.QMenu) and q.isVisible())
 action=next(a for a in widget.actions() if a.text()==title);action.trigger();widget.close();wait()

try:
 from test_v40 import make_engine
 from shapely.geometry import Point,LineString,box
 import shapely
 points=[(-300.,0.),(300.,180.),(300.,-180.)]
 source=shapely.union_all([LineString([p,(0,0)]).buffer(20) for p in points]+[Point(p).buffer(35) for p in points])
 shapes={'127/0':[box(-600,-400,600,400)],'128/0':[box(-360,-80,-240,80)],'3/0':[Point(points[0]).buffer(15)],'4/0':[Point(points[0]).buffer(8)],'5/0':[source],'6/0':[Point(p).buffer(8) for p in points[1:]]}
 from die_shift_studio.core import Parameters
 from die_shift_studio.settings import ViewSettings
 e,data,c=make_engine(shapes,Parameters(single_bound_x_um=5,single_bound_y_um=5,bound_angle_deg=0,annulus_um=5),ViewSettings());roles=e.roles;p=e.p;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for name,rows in data.layers.items():
  layer,dt=map(int,name.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 copper_source=source
 source=folder/'case.gds';lib.write_gds(source)
 project=dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings={'animation_enabled':False,'reconstruction_forced':[['5/0',0]]},overlays={'reconstruction':True})
 path=folder/'source.dshift.json';path.write_text(json.dumps(project));w.load_project(path);wait()
 if not w.reconstruction_toggle.isChecked():w.reconstruction_toggle.setChecked(True);wait()
 w.request_preview();wait();w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw()
 first=w.current_frame['reconstruction']['pieces'];assert len(first)>=1
 target=next(q for q in first if q.get('graph_members'));members=target['graph_members'];assert len(members)==2
 # Pick a true merged-domain outer boundary and drag across the branch junction.
 point=target['moved_cut'].representative_point().coords[0]
 event('button_press_event',point);assert w.cut_drag.state is not None
 initial=w.cut_drag.state['piece'];plan=initial['route_plan'];a,b=initial['interval']
 cursor=plan.route.interpolate(min(b-25.,plan.length*.6)).coords[0]
 event('motion_notify_event',cursor);w.cut_drag._tick();assert w.cut_drag.state['part'] is not None
 assert not w.current_frame['reconstruction']['constraints_complete']
 assert all(v['geometry'].is_valid for v in w.current_frame['reconstruction']['replacements']['5/0'])
 # Starting the debounce task after the foreground clears its active engine
 # must still use the completed solver captured with this drag request.
 from die_shift_studio.reconstruction import Reconstructor
 original_plans=Reconstructor._plans_for_all_poses
 def cached_plans(solver,progress):
  assert solver._plans is not None or solver.plan_pool,'feedback rebuilt all paths'
  return original_plans(solver,progress)
 with patch.object(Reconstructor,'_plans_for_all_poses',cached_plans):
  saved_engine=w.engine;w.engine=None;w.constraint_feedback.start();w.engine=saved_engine
  event('button_release_event',cursor);wait()
 assert w.current_frame['reconstruction']['constraints_complete']
 assert all(v['geometry'].is_valid for v in w.current_frame['reconstruction']['replacements']['5/0'])
 assert not errors
 # Return across the junction. No duplicated copper or missing terminal pads.
 target=next(q for q in w.current_frame['reconstruction']['pieces'] if q.get('route_plan'))
 point=target['moved_cut'].representative_point().coords[0]
 event('button_press_event',point);assert w.cut_drag.state is not None
 plan=w.cut_drag.state['piece']['route_plan'];cursor=plan.route.interpolate(80.).coords[0]
 event('motion_notify_event',cursor);event('button_release_event',cursor);wait()
 geometry=w.current_frame['reconstruction']['replacements']['5/0'][0]['geometry']
 original=w.geometry_cache.layers['5/0']['geometry'][0]
 assert geometry.symmetric_difference(original).area<.01,(geometry.symmetric_difference(original).area,geometry.area,original.area)
 assert all(v['geometry'].is_valid for v in w.current_frame['reconstruction']['replacements']['5/0'])
 print('GUI_V42_BRANCH_PASS: real merged-handle drag across junction and back, deferred native validation and exact nominal copper')
finally:w.close();app.processEvents();tmp.cleanup()
