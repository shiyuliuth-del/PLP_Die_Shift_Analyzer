"""Timestamped logs, individual bounds, latest generation, streaming and final geometry."""
import os,sys,time,json,tempfile,threading
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W,Worker
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda x:errors.append(str(x));metrics={}
def busy():return any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.coverage_timer.isActive(),w._coverage_pending,w.transition.timer.isActive(),w.viewport_timer.isActive()))
def wait(timeout=120,allow_errors=False):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  app.processEvents()
  if not busy():break
  time.sleep(.002)
 assert not busy(),'worker timeout'
 if not allow_errors:assert not errors,errors
 app.processEvents()
def shot(name):w.canvas.draw();w.grab().save(str(ROOT/'figures'/('software_V13_'+name+'.png')))
assert not w.runtime_toggle.isChecked() and w.runtime_view.isHidden()
w.runtime_toggle.setChecked(True);assert not w.runtime_view.isHidden()
print('V11 standard-output capture sentinel');sys.stderr.write('V11 standard-error capture sentinel\n');app.processEvents()
assert 'capture sentinel' in w.runtime_view.toPlainText();assert '[ERROR]' in w.runtime_view.toPlainText()
assert w.log_bus.path.exists() and 'standard-output' in w.log_bus.path.read_text()
w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait()
assert w.per_die_toggle.isChecked() and w.per_die_table.rowCount()==2
assert not w.fields['multi_bound_x_um'].isEnabled()
w.per_die_table.cellWidget(0,1).setValue(75);assert w.params().for_die(0).multi_bound_x_um==75
w.run_evaluation();wait();assert w.result['dies'][0]['input_bounds'][0]==75
# Preserve independent amplitudes and orders through a portable project save/load.
path=Path(tmp.name)/'Jobs'/'individual.dshift.json';w.write_project(path);w.load_project(path);wait()
assert w.per_die_values==[[75,5,0],[100,8,0]]
w.run_evaluation();wait();old=w.result
for text in ('1','12','2','6'):
 w.target_sigma.setCurrentText(text);w.refresh_coverage();app.processEvents()
wait();assert w.result is not old and w.result['parameters']['target_nsigma']==6 and not w.dirty
# Simulate an ordinary input edit while the automatic evaluation is in flight.
w.target_sigma.setCurrentText('2');w.refresh_coverage();w.per_die_table.cellWidget(1,1).setValue(99);wait()
assert w.result['parameters']['die_bounds'][1][0]==99 and w.result['parameters']['target_nsigma']==2
w.manual_states=np.array([[80.,0,0],[80.,0,0]]);w.request_preview();wait();assert w.current_frame['contact']['issues']
w.deformation_toggle.setChecked(True);wait();f=w.current_frame
assert f['deformation']['released_count']==1 and f['deformation']['accepted_count']==2
assert not f['contact']['issues'];metrics['iteration_history']=f['deformation']['iterations']
w.set_view([60,0],600,280);w.controls.setCurrentIndex(0);shot('iteration')
# Low coverage probability cloud stays complete, and caches match the last setting.
w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait()
for target in ('1','2','12'):
 w.target_sigma.setCurrentText(target);wait()
 assert w.result['parameters']['target_nsigma']==int(target)
 assert all(q['edge_probability']<1e-10 for q in w.probability_data['maps'])
 assert w.space_data['target_sigma']==int(target)
# Streaming publishes a validated state before the final state, with bounded queue.
w.setting_fields['animation_enabled'].setChecked(True);wait();frames=[];plot=w.plot_layout
start=time.perf_counter()
def record(*a,**kw):
 plot(*a,**kw)
 if getattr(w,'_in_animation',False) and w.current_frame:
  f=w.current_frame;np.testing.assert_array_equal(f['states'],f['deformation']['states']);frames.append((time.perf_counter()-start,f['states'].tolist()))
w.plot_layout=record;w.manual_states=np.array([[20.,0,0],[20.,0,0]]);w.request_preview();wait();w.plot_layout=plot
assert frames and w._prepared_motion['streamed'];np.testing.assert_array_equal(w.shown_states,w.manual_states)
metrics['first_validated_stream_frame_ms']=frames[0][0]*1000;metrics['streamed_frames']=len(frames)
# Live animation disable cancels the ongoing stream and converges to exact target.
w.manual_states=np.array([[80.,0,0],[80.,0,0]]);w.request_preview();app.processEvents();w.setting_fields['animation_enabled'].setChecked(False);wait()
np.testing.assert_array_equal(w.shown_states,w.manual_states)
# Deliberate worker failure is timestamped and always resets progress/controls.
w.start_worker(lambda progress:(_ for _ in ()).throw(ValueError('V11 handled-error sentinel')),lambda _:None)
wait(allow_errors=True);assert len(errors)==1 and 'handled-error sentinel' in errors[0];errors.clear()
assert w.progress.isHidden() and w.run_button.isEnabled();assert 'handled-error sentinel' in w.runtime_view.toPlainText()
# Pink final-layout violations at a stricter minimum angle, with source-layer visibility.
w.drc_toggle.setChecked(True);wait();w.setting_fields['deformation_min_angle_deg'].setValue(170);wait();assert w.current_frame['deformation']['violations']
assert w.violation_artist.get_visible() and w.violation_artist.get_paths()
w.drc_toggle.setChecked(False);assert not w.violation_artist.get_visible();w.drc_toggle.setChecked(True);wait()
w.controls.setCurrentIndex(2);shot('settings_violations')
# Blank row completes global iteration without reusing an invalid old probability lower bound.
w.setting_fields['deformation_min_angle_deg'].setValue(90);wait();w.select_preview(0,0);wait()
assert w.current_frame['nominal_global'] and w.current_frame['global_deformation']['states']
assert all(q.get('adaptive_unresolved') for q in w.current_frame['contact']['issues'])
w.tabs.setCurrentWidget(w.detail);app.processEvents();assert '逐芯片输入解析' in w.detail.toPlainText();assert '预演分析的迭代结果' in w.detail.toPlainText()
metrics['status']='PASS';(ROOT/'results/gui_legacy_V13.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
w.close();app.processEvents();tmp.cleanup();print(json.dumps(metrics,ensure_ascii=False,indent=2))
