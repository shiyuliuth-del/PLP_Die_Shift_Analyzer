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
 def test_microhole_repair_is_bounded_by_both_dimensions(self):
  from die_shift_studio.deformation import DeformationPreview
  from shapely.geometry import box,Polygon
  # Area alone must never allow filling a long thin intentional slit.
  small=box(5,5,5.01,5.02);slit=box(10,5,30,5.001);large=box(40,40,50,50)
  source=Polygon(box(0,0,100,100).exterior,[g.exterior for g in (small,slit,large)])
  dp=object.__new__(DeformationPreview);dp.settings=ViewSettings();dp.base_sources={}
  c=SimpleNamespace(key='5/0',indices=(0,),poly=source)
  repaired,removed=dp.prepared_source(c)
  self.assertEqual(len(removed),1);self.assertEqual(len(repaired.interiors),2)
  self.assertAlmostEqual(repaired.difference(source).area,small.area)
  self.assertTrue(repaired.intersection(slit).area==0 and repaired.intersection(large).area==0)
  self.assertEqual(len(source.interiors),3)
 def test_local_convex_tip_is_smoothed_without_moving_remote_anchor(self):
  from shapely.geometry import Polygon,box
  from die_shift_studio.local_rebuild import fair_concave_joins
  from die_shift_studio.deformation import angle_regions,snapped
  source=Polygon([(0,0),(100,0),(20,20),(0,20)]);support=box(80,-10,110,20)
  self.assertFalse(angle_regions(source,90,.001).is_empty)
  q=snapped(fair_concave_joins(source,support,.05,15,minimum=90),.001)[0]
  self.assertTrue(q.is_valid);self.assertTrue(angle_regions(q,90,.001).is_empty)
  self.assertTrue(q.covers(box(1,1,10,10)))
  self.assertLess(q.symmetric_difference(source).difference(support).area,1e-8)
 def test_partial_same_net_adapts_safe_terminal_without_enlarging_old_contact(self):
  from die_shift_studio.core import read_gds,build_case,Parameters
  from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
  from die_shift_studio.drc import rule_region
  from die_shift_studio.native_backend import geometry
  root=Path(__file__).resolve().parents[1];q=json.loads((root/'examples/示例L_同网部分适配与既有接触.json').read_text())
  d=read_gds(root/'examples'/q['gds_path']);case=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters'])
  e=PreviewEngine(GeometryCache(d),case,q['roles'],{},p,{},case['connections']);e.configure_deformation(True,ViewSettings())
  try:
   z=np.array([[0.,35.,0.],[-200.,0.,0.]]);f=e.frame(z);out=f['deformation'];statuses={r['via_index']:r['status'] for r in out['interfaces']}
   self.assertEqual(statuses[0],'已适配');self.assertTrue(statuses[1].startswith('未'))
   self.assertGreater(out['blocked_count'],0);self.assertEqual(out['partial_component_count'],1)
   v=out['replacements']['5/0'][0];g=v['geometry'];co=next(c for c in e.deformer.components if c.indices==v['indices'])
   chosen=set(map(tuple,v['adapted_vias']))
   for r in co.records:
    if (r['key'],r['index']) in chosen:
     via=e.contact.moved(r['poly'],r['owner'],z);self.assertTrue(g.covers(via));self.assertGreaterEqual(g.boundary.distance(via)+1e-5,15)
    else:self.assertTrue(g.covers(r['poly']))
   self.assertTrue(rule_region(g,15,d.dbu_um).is_empty);self.assertTrue(rule_region(g,15,d.dbu_um,'notch').is_empty)
   old=geometry('normalize',co.poly,d.dbu_um)
   for r in e.contact.records:
    if r['net']==co.net:continue
    control=e.contact.moved(r['poly'],r['owner'],z).buffer(30,quad_segs=32)
    self.assertLess(g.intersection(control).difference(old.intersection(control)).area,1e-8)
  finally:e.close()
if __name__=='__main__':unittest.main()
