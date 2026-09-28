"""Live coverage, keyboard navigation, animation controls and validated deform UI."""
import os,sys,time,json,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PyQt5.QtTest import QTest
from die_shift_studio.gui import MainWindow,W,QtCore
from die_shift_studio.qt_compat import Qt
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda x:errors.append(str(x));metrics={}
def busy():return w.worker or w.preview_worker or w.preview_pending or w.motion_worker or w.motion_pending or w.coverage_timer.isActive() or w._coverage_pending or w.transition.timer.isActive() or w.viewport_timer.isActive() or w.issue_navigation.isActive()
def wait(timeout=70):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  if not busy():break
  time.sleep(.002)
 assert not busy(),'worker timeout';assert not errors,errors;app.processEvents()
def wait_motion():
 end=time.monotonic()+5
 while time.monotonic()<end:
  app.processEvents()
  if w.transition.timer.isActive():return
  time.sleep(.002)
 raise AssertionError('No animation started')
def shot(name):
 w.canvas.draw();w.grab().save(str(ROOT/'figures'/('software_V12_'+name+'.png')))
w.load_project(ROOT/'Jobs/示例E_连续型变与约束.dshift.json');wait();order=w.ordered_layers().copy()
assert order[2]=='127/0' and order[5]=='128/0';assert w.run_button.text()=='方案评估'
assert not w.deformation_toggle.isChecked()
w.layer_moved();wait();assert w.ordered_layers()==order
w.run_evaluation();wait();assert w.ordered_layers()==order
r=w.result;w.target_sigma.setCurrentText('1');wait();assert w.result is not r and w.result['parameters']['target_nsigma']==1
# Repeated target edits while a worker is active coalesce to the newest value.
w.target_sigma.setCurrentText('3');w.refresh_coverage();app.processEvents();w.target_sigma.setCurrentText('12');wait()
assert w.result['parameters']['target_nsigma']==12 and not w.dirty
w.target_sigma.setCurrentText('6');wait()
# Both original and new numerical results preserve exact user row order.
assert w.ordered_layers()==order
w.deformation_toggle.setChecked(True);wait();w.extreme_table.setCurrentCell(1,0);wait();f=w.current_frame
assert f['deformation']['accepted_count']>=1
assert w.deformation_table.rowCount()>0 and '原图形线宽不足' in str(f['deformation']['diagnostics'])
for key,values in f['deformation']['replacements'].items():
 hidden={i for q in values for i in q['indices']};drawn=set(np.frombuffer(w.layer_artists[key]._visible_ids,dtype=int))
 assert not hidden&drawn
 assert all(a.get_visible() for a in w.deformation_artists[key])
 w.layer_styles[key]={'alpha':.22};w.plot_layout(preserve=True);assert all(a.get_alpha()==.22 for a in w.deformation_artists[key])
 w.layer_styles.pop(key);w.plot_layout(preserve=True)
 # Picking uses the displayed deformed geometry, rather than its deleted source.
 q=values[0];point=q['dark'].representative_point();hits=w.selection.hits(point.buffer(.001),point=True)
 assert (key,q['indices'][0]) in hits
 w.visible_boxes[key].setChecked(False);assert not any(a.get_visible() for a in w.deformation_artists[key])
 w.visible_boxes[key].setChecked(True)
w.set_view([260,60],1120,450);wait();shot('deformation')
metrics['deformation_accepted']=f['deformation']['accepted_count'];metrics['deformation_diagnostics']=f['deformation']['diagnostics']
# Appearance changes immediately, then contour method invalidates the preview.
w.setting_fields['deformation_method'].setCurrentIndex(w.setting_fields['deformation_method'].findData('arc'));wait();assert w.engine.deformer.settings.deformation_method=='arc'
w.deformation_toggle.setChecked(False);wait();assert not any(a.get_visible() for pair in w.deformation_artists.values() for a in pair)
# Deformation playback uses only poses already validated in the background.
w.deformation_toggle.setChecked(True);wait();w.setting_fields['animation_enabled'].setChecked(True)
live=[];plot=w.plot_layout
def inspect_motion(*args,**kwargs):
 plot(*args,**kwargs)
 if getattr(w,'_in_animation',False) and w.current_frame and 'deformation' in w.current_frame:
  f=w.current_frame;np.testing.assert_array_equal(f['states'],f['deformation']['states']);live.append(f['states'].copy())
