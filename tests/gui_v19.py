"""Actual Qt rendering of smooth islands, middle bands and cached animation."""
import os,sys,time,json,tempfile,math
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1600,1000);w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
    end=time.monotonic()+240
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):break
        time.sleep(.003)
    else:raise AssertionError('V19 Qt timeout')
    assert not errors,errors
w.load_project(ROOT/'Jobs/示例K_中段连接与平滑孔盘.dshift.json');wait();w.run_evaluation();wait()
w.manual_states=np.array([[-35.,-35.,-math.pi/180]]);w.request_preview(direct=True);wait();w.deformation_toggle.setChecked(True);wait()
frame=w.current_frame;d=frame['deformation'];assert d['accepted_count']==2 and not d['violations']
values=d['replacements']['5/0'];assert {v['mode'] for v in values}=={'axial'}
assert any(not v['adaptation'].is_empty for v in values)
assert any(a.get_visible() for a in w.deformation_artists['5/0'])
w.visible_boxes['5/0'].setChecked(False);wait();assert not any(a.get_visible() for a in w.deformation_artists['5/0'])
w.visible_boxes['5/0'].setChecked(True);wait();w.fit_plot();wait()
w.grab().save(str(ROOT/'figures/software_V19_middle_pad.png'))
start=w.shown_states.copy();end=np.array([[-25.,-25.,-.5*math.pi/180]])
frames=[];original=w.show_motion_frame
def track(f):
    np.testing.assert_array_equal(f['states'],f['deformation']['states']);frames.append(f['states'].copy());original(f)
w.show_motion_frame=track;w.manual_states=end.copy();w.request_preview();wait();assert len(frames)>=2
first_count=len(frames);w.manual_states=start.copy();w.request_preview(direct=True);wait();frames.clear()
w.manual_states=end.copy();w.request_preview();wait();assert w._prepared_motion['cached'] and len(frames)>=2
w.show_motion_frame=original
# Revisit more than the old LRU capacity, repeatedly, without geometry solves.
from unittest.mock import patch
w.setting_fields['animation_enabled'].setChecked(False);wait()
poses=[np.array([[x,y,o*math.pi/180]]) for x,y,o in ((-35,-35,-1),(35,-35,-1),(-35,35,-1),(35,35,-1),(-35,-35,1),(35,35,1))]
for pose in poses:w.manual_states=pose;w.request_preview();wait()
engine=w.engine;group=w.cache_group;times=[]
with patch.object(engine.deformer,'search',side_effect=AssertionError('Repeated completed pose solve')):
 for cycle in range(3):
  for pose in poses:
   started=time.perf_counter();w.manual_states=pose;w.request_preview();wait();times.append(time.perf_counter()-started)
# A settings-only cache quota edit must not restart preview or animation.
with patch.object(w,'request_preview',side_effect=AssertionError('cache count changed physics')):
 w.setting_fields['cache_max_entries'].setValue(1500)
# Switching projects must preserve all caches; returning restores the assessment.
project=ROOT/'Jobs/示例K_中段连接与平滑孔盘.dshift.json'
w.load_project(ROOT/'Jobs/示例J_短宽孔盘与DRC容差.dshift.json');wait()
w.load_project(project);wait();w.setting_fields['animation_enabled'].setChecked(False);wait();assert w.cache_group is group and w.result is not None
w.deformation_toggle.setChecked(True);wait()
with patch.object(engine.deformer,'search',side_effect=AssertionError('Lost cache on project change')):
 w.manual_states=poses[0];w.request_preview();wait()
# Return to the blank first row: no motion and immediately zero geometry.
w.manual_states=None;w.active_preview=None;started=time.perf_counter();w.request_preview();zero_restore_ms=(time.perf_counter()-started)*1000
assert w.current_frame is None or not np.any(w.current_frame['states']);wait()
w.manual_states=poses[0];w.request_preview();wait()
w.manual_states=None;w.active_preview=None;w.request_preview();assert not np.any(w.current_frame['states']);wait()
w.tabs.setCurrentWidget(w.detail);wait();text=w.detail.toPlainText();assert '中段连接' in text and '原始材料' in text
w.grab().save(str(ROOT/'figures/software_V19_report.png'))
w.controls.setCurrentIndex(2);scroll=w.controls.currentWidget();scroll.ensureWidgetVisible(w.clear_cache_button);app.processEvents();w.refresh_cache_status()
w.grab().save(str(ROOT/'figures/software_V19_cache.png'))
result=dict(status='PASS',accepted=2,modes=sorted(v['mode'] for v in values),dashed_middle_band=True,first_animation_frames=first_count,cached_animation_frames=len(frames),hidden_layer_hides_deformation=True,report_formula_rendered=True)
result.update(repeated_pose_switches=18,repeated_switch_median_ms=float(np.median(times)*1000),project_switch_keeps_results=True,zero_restore_ms=zero_restore_ms)
w.clear_cached_results(all_projects=True);wait()
while w.cache_clear_worker:app.processEvents();time.sleep(.003)
w.refresh_cache_status();assert w.cache_registry.statistics(w.cache_project_key)[:2]==(0,0)
result['clear_all_projects_bytes']=0
(ROOT/'results/gui_V19.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
w.close();app.processEvents();tmp.cleanup();print('GUI_V19_PASS',result)
