"""V45 current-plane DRC, lazy analyses, compact controls and manual pose dialog."""
import os,sys,time,json,tempfile
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
import numpy as np,gdstk
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_v38 import fixture
from die_shift_studio.gui import MainWindow,W
from qt_shutdown import close_window
from die_shift_studio.core import Parameters
from die_shift_studio.current_drc import locations
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1540,980);w.show();errors=[];w.error=lambda x:errors.append(str(x))
def wait(timeout=180):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  busy=(w._tasks,w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.coverage_timer.isActive(),w.transition.timer.isActive(),w.viewport_timer.isActive(),w.reconstruction_yield.worker,w.reconstruction_yield.pending,w.reconstruction_yield.timer.isActive(),w.width_yield.worker,w.width_yield.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending,w.constraint_feedback.timer.isActive(),w.filter_job,w.analysis.worker)
  if not any(busy):break
  time.sleep(.005)
 else:raise AssertionError(('timeout',w.log_bus.text()[-6000:]))
 assert not errors,errors
 assert '[ERROR]' not in w.log_bus.text(),w.log_bus.text()[-6000:]
try:
 assert [w.tabs.tabText(i) for i in range(w.tabs.count())]==['结果图形','结果分析']
 assert [w.preview_tabs.tabText(i) for i in range(w.preview_tabs.count())]==['极端情况','信息']
 assert w.layer_table.columnCount()==5 and w.layer_table.horizontalHeaderItem(4).text()=='补偿'
 assert w.gds_label.isReadOnly()
 assert not any(q.text()=='DRC问题' for q in w.findChildren(W.QCheckBox))
 assert w.space_toggle.text()=='避让空间' and w.manual_button.text()=='手动偏移'
 assert all(not q.isChecked() for q in w.setting_sections.values())
 assert all(q.isChecked() for q in w.process_sections.values())
 assert set(w.process_sections)=={'线宽线距','过孔包围量','芯片距离','其他'}
 assert all(q.styleSheet()==w.setting_sections['通用'].styleSheet() for q in w.process_sections.values())
 assert w.setting_fields['drc_tolerance_um'].value()==.1
 assert w.measured_output.memory.value()==0 and w.measured_output.threads.value()==0
 e,data,_=fixture();roles=e.roles;e.close()
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
 for key,rows in data.layers.items():
  layer,dt=map(int,key.split('/'));cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=dt) for xy in rows))
 source=folder/'case.gds';lib.write_gds(source)
 p=Parameters(line_width_um=40,line_space_um=15,annulus_um=10,moving_annulus_um=5,single_bound_x_um=25,single_bound_y_um=25)
 job=folder/'initial.dshift.json';job.write_text(json.dumps(dict(schema_version=5,gds_path=str(source),top_cell='TOP',roles=roles,layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),settings={'animation_enabled':False},overlays={'reconstruction':False})))
 with patch('die_shift_studio.result_analysis.calculate',side_effect=AssertionError('eager analysis')):
  w.load_project(job);wait();w.run_evaluation();wait()
  assert not w.result['analysis_complete'] and not w.result.get('safe_ranges')
  w.problem_filter.setCurrentIndex(0);wait()
  w.manual_button.click();app.processEvents();d=w.manual_preview
  assert d.isVisible() and len(d.rows)==1
  assert d.table.editTriggers()==getattr(W.QAbstractItemView,'EditTrigger',W.QAbstractItemView).NoEditTriggers
  d.rows[0][0].setValue(12);d.rows[0][1].setValue(7);d.rows[0][2].setValue(.5);d.preview_button.click();wait()
  np.testing.assert_allclose(w.shown_states,[[12,7,np.deg2rad(.5)]])
  d.restore_button.click();wait();np.testing.assert_allclose(w.shown_states,np.zeros((1,3)))
  d.hide()
  original=w.style_for('3/0');w.reconstruction_toggle.setChecked(True);wait()
  assert w.style_for('3/0')['color']=='#aaffff' and w.style_for('3/0')['alpha']==.3
  assert w.current_frame['drc']==w.current_frame['drc']
  w.show_issues.setChecked(True);wait();w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw()
  assert len(w.filtered_issues)>0
  assert len(w.filtered_issues)==w.issue_table.rowCount()==len(locations(w.current_frame['drc']))
  assert len(w.rendered_issues)==len(w.filtered_issues)
  count=w.issue_table.rowCount();before=w.current_frame
  w.visible_boxes['5/0'].setChecked(False);wait()
  assert w.issue_table.rowCount()==count and w.current_frame is before
  assert all('5/0' not in r.get('layers',()) for r in w.rendered_issues)
  w.visible_boxes['5/0'].setChecked(True);wait()
  w.show_issues.setChecked(False);wait();assert not w.drc_overlay.points.get_visible() and w.issue_table.rowCount()==count
  w.show_issues.setChecked(True);wait();assert w.drc_overlay.points.get_visible()
  if os.environ.get('DSA_QA_SCREENSHOT'):w.grab().save(os.environ['DSA_QA_SCREENSHOT'])
  w.reconstruction_toggle.setChecked(False);wait();assert w.style_for('3/0')==original
  w.setting_fields['drc_tolerance_um'].setValue(.15);wait()
  assert w.current_frame['drc_rules']['tolerance_um']==.15
 w.tabs.setCurrentWidget(w.analysis_page);wait();assert w.result['analysis_complete'] and w.result['safe_ranges']
 assert len(w.safe_range_figure.axes)>0
 w.tabs.setCurrentIndex(0);wait()
 with patch('die_shift_studio.result_analysis.calculate',side_effect=AssertionError('repeated analysis')):
  w.tabs.setCurrentWidget(w.analysis_page);wait()
 w.tabs.setCurrentIndex(0);w.manual_button.click();app.processEvents()
 w.manual_preview.rows[0][3].setValue(['custom',4,5]);w.manual_preview.rows[0][0].setValue(3);w.manual_preview.preview_button.click();wait()
 assert w.params().for_die(0).rotation_x_um==4
 saved=folder/'saved.dshift.json';w.write_project(saved)
 assert json.loads(saved.read_text())['saved_results'] is None, 'Changed pivots must not relabel an old assessment as current'
 w.manual_preview.hide();w.load_project(saved);wait()
 assert w.params().for_die(0).rotation_y_um==5
 assert w.params().minimum_area_um2==.5
 # A project/result reset cooperatively cancels an explicitly requested analysis.
 import threading
 started=threading.Event()
 w.run_evaluation();wait();w.tabs.setCurrentIndex(0)
 def slow(*args,**kwargs):
  started.set()
  for i in range(1000):kwargs['progress'](i%100,'取消测试');time.sleep(.003)
  raise AssertionError('obsolete analysis completed')
 with patch('die_shift_studio.core.evaluate',side_effect=slow):
  w.tabs.setCurrentWidget(w.analysis_page)
  end=time.monotonic()+5
  while not started.is_set() and time.monotonic()<end:app.processEvents();time.sleep(.005)
  assert started.is_set();w.drop_result();wait();assert w.result is None
 print('GUI_V45_PASS compact layout, lazy analysis, manual pose/pivot Jobs, current copper DRC parity, style restore, auto IPC budgets',flush=True)
finally:close_window(app,w);tmp.cleanup()
