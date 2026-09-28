"""Actual Qt toggles, parameter round trips and animation reuse."""
import os,sys,time,tempfile,json
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.space_map import space_maps
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.transition.timer.isActive(),w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive())):break
        time.sleep(.002)
    else:raise AssertionError('worker timeout')
    assert not errors,errors
def pose(z):w.manual_states=z.copy();w.request_preview(direct=True);wait()
w.load_project(ROOT/'Jobs/示例G_小偏移与三段追溯.dshift.json');wait();w.run_evaluation();wait()
a=np.array([[1.,1,0],[0,35,0]]);b=np.array([[2.,2,0],[0,34,0]])
pose(a);w.deformation_toggle.setChecked(True);w.drc_toggle.setChecked(True);wait()
with patch('die_shift_studio.probability_overlay.probability_maps',wraps=probability_maps) as prob,patch('die_shift_studio.space_map.space_maps',wraps=space_maps) as space:
    w.probability_toggle.setChecked(True);wait();w.space_toggle.setChecked(True);wait()
    assert prob.call_count==1 and space.call_count==1,(prob.call_count,space.call_count)
    for _ in range(2):
        w.probability_toggle.setChecked(False);w.space_toggle.setChecked(False);w.drc_toggle.setChecked(False);wait()
        w.deformation_toggle.setChecked(False);wait();w.deformation_toggle.setChecked(True);wait()
        w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);w.drc_toggle.setChecked(True);wait()
    assert prob.call_count==1 and space.call_count==1,(prob.call_count,space.call_count)
    # Same numeric condition after an edit reuses the overlay too.
    w.fields['annulus_um'].setValue(13);wait();w.fields['annulus_um'].setValue(12);wait()
    assert prob.call_count==2 and space.call_count==2
    assert len(w.probability_artists)>0 and w.space_artists
w.setting_fields['animation_enabled'].setChecked(True);wait();pose(a)
w.manual_states=b.copy();w.request_preview();wait();assert w._prepared_motion['streamed']
pose(a);deformer=w.engine.deformer
with patch.object(deformer,'frame',side_effect=AssertionError('animation recomputed geometry')):
    start=time.perf_counter();w.manual_states=b.copy();w.request_preview();wait();replay_ms=(time.perf_counter()-start)*1000
assert w._prepared_motion['cached'];np.testing.assert_array_equal(w.shown_states,b)
old_values=w.session_values;old_engine=w.engine;old_path=old_values.root
w.run_evaluation();wait();assert old_values.closed and old_engine.frames.closed and not old_path.exists()
w.deformation_toggle.setChecked(False);w.probability_toggle.setChecked(False);w.space_toggle.setChecked(False);wait()
new_path=w.session_values.root;w.close();app.processEvents();wait();assert not new_path.exists()
out=dict(status='PASS',probability_calls_with_parameter_roundtrip=2,space_calls_with_parameter_roundtrip=2,animation_replay_ms=replay_ms,animation_recomputed_deformation_calls=0,manual_evaluation_clears_cache=True,close_removes_cache=True)
(ROOT/'results/gui_V13.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out));tmp.cleanup()
