"""Real scope switch: computation, caches, both summaries, saved engineering mode."""
import os,sys,time,tempfile,json
from pathlib import Path
from dataclasses import asdict
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gdstk
from shapely.geometry import Point,box
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import Parameters
from qt_shutdown import close_window
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();root=Path(tmp.name)
w=MainWindow(jobs_dir=root/'Jobs');w.resize(1400,950);w.show();errors=[];w.error=lambda s:errors.append(str(s))
def wait():
 end=time.monotonic()+240
 while time.monotonic()<end:
  app.processEvents();time.sleep(.005)
  if not (w._tasks or w.preview_pending or w._coverage_pending or w.reconstruction_yield.timer.isActive() or w.width_yield.timer.isActive() or w.viewport_timer.isActive()):break
 else:raise AssertionError(w.runtime_view.toPlainText()[-4000:])
 assert not errors,errors
try:
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 shapes={'127/0':[box(-300,-300,1400,300)],'128/0':[box(x-30,-30,x+30,30) for x in (0,1000)],
         '3/0':[Point(x,0).buffer(20) for x in (0,1000)],'4/0':[Point(x,0).buffer(4) for x in (0,1000)],
         '5/0':[box(x-100,-100,x+150,100) for x in (0,1000)],'6/0':[Point(x+80,0).buffer(4) for x in (0,1000)]}
 roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
 for key,polys in shapes.items():
  layer,dt=map(int,key.split('/'));cell.add(*(gdstk.Polygon(g.exterior.coords,layer=layer,datatype=dt) for g in polys))
 source=root/'case.gds';lib.write_gds(source)
 p=Parameters(multi_bound_x_um=1,multi_bound_y_um=0,bound_angle_deg=0,line_width_um=5,line_space_um=5,wafer_line_width_um=5,wafer_line_space_um=5,annulus_um=5,moving_annulus_um=5)
 job=root/'case.dshift.json';job.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,
       layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings={'animation_enabled':False,'reconstruction_grid_nodes':3},overlays={})))
 wait();w.load_project(job);wait();assert w.view_settings.evaluation_scope=='focus'
 assert not w.setting_fields['reconstruction_grid_nodes'].isEnabled()
 w.run_evaluation();wait();assert w.result['extreme_factors']['package_states']==3
 w.width_toggle.setChecked(True);w.reconstruction_toggle.setChecked(True);wait()
 assert w.reconstruction_yield.bank['method']=='focus';assert w.reconstruction_yield.bank['total']==3
 assert w.width_yield.bank['method']=='focus';assert w._width_grid_summary['package_states']==3
 assert '重点组合' in w.width_yield.label.text()
 w.grid_mode.setCurrentIndex(0);wait()
 assert w.view_settings.evaluation_scope=='all';assert w.setting_fields['reconstruction_grid_nodes'].isEnabled();assert w.result['extreme_factors']['package_states']==9
 assert w.reconstruction_yield.bank['method']=='grid';assert w.reconstruction_yield.evidence['package_states']==9
 assert w.width_yield.bank['method']=='grid'
 w.grid_mode.setCurrentIndex(1);wait();assert w.reconstruction_yield.bank['method']=='focus'
 # Start a stale full-scope assessment, then change scope before it finishes.
 from unittest.mock import patch
 import die_shift_studio.gui as gui
 actual=gui.evaluate
 def delayed(*args,**kwargs):
  if kwargs.get('extreme_scope')=='all':time.sleep(.25)
  return actual(*args,**kwargs)
 # Evict only assessment values to exercise the real worker acceptance guard.
 for key in list(w.session_values):
  if isinstance(key,tuple) and key and key[0]=='assessment-v28':del w.session_values[key]
 with patch.object(gui,'evaluate',side_effect=delayed):
  w.grid_mode.setCurrentIndex(0)
  deadline=time.monotonic()+5
  while w.worker is None and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
  assert w.worker is not None
  w.grid_mode.setCurrentIndex(1);wait()
 assert w.result['evaluation_scope']=='focus' and w.reconstruction_yield.bank['method']=='focus'

 saved=root/'Jobs'/'saved'/'saved.dshift.json';w.write_project(saved);w.load_project(saved);wait()
 assert w.grid_mode.currentIndex()==1 and w.view_settings.evaluation_scope=='focus'
 assert not errors,errors
 print('GUI_EVALUATION_SCOPE_PASS focus 3 / all 9, reconstruction/DRC/width/cache/saved mode')
finally:close_window(app,w);tmp.cleanup()
