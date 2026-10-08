"""Real Qt V17: cold/cached/streamed deformed frames, synced legend, signed DRC."""
import os,sys,time,tempfile,json
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1640,1050);w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
 end=time.monotonic()+240
 while time.monotonic()<end:
  app.processEvents()
  if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):break
  time.sleep(.003)
 else:raise AssertionError('V17 Qt timeout')
 assert not errors,errors
w.load_gds(ROOT/'examples/示例G_小偏移与三段追溯.gds');wait()
assert w.view_settings.animation_enabled
assert [w.fields[k].value() for k in ('line_width_um','line_space_um','wafer_line_width_um','wafer_line_space_um','annulus_um')]==[15]*5
assert '良率要求' in [g.title() for g in w.findChildren(W.QGroupBox)]
assert '良率级别' in [l.text() for l in w.findChildren(W.QLabel)]
w.load_project(ROOT/'Jobs/示例G_小偏移与三段追溯.dshift.json');wait();w.run_evaluation();wait()
def pose(z):w.manual_states=z.copy();w.request_preview(direct=True);wait()
a=np.zeros((2,3));b=np.array([[1.,1,0],[0,35,0]])
pose(a);w.deformation_toggle.setChecked(True);wait();w.setting_fields['animation_enabled'].setChecked(True);wait()
w.setting_fields['animation_duration_ms'].setValue(600);w.setting_fields['animation_speed'].setValue(1);wait()
original=w.show_motion_frame
# Store observations separately from the callback name.
frames=[]
def track(frame):
 np.testing.assert_array_equal(frame['states'],frame['deformation']['states'])
 frames.append((time.perf_counter(),frame['states'].copy()));original(frame)
w.show_motion_frame=track
w.manual_states=b.copy();w.request_preview();wait()
assert w._prepared_motion['prepared'] and not w._prepared_motion['streamed'] and len(frames)>=2
cold_count=len(frames);pose(a);frames.clear();deformer=w.engine.deformer
with patch.object(deformer,'frame',side_effect=AssertionError('cached deformation was recomputed')):
 w.manual_states=b.copy();w.request_preview();wait()
assert w._prepared_motion['cached'] and len(frames)>=2
cached_count=len(frames);np.testing.assert_array_equal(w.shown_states,b)
# Stream mode also plays already-computed frames rather than replacing them at once.
w.setting_fields['animation_strategy'].setCurrentIndex(w.setting_fields['animation_strategy'].findData('stream'));wait();pose(a);frames.clear()
w.manual_states=b.copy();w.request_preview();wait();assert w._prepared_motion['streamed'] and len(frames)>=2
stream_count=len(frames);w.show_motion_frame=original
# Same scale panning follows DIE-label visibility, not exact reset coordinates.
w.fit_plot();w.plot_layout(preserve=True);wait()
for delta,zoom in ((0,1),(80,1),(0,.5),(0,1)):
 home=w._default_view;w.set_view([np.mean(home[0])+delta,np.mean(home[1])],np.ptp(home[0])*zoom,np.ptp(home[1])*zoom);w.plot_layout(preserve=True);wait()
 assert w.legend.get_visible()==any(t.get_visible() for t in w.die_labels)
w.visible_boxes['128/0'].setChecked(False);wait();assert not w.legend.get_visible() and not any(t.get_visible() for t in w.die_labels)
w.visible_boxes['128/0'].setChecked(True);wait()
# Tolerance updates the actual DRC and deformation cache; reloading restores it.
w.setting_fields['drc_tolerance_um'].setValue(.25);wait()
assert w.engine.deformer.rule_width==w.params().line_width_um-.25
assert w.current_frame['drc_rules']['tolerance_um']==.25
with patch.object(W.QInputDialog,'getText',return_value=('V17回归',True)):w.project_path=None;w.save_project()
assert json.loads(w.project_path.read_text())['settings']['drc_tolerance_um']==.25
w.probability_toggle.setChecked(True);wait();data=w.probability_data
from matplotlib.colors import PowerNorm
rgba=w.probability_colormap()(PowerNorm(w.view_settings.probability_gamma,0,1)(.001))
assert rgba[3]>.2 # visible tails without changing the computed values
for palette in ('teal','amber','rose','magma','turbo'):
 w.setting_fields['probability_cmap'].setCurrentIndex(w.setting_fields['probability_cmap'].findData(palette));wait();assert w.probability_data is data
w.setting_fields['probability_cmap'].setCurrentIndex(w.setting_fields['probability_cmap'].findData('blue'));wait()
w.grab().save(str(ROOT/'figures/software_V17_preview.png'))
w.controls.setCurrentIndex(2);w.grab().save(str(ROOT/'figures/software_V17_settings.png'))
w.tabs.setCurrentWidget(w.detail);wait();assert 'DRC正负容差' in w.detail.toPlainText() and '0.25' in w.detail.toPlainText()
w.grab().save(str(ROOT/'figures/software_V17_report.png'))
w.close();app.processEvents();wait();tmp.cleanup()
result=dict(status='PASS',cold_frames=cold_count,cached_frames=cached_count,stream_frames=stream_count,cache_recomputed_geometry=0,legend_label_sync=True,signed_tolerance_roundtrip=True,visible_tail_alpha=float(rgba[3]),palettes_reuse_probabilities=True)
(ROOT/'results/gui_V17.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('GUI_V17_PASS',result)
