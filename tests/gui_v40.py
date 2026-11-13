"""Real selection/callouts, ignore persistence, latest-style completion and export reuse."""
import os,sys,time,tempfile,json
from pathlib import Path
from dataclasses import asdict,replace
from unittest.mock import patch
import numpy as np,gdstk,shapely
from matplotlib.backend_bases import MouseEvent
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_v38 import fixture
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import Parameters
from die_shift_studio.reconstruction_events import zone_key,active_region
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1450,950);w.show();errors=[];w.error=lambda s:errors.append(str(s))
def wait(timeout=180):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  busy=(w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.coverage_timer.isActive(),w.viewport_timer.isActive(),w.transition.timer.isActive(),w.reconstruction_yield.worker,w.reconstruction_yield.pending,w.reconstruction_yield.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending,w.filter_job)
  if not any(busy):break
  time.sleep(.005)
 else:raise AssertionError(('timeout',w.runtime_view.toPlainText()[-8000:],busy))
 assert not errors,errors
 assert '[ERROR]' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-8000:]
def event(name,point,button=1):
 x,y=w.ax.transData.transform(point);e=MouseEvent(name,w.canvas,x,y,button=button)
 w.canvas.callbacks.process(name,e);app.processEvents()
def click(point,button=1):event('button_press_event',point,button);event('button_release_event',point,button)
def menu_action(title,point):
 click(point,3)
 menu=next(q for q in app.topLevelWidgets() if isinstance(q,W.QMenu) and q.isVisible())
 action=next(q for q in menu.actions() if q.text()==title);action.trigger();menu.close();wait()
try:
 e,data,_=fixture();roles=e.roles;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for key,rows in data.layers.items():
  layer,dt=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 source=folder/'case.gds';lib.write_gds(source)
 p=Parameters(line_width_um=40,line_space_um=40,annulus_um=10,moving_annulus_um=5,single_bound_x_um=25,single_bound_y_um=25)
 project={'schema_version':5,'gds_path':str(source),'top_cell':'TOP','roles':roles,'layer_order':['6/0','5/0','4/0','3/0','128/0','127/0'],'parameters':asdict(p),'settings':{'animation_enabled':False},'overlays':{'reconstruction':False}}
 job=folder/'initial.dshift.json';job.write_text(json.dumps(project));w.load_project(job);wait();w.run_evaluation();wait()
 assert w.params().line_width_um==40
 w.reconstruction_toggle.setChecked(True);wait()
 assert w.reconstruction_yield.evidence and w.reconstruction_yield.evidence['complete']
 assert w.problem_filter.isChecked() and w.extreme_table.rowCount()>1
 assert not w.dirty,'reconstruction edits must not invalidate the base assessment'
 views=w.reconstruction_overlay.zone_views;assert any(status=='red' for q,status,g in views.values())
 q,status,region=next(v for v in views.values() if v[1]=='red')
 assert region.intersection(q['regions']['B']).area<.001,'red fill reached fixed copper'
 point=region.representative_point().coords[0];w.ax.set_xlim(point[0]-180,point[0]+180);w.ax.set_ylim(point[1]-100,point[1]+100);w.plot_layout(preserve=True);w.canvas.draw()
 click(point);assert w.selection.selected
 labels=[a for a in w.reconstruction_overlay.labels if a.get_visible()]
 assert labels and any('线宽不足' in a.get_text() for a in labels)
 assert not any(isinstance(a,W.QMenu) and a.isVisible() for a in app.topLevelWidgets()),'left click must be a canvas callout'
 assert labels[0].arrow_patch is not None
 if os.environ.get('DSA_QA_SCREENSHOT'):w.grab().save(os.environ['DSA_QA_SCREENSHOT'].replace('.png','_red.png'))
 w.escape_action();assert not any(a.get_visible() for a in w.reconstruction_overlay.labels)
 click(point);menu_action('忽略选中图形的重构',point)
 assert w.view_settings.reconstruction_ignored==( ('5/0',0), )
 assert not w.current_frame['reconstruction']['pieces']
 assert w.extreme_table.rowCount()==1
 saved=folder/'saved.dshift.json';w.write_project(saved)
 assert json.loads(saved.read_text())['ignored_reconstruction']['refs']==[['5/0',0]]
 w.load_project(saved);wait();assert w.view_settings.reconstruction_ignored==( ('5/0',0), )
 w.run_evaluation();wait()
 if not w.reconstruction_toggle.isChecked():w.reconstruction_toggle.setChecked(True)
 wait();w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw();point=(300.,0.)
 click(point);menu_action('取消忽略选中图形的重构',point)
 assert not w.view_settings.reconstruction_ignored and w.current_frame['reconstruction']['pieces']
 combo=w.setting_fields['reconstruction_style']
 for style in ('linear','arc','c1','linear','arc','c2'):
  combo.setCurrentIndex(combo.findData(style));app.processEvents();time.sleep(.015)
 wait()
 assert w.current_frame['reconstruction']['parameters']['connection_style']=='c2'
 assert not w._tasks and not w.progress.isVisible(),(w._tasks,w.runtime_view.toPlainText()[-5000:])
 assert w.problem_filter.isChecked() and w.extreme_table.rowCount()>1
 angles=w.setting_fields['reconstruction_boundary_mode'];assert [angles.itemText(i) for i in range(angles.count())]==['垂直','重构区优先','两侧平均']
 angles.setCurrentIndex(angles.findData('wide'));wait()
 w.setting_fields['drc_tolerance_um'].setValue(12);wait()
 assert w.reconstruction_yield.evidence['complete']
 assert all(status=='green' for q,status,g in w.reconstruction_overlay.zone_views.values()),[(q['problem_reasons'],s) for q,s,g in w.reconstruction_overlay.zone_views.values()]
 assert w.extreme_table.rowCount()==1
 # Actual native save flow and frozen completed reconstruction: no rerouting.
 target=folder/'cached_ABC.dxf'
 from die_shift_studio.qt_compat import ACCEPTED
 def accept(dialog):dialog.selectFile(str(target));return ACCEPTED
 from die_shift_studio.export_ui import export_dxf
 with patch('die_shift_studio.export_ui.run_dialog',side_effect=accept),patch('die_shift_studio.reconstruction.Reconstructor.frame',side_effect=AssertionError('export recomputed a completed frame')):
  export_dxf(w,regions=True);wait()
 assert target.is_file() and target.stat().st_size>1000
 w.write_project(saved);w.load_project(saved);wait();assert w.view_settings.reconstruction_boundary_mode=='wide'
 if os.environ.get('DSA_QA_SCREENSHOT'):w.grab().save(os.environ['DSA_QA_SCREENSHOT'])
 print('GUI_V40_PASS: rule colours/filter, shared canvas callouts, ignored source Jobs, cancelled rapid styles, boundary modes and no-reroute streaming export')
finally:w.close();app.processEvents();tmp.cleanup()
