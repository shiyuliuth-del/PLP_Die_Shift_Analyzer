"""Real Qt ruler shortcuts, strict rotated boxes, paging and diagnostic preview."""
import os,sys,time,json,tempfile
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np,gdstk,shapely
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt
from matplotlib.backend_bases import MouseEvent
from qt_shutdown import close_window
from test_v49_reconstruction import V49ReconstructionTests
try:from PySide6.QtTest import QTest
except ImportError:from PyQt5.QtTest import QTest
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1600,1000);w.show();errors=[];w.error=lambda x:errors.append(str(x))
def wait(limit=120):
 end=time.monotonic()+limit
 while time.monotonic()<end:
  app.processEvents()
  if not any((w._tasks,w.worker,w.preview_workers,w.preview_pending,w.filter_job,w.transition.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending,w.reconstruction_yield.worker,w.reconstruction_yield.pending,w.reconstruction_yield.timer.isActive(),w.coverage_timer.isActive())):return
  time.sleep(.005)
 raise AssertionError(w.runtime_view.toPlainText()[-5000:])
def event(name,point,button=1,step=0):
 x,y=w.ax.transData.transform(point);e=MouseEvent(name,w.canvas,x,y,button=button,step=step);w.canvas.callbacks.process(name,e);app.processEvents()
def key(k,modifier=None):
 w.canvas.setFocus();app.processEvents();QTest.keyClick(w.canvas,getattr(getattr(Qt,'Key',Qt),'Key_'+k),modifier or getattr(getattr(Qt,'KeyboardModifier',Qt),'NoModifier'));app.processEvents()
try:
 e,data,case=V49ReconstructionTests().fixture();roles=e.roles;p=e.p;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for layer,rows in data.layers.items():
  l,d=map(int,layer.split('/'));cell.add(*(gdstk.Polygon(xy,layer=l,datatype=d) for xy in rows))
 source=folder/'case.gds';lib.write_gds(source)
 job=folder/'case.dshift.json';job.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings=dict(animation_enabled=False,reconstruction_boundary_mode='perpendicular'),overlays={})))
 w.load_project(job);wait();w.run_evaluation();wait();w.problem_filter.setChecked(False);wait();w.canvas.draw()
 assert w.show_issues.text()=='显示DRC问题'
 assert w.extreme_table.columnCount()==4 and not w.extreme_table.verticalHeader().isHidden()
 assert '共 ' in w.page_label.text() if hasattr(w,'page_label') else True
 key('K');assert w.rulers.active
 event('button_press_event',(0,80));event('motion_notify_event',(30,120));event('button_release_event',(30,120))
 assert len(w.rulers.rows)==1;assert abs(np.linalg.norm(np.subtract(*w.rulers.rows[0]))-50)<1e-5
 key('Escape');assert not w.rulers.active and len(w.rulers.rows)==1
 w.write_project(job);w.load_project(job);wait();assert len(w.rulers.rows)==1
 key('K',getattr(getattr(Qt,'KeyboardModifier',Qt),'ShiftModifier'));assert not w.rulers.rows
 key('K');event('button_press_event',(0,80));event('button_release_event',(0,80));event('button_press_event',(30,120));event('button_release_event',(30,120));assert len(w.rulers.rows)==1
 key('K',getattr(getattr(Qt,'KeyboardModifier',Qt),'ControlModifier'));assert not w.rulers.rows
 key('Escape');w.reconstruction_toggle.setChecked(True);wait();w.region_move.setChecked(True);w.canvas.draw()
 handle=w.current_frame['reconstruction']['pieces'][0]['moved_cut'].centroid.coords[0]
 event('button_press_event',handle);assert w.region_select.state is not None and w.cut_drag.state is None
 key('Escape');w.region_move.setChecked(True);w.canvas.draw()
 event('button_press_event',(80,-60));event('motion_notify_event',(220,70));limits=(w.ax.get_xlim(),w.ax.get_ylim())
 event('scroll_event',(220,70),button='up',step=10)
 assert w.region_select.state is not None and np.isclose(w.region_select.state['angle'],-np.pi/9)
 np.testing.assert_allclose([w.ax.get_xlim(),w.ax.get_ylim()],limits)
 expected=w.region_select.vertices().copy();event('button_release_event',(220,70));wait()
 stored=w.view_settings.reconstruction_boxes[-1];np.testing.assert_allclose(stored[1],expected,atol=.01)
 for q in w.current_frame['reconstruction']['pieces']:
  source=w.geometry_cache.layers[q['layer']]['geometry'][q['index']]
  wanted=source.intersection(shapely.Polygon(stored[1]))
  if not wanted.is_empty:assert q['nominal_regions']['A'].symmetric_difference(wanted).area<1.e-6,(q['index'],q['nominal_regions']['A'].wkt,wanted.wkt)
 key('Escape');assert w.region_select.mode is None
 # Paging must never truncate the full canvas catalogue or change the row's source.
 rows=[dict(kind='线宽不足',layer='5/0',layers=('5/0',),point=[i,0],geometry=shapely.box(i,0,i+.1,1),required_um=10) for i in range(600)]
 w.display_issues=rows;w.show_issue_page(0);assert w.issue_table.rowCount()==256
 w.issue_next.click();assert w.issue_table.verticalHeaderItem(0).text()=='257';assert w.filtered_issues[0] is rows[256]
 w.issue_next.click();assert w.issue_table.rowCount()==88 and w.issue_summary.text()=='第 3/3 页'
 assert len(w.display_issues)==600
 w.show_issues.setChecked(True);w.view_bounds=(-1,-1,601,2);w.drc_overlay.draw();assert len(w.rendered_issues)==600
 assert not errors,errors
 assert 'Traceback' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-6000:]
 print('GUI_V49_PASS rulers/shortcuts/project restoration, rotated exact box and paged DRC source identities',flush=True)
finally:close_window(app,w);tmp.cleanup()
