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
    else:raise AssertionError('V18 Qt timeout')
    assert not errors,errors
w.load_project(ROOT/'Jobs/示例K_中段连接与平滑孔盘.dshift.json');wait();w.run_evaluation();wait()
w.manual_states=np.array([[-35.,-35.,-math.pi/180]]);w.request_preview(direct=True);wait();w.deformation_toggle.setChecked(True);wait()
frame=w.current_frame;d=frame['deformation'];assert d['accepted_count']==2 and not d['violations']
values=d['replacements']['5/0'];assert {v['mode'] for v in values}=={'axial','pad_envelope'}
assert any(not v['adaptation'].is_empty for v in values)
assert any(a.get_visible() for a in w.deformation_artists['5/0'])
w.visible_boxes['5/0'].setChecked(False);wait();assert not any(a.get_visible() for a in w.deformation_artists['5/0'])
w.visible_boxes['5/0'].setChecked(True);wait();w.fit_plot();wait()
w.grab().save(str(ROOT/'figures/software_V18_middle_pad.png'))
start=w.shown_states.copy();end=np.array([[-25.,-25.,-.5*math.pi/180]])
frames=[];original=w.show_motion_frame
def track(f):
    np.testing.assert_array_equal(f['states'],f['deformation']['states']);frames.append(f['states'].copy());original(f)
w.show_motion_frame=track;w.manual_states=end.copy();w.request_preview();wait();assert len(frames)>=2
first_count=len(frames);w.manual_states=start.copy();w.request_preview(direct=True);wait();frames.clear()
w.manual_states=end.copy();w.request_preview();wait();assert w._prepared_motion['cached'] and len(frames)>=2
w.show_motion_frame=original
w.tabs.setCurrentWidget(w.detail);wait();text=w.detail.toPlainText();assert '中段连接' in text and '平滑凸包络' in text
w.grab().save(str(ROOT/'figures/software_V18_report.png'))
result=dict(status='PASS',accepted=2,modes=sorted(v['mode'] for v in values),dashed_middle_band=True,first_animation_frames=first_count,cached_animation_frames=len(frames),hidden_layer_hides_deformation=True,report_formula_rendered=True)
(ROOT/'results/gui_V18.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
w.close();app.processEvents();tmp.cleanup();print('GUI_V18_PASS',result)
