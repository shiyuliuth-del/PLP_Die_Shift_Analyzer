"""Real Qt workers: high-load queue, low-load parallel admission, stale-result guard."""
import os,sys,time,json,tempfile,threading
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.fast_preview import PreviewEngine
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda e:errors.append(str(e))
def until(predicate,seconds=60):
 end=time.monotonic()+seconds
 while not predicate():
  app.processEvents();time.sleep(.004)
  if time.monotonic()>end:raise AssertionError('Scheduler Qt timeout')
 assert not errors,errors
w.load_project(ROOT/'Jobs/示例K_中段连接与平滑孔盘.dshift.json');until(lambda:not(w.worker or w.hardware_worker));w.setting_fields['animation_enabled'].setChecked(False)
profile=dict(cpu=8,available_memory_mb=8000,total_memory_mb=16000,workers=6,process_workers=4,cpu_percent=95.)
release=threading.Event();started=threading.Event();second=threading.Event();calls=[];original=PreviewEngine.frame
first=np.array([[17.,4.,0.]]);last=np.array([[-13.,6.,0.]])
def blocking(e,z,*args,**kwargs):
 calls.append(np.asarray(z).copy())
 if np.array_equal(z,first):started.set();assert release.wait(20),'Test gate not released'
 elif np.array_equal(z,last):second.set()
 return original(e,z,*args,**kwargs)
try:
 with patch('die_shift_studio.scheduling.load_snapshot',side_effect=lambda:dict(profile)),patch.object(PreviewEngine,'frame',blocking):
  w.manual_states=first;w.request_preview();until(started.is_set)
  w.manual_states=last;w.request_preview();app.processEvents();assert len(w.preview_workers)==1 and w.preview_pending is not None and not second.is_set()
  profile['cpu_percent']=10;w.launch_preview();until(second.is_set);assert len(w.preview_workers)==2
  until(lambda:w.current_frame is not None and np.array_equal(w.current_frame['states'],last));release.set()
  until(lambda:not(w.preview_worker or w.preview_pending));np.testing.assert_array_equal(w.current_frame['states'],last)
 with patch.object(PreviewEngine,'frame',side_effect=AssertionError('Cached state queued/recomputed')):
  w.manual_states=first;w.request_preview();np.testing.assert_array_equal(w.current_frame['states'],first)
 assert not errors,errors
 result=dict(status='PASS',high_load_queued=True,low_load_parallel=True,stale_result_did_not_overwrite=True,cached_return_without_worker=True,engine_count=len(w.session_engines))
 (ROOT/'results').mkdir(exist_ok=True);(ROOT/'results/gui_scheduler_V20.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
finally:
 release.set();until(lambda:not w.preview_workers);w.close();app.processEvents();tmp.cleanup()