w.plot_layout=inspect_motion;w.extreme_table.setCurrentCell(8,0);wait();w.plot_layout=plot
assert len(live)>=1 and w._prepared_motion and w._prepared_motion['streamed']
metrics['validated_deformation_display_frames']=len(live);metrics['prepared_deformation_frames']=w._prepared_motion['count']
w.setting_fields['animation_enabled'].setChecked(False);w.deformation_toggle.setChecked(False);wait()
# Keyboard up/down navigates the insufficiency list, including retained binary pose.
w.load_project(ROOT/'Jobs/示例D_芯片外扇出.dshift.json');wait();w.run_evaluation();wait()
w.manual_states=np.array([[65.,0,0],[65.,0,0]]);w.request_preview();wait();assert w.issue_table.rowCount()>=2
w.issue_table.setFocus();w.issue_table.setCurrentCell(0,0);wait();QTest.keyClick(w.issue_table,Qt.Key_Down);wait();assert w.issue_table.currentRow()==1
np.testing.assert_allclose([np.mean(w.ax.get_xlim()),np.mean(w.ax.get_ylim())],w.filtered_issues[1]['point'])
QTest.keyClick(w.issue_table,Qt.Key_Up);wait();assert w.issue_table.currentRow()==0
np.testing.assert_allclose([np.mean(w.ax.get_xlim()),np.mean(w.ax.get_ylim())],w.filtered_issues[0]['point'])
# Instant disable completes the destination, not an abandoned intermediate pose.
w.fit_plot();wait();w.setting_fields['animation_enabled'].setChecked(True)
w.manual_states=np.array([[-65.,0,0],[0,0,0]]);w.request_preview();wait_motion();w.setting_fields['animation_enabled'].setChecked(False)
assert not w.transition.timer.isActive();np.testing.assert_allclose(w.shown_states,w.manual_states);wait()
w.setting_fields['animation_enabled'].setChecked(True);w.manual_states=np.array([[65.,0,0],[0,0,0]]);w.request_preview();wait_motion()
w.setting_fields['animation_fps'].setValue(60);assert w.transition.timer.interval()<=17
w.setting_fields['animation_speed'].setValue(5);assert w.view_settings.animation_speed==5;wait();np.testing.assert_allclose(w.shown_states,w.manual_states)
w.setting_fields['animation_enabled'].setChecked(False);wait()
# Static probability raster cache agrees with normal Matplotlib draws.
w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait();w.fit_plot();wait();w.render_scene();a=np.asarray(w.canvas.buffer_rgba()).copy();w.canvas.draw();b=np.asarray(w.canvas.buffer_rgba()).copy()
x0,y0,x1,y1=map(int,w.ax.bbox.extents);h=b.shape[0];delta=np.max(abs(a.astype(float)-b.astype(float)),axis=2)[h-y1+4:h-y0-4,x0+4:x1-4]
metrics['static_overlay_pixel_difference_fraction']=float(np.mean(delta>8))
assert np.mean(delta>8)<.007
# Culling only the drawing, never the physical result or distribution data.
prob=w.probability_data;w.set_view([0,1000],200,150);wait();assert any(not a.get_visible() for a in w.probability_artists)
w.set_view([2100,-900],200,150);wait();assert w.probability_data is prob
assert any(a.get_visible() and a._world_bounds[0]>1500 for a in w.probability_artists)
# Coverage edit preserves manual preview and updates the statistics and fields.
pose=w.shown_states.copy();old=w.result;w.target_sigma.setCurrentText('∞');wait();np.testing.assert_allclose(w.shown_states,pose)
assert w.result is not old and w.result['contact_analysis']['mode']=='binary'
w.fit_plot();wait();shot('live_overlays');metrics['status']='PASS'
(ROOT/'results/gui_legacy_regression_V12.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n');w.close();app.processEvents();tmp.cleanup();print(json.dumps(metrics,ensure_ascii=False,indent=2))
