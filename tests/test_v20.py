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
 def test_no_motion_is_not_a_material_following_certificate(self):
  from die_shift_studio.material_field import material_certificate
  r=dict(key='4/0',index=0,owner=0);xy=np.array([[0.,0.],[10.,0.]])
  q=material_certificate(lambda x:x,[(r,xy,10)],np.zeros((1,2)),np.array([[25.,0.,0.]]),.005)
  self.assertFalse(q['verified']);self.assertAlmostEqual(q['maximum_error_um'],25)
 def test_unavailable_mesh_backend_leaves_other_candidate_families_available(self):
  from die_shift_studio.material_field import material_candidates
  r=dict(owner=0,center=[0,0],poly=Point(0,0).buffer(10))
  with patch('die_shift_studio.material_field.MetalMesh',side_effect=shapely.errors.UnsupportedGEOSVersionError('offline GEOS')):
   out=list(material_candidates(box(-30,-30,30,100),[r],[Point(0,80).buffer(10)],np.zeros((1,2)),np.zeros((1,3)),ViewSettings(),15,.001,{}))
  self.assertEqual(out,[])
 def test_source_mesh_preserves_hole_and_reuses_clipped_regions(self):
  from die_shift_studio.material_field import MetalMesh
  source=box(0,0,100,100).difference(box(40,40,60,60));m=MetalMesh(source,10)
  self.assertLess(shapely.union_all(m.polygons).symmetric_difference(source).area,1e-8)
  d=m.distance(m.nodes_near([Point(0,0)],.001));g=m.interval_region(d,0,60)
  self.assertIs(g,m.interval_region(d,0,60));self.assertTrue(source.covers(g))
 def test_final_copper_and_source_landing_both_move(self):
  from die_shift_studio.core import read_gds,build_case,Parameters
  from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
  from die_shift_studio.preview import rigid_transform
  root=Path(__file__).resolve().parents[1];q=json.loads((root/'examples/示例K_中段连接与平滑孔盘.json').read_text())
  d=read_gds(root/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);e=PreviewEngine(GeometryCache(d),c,q['roles'],{},Parameters.from_dict(q['parameters']),{},c['connections'])
  try:
   e.configure_deformation(True,ViewSettings());z=np.array([[-35.,-35.,-math.pi/180]]);f=e.frame(z)
   self.assertEqual(f['deformation']['accepted_count'],2)
   for v in f['deformation']['replacements']['5/0']:
    cert=v['material_certificate'];self.assertTrue(cert['verified']);self.assertLessEqual(cert['output_boundary_error_um'],.052)
    self.assertNotIn(v['mode'],('pad_envelope','local_rebuild'))
    for sample in cert['samples']:
     expected=rigid_transform(sample['source'],e.bound.centers[sample['owner']],z[sample['owner']])
     np.testing.assert_allclose(sample['mapped'],expected,atol=.005)
     self.assertGreater(np.linalg.norm(expected-sample['source'],axis=1).max(),20)
     self.assertTrue(v['geometry'].buffer(.052).covers(shapely.MultiPoint(expected)))
  finally:e.close()
 def test_repeated_failed_search_is_cached_but_changed_neighbour_retries(self):
  from die_shift_studio.deformation import DeformationPreview,Component
  from collections import OrderedDict
  from shapely.strtree import STRtree
  import threading
  dp=object.__new__(DeformationPreview);dp.candidate_lock=threading.RLock();dp.search_results=OrderedDict();dp.search_result_bytes=0
  dp.model=SimpleNamespace(bound=SimpleNamespace(centers=np.zeros((1,2))),nodes={0:{'net':1},1:{'net':2}})
  dp.p=SimpleNamespace(annulus_um=15,line_space_um=15,line_width_um=15);dp.rule_space=15
  record=dict(key='4/0',index=0,owner=0);c=Component('5/0',(0,),1,box(0,0,100,30),[record],[]);z=np.array([[25.,0.,0.]])
  def snapshot(other):
   gs=np.array([c.poly,other],object);return {'5':([0,1],gs,STRtree(gs))}
  with patch.object(dp,'_search_uncached',side_effect=ValueError('finite search unresolved')) as solve:
   for _ in range(2):
    with self.assertRaisesRegex(ValueError,'unresolved'):dp.search(c,[record],[],z,[],snapshot(box(0,45,100,65)))
   self.assertEqual(solve.call_count,1)
   with self.assertRaises(ValueError):dp.search(c,[record],[],z,[],snapshot(box(0,60,100,80)))
   self.assertEqual(solve.call_count,2)
 def test_independent_engines_share_completed_frames_but_not_working_arrays(self):
  from die_shift_studio.core import read_gds,build_case,Parameters
  from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
  from die_shift_studio.session_cache import ProjectCacheRegistry
  root=Path(__file__).resolve().parents[1];q=json.loads((root/'examples/示例K_中段连接与平滑孔盘.json').read_text());d=read_gds(root/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);registry=ProjectCacheRegistry();group=registry.get('K');cache=GeometryCache(d)
  engines=[PreviewEngine(cache,c,q['roles'],{},Parameters.from_dict(q['parameters']),{},c['connections'],cache_group=group,cache_identity='K') for _ in range(2)]
  try:
   a,b=engines;self.assertIs(a.frames,b.frames);self.assertIsNot(a.bound,b.bound);self.assertIsNot(a.contact,b.contact)
   for e in engines:e.configure_deformation(True,ViewSettings());e.configure_drc(False,ViewSettings())
   z=np.array([[-35.,-35.,-.01]]);f=a.frame(z)
   with patch.object(b.deformer,'search',side_effect=AssertionError('Recomputed shared state')):self.assertIs(b.frame(z),f)
  finally:
   for e in engines:e.close()
   registry.close()
if __name__=='__main__':unittest.main()
