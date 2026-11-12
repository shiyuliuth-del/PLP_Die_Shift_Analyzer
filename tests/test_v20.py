"""V20: original copper motion, reusable reverse animation, load admission."""
import unittest,math,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import Point,box
from die_shift_studio.settings import ViewSettings

class V20Tests(unittest.TestCase):
 def test_reverse_animation_uses_exact_existing_validated_frames(self):
  from die_shift_studio.prepared_motion import prepare_motion
  calls=[];cache={};start=np.zeros((1,3));end=np.array([[35.,-35.,math.pi/180]])
  def frame(z,*a,**kw):calls.append(z.copy());return dict(states=z.copy())
  e=SimpleNamespace(signature='pose',cache_key=lambda:(),bound=SimpleNamespace(owner_radii=np.array([500.])),frame=frame)
  cfg=ViewSettings();forward=prepare_motion(e,start,end,cfg,1000,cache,lambda *_:None)
  with patch.object(e,'frame',side_effect=AssertionError('Reverse path recomputed')):
   reverse=prepare_motion(e,end,start,cfg,1000,cache,lambda *_:None)
  self.assertTrue(reverse['cached']);self.assertEqual(len(calls),forward['count'])
  for (f,a),(g,b) in zip(forward['frames'],reversed(reverse['frames'])):
   self.assertIs(a,b);self.assertAlmostEqual(f+g,1.)
 def test_parallel_overlay_requests_compute_once(self):
  from concurrent.futures import ThreadPoolExecutor
  from die_shift_studio.session_cache import SessionCache
  import threading
  cache=SessionCache();entered=threading.Event();release=threading.Event();calls=[]
  def compute():calls.append(1);entered.set();release.wait(3);return 42
  try:
   with ThreadPoolExecutor(2) as pool:
    a=pool.submit(cache.get_or_compute,'overlay',compute);self.assertTrue(entered.wait(2))
    b=pool.submit(cache.get_or_compute,'overlay',compute);release.set()
    self.assertEqual(a.result(),42);self.assertEqual(b.result(),42);self.assertEqual(len(calls),1)
  finally:release.set();cache.close()
 def test_load_admission_and_per_job_budget(self):
  from die_shift_studio.scheduling import admission,job_budget,constrain
  low=dict(cpu=8,available_memory_mb=8000,workers=6,process_workers=4,cpu_percent=15)
  self.assertTrue(admission(1,300,low)['start']);self.assertFalse(admission(2,300,low)['start'])
  for change in ({'cpu_percent':95},{'cpu_percent':None},{'available_memory_mb':600},{'cpu':4}):
   self.assertFalse(admission(1,300,dict(low,**change))['start'])
  with job_budget(2,1):self.assertEqual((constrain(low)['workers'],constrain(low)['process_workers']),(2,1))
  self.assertEqual(constrain(low)['workers'],6)
if __name__=='__main__':unittest.main()
