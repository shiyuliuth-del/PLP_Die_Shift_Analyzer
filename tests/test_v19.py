"""Pinned session caches and independently validated partial copper adaptation."""
import unittest,json,math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import shapely
from die_shift_studio.session_cache import ProjectCacheRegistry,SessionCache,logical_fingerprint
from die_shift_studio.settings import ViewSettings

class V19Tests(unittest.TestCase):
 def test_logical_key_independent_of_aliasing_order_and_signed_zero(self):
  a={'z':[1.,-0.],'a':np.array([3,4])}
  b={'a':[3,4],'z':[1.,0.]}
  self.assertEqual(logical_fingerprint([a,a]),logical_fingerprint([b,dict(b)]))
 def test_disk_lookup_uses_equal_key_not_pickle_alias_pattern(self):
  value='long-key-'+''.join(map(str,range(300)));a=(value,value);b=(value,value.encode().decode())
  self.assertEqual(a,b);self.assertIsNot(b[0],b[1]);cache=SessionCache(0)
  try:
   cache[a]=123;self.assertEqual(cache[b],123);cache[b]=456
   self.assertEqual(cache[a],456);self.assertEqual(len(list(cache.root.iterdir())),1)
   del cache[b];self.assertFalse(list(cache.root.iterdir()))
  finally:cache.close()
 def test_pinned_zero_survives_shared_project_limit_and_disk_spill(self):
  registry=ProjectCacheRegistry();g=registry.get('A',3);a=SessionCache(0,group=g);b=SessionCache(0,group=g)
  try:
   a.pin('zero');a['zero']={'states':[0,0,0]}
   for i in range(20):(a if i%2 else b)[i]=np.arange(20)+i
   self.assertEqual(len(g.recent),4);self.assertEqual(a['zero']['states'],[0,0,0]);self.assertIn('zero',a.memory)
   self.assertEqual(g.bytes,sum(p.stat().st_size for c in g.caches.values() for p in c.root.iterdir()))
   other=registry.get('B',3);other.values['result']=b'Other';self.assertGreater(registry.statistics('A')[1],g.bytes)
   registry.get('A',3);self.assertEqual(a['zero']['states'],[0,0,0])
   retired=registry.retire('A');self.assertEqual(registry.statistics('B')[1],retired.bytes+other.bytes)
   size=retired.bytes;a['stale worker']=123;self.assertEqual(retired.bytes,size)
   retired.clear();self.assertEqual(registry.statistics('B')[1],other.bytes)
   registry.rekey('B','saved project');self.assertIs(registry.get('saved project'),other)
  finally:
   paths=[c.root for group in list(registry.groups.values())+registry.retired for c in group.caches.values()]
   registry.close();self.assertTrue(all(not p.exists() for p in paths))
 def test_cancelled_animation_resumes_only_missing_frames_and_replays(self):
  from die_shift_studio.prepared_motion import prepare_motion
  calls=[];published=[];cache={};start=np.zeros((1,3));end=np.array([[35.,0.,0.]])
  def frame(z,*a,**kw):calls.append(z.copy());return {'states':z.copy()}
  e=SimpleNamespace(signature='A',cache_key=lambda:(),bound=SimpleNamespace(owner_radii=np.array([500.])),frame=frame)
  def cancel(n,t):
   if len(calls)==2:raise InterruptedError('user switches state')
  cfg=ViewSettings(animation_duration_ms=1000,animation_fps=30)
  with self.assertRaises(InterruptedError):prepare_motion(e,start,end,cfg,1000,cache,cancel)
  self.assertEqual(len(next(iter(cache.values()))['frames']),2)
  result=prepare_motion(e,start,end,cfg,1000,cache,lambda *_:None)
  self.assertEqual(len(calls),result['count'])
  with patch.object(e,'frame',side_effect=AssertionError('recomputed')):
   reuse=prepare_motion(e,start,end,cfg,1000,cache,lambda *_:None,published.append)
  self.assertTrue(reuse['cached']);self.assertEqual(len(published),result['count'])
if __name__=='__main__':unittest.main()
