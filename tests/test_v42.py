"""V42 independent geometry contracts, not screenshots or counts alone."""
import unittest,tempfile,json
from pathlib import Path
from dataclasses import replace
import numpy as np,shapely
from shapely.geometry import box,Point,LineString
from test_v38 import fixture
from test_v40 import make_engine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.core import Parameters
from die_shift_studio.reconstruction import transform

class V42Tests(unittest.TestCase):
 def test_border_is_independent_of_space_but_uses_signed_tolerance(self):
  from die_shift_studio.edge_clearance import edge_violations
  e,_,_=fixture()
  try:
   g=box(-194,-100,-185,100);rep={'5/0':[dict(indices=(0,),geometry=g)]};z=np.zeros((1,3))
   self.assertFalse(edge_violations(e.contact,z,replace(e.p,package_clearance_um=10),ViewSettings(drc_tolerance_um=4),rep))
   self.assertTrue(edge_violations(e.contact,z,replace(e.p,package_clearance_um=10),ViewSettings(drc_tolerance_um=-4),rep))
   self.assertFalse(edge_violations(e.contact,z,replace(e.p,package_clearance_um=5),ViewSettings(),rep))
   self.assertFalse(edge_violations(e.contact,z,replace(e.p,package_clearance_um=0),ViewSettings(),rep))
  finally:e.close()
 def test_candidate_ranking_uses_board_margin_not_wire_spacing(self):
  e,_,_=fixture()
  try:
   r=e.reconstructor;r.model.case['board']=box(-200,-250,850,42)
   from die_shift_studio.reconstruction_rules import FixedRules
   r.rules=FixedRules(r.model,replace(e.p,package_clearance_um=10),r.settings)
   q=r.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan'];part=plan.partition(*q['interval'])
   _,error,score=r.rules.best_regions(plan,part,e.bound.centers[0],np.zeros(3),'linear')
   self.assertIsNone(error);self.assertEqual(score[0],0)
   strict=FixedRules(r.model,replace(e.p,package_clearance_um=20),r.settings)
   _,error,score=strict.best_regions(plan,part,e.bound.centers[0],np.zeros(3),'linear')
   self.assertEqual(score[1],0) # rigid-pad-only margin does not rank the A region
   stricter=FixedRules(r.model,replace(e.p,package_clearance_um=30),r.settings)
   _,error,score=stricter.best_regions(plan,part,e.bound.centers[0],np.zeros(3),'linear')
   self.assertGreater(score[1],0)
  finally:e.close()
 def test_ignored_copper_moves_and_fixed_via_enclosure_is_checked(self):
  e,_,_=fixture()
  try:
   e.configure_reconstruction(True,ViewSettings(reconstruction_ignored=(('5/0',0),),reconstruction_following=(('5/0',0),)))
   z=np.array([[15.,12.,.01]]);f=e.frame(z)['reconstruction'];g=f['replacements']['5/0'][0]['geometry']
   original=e.contact.cache.layers['5/0']['geometry'][0]
   self.assertLess(g.symmetric_difference(transform(original,e.bound.centers[0],z[0])).area,.01)
   self.assertTrue(any(v['kind']=='孔盘包围不足' for v in f['violations']))
  finally:e.close()
 def test_serial_overlaps_merge_to_one_source_interval(self):
  from die_shift_studio.reconstruction_edits import key
  e,_,_=fixture()
  try:
   cfg=ViewSettings(reconstruction_boundary_mode='perpendicular',reconstruction_added=(('5/0',0,300.,0.,'second'),),reconstruction_forced=(('5/0',0),))
   e.configure_reconstruction(True,cfg);f=e.frame(np.zeros((1,3)))['reconstruction'];a,b=f['pieces']
   intervals=((*key(a['route_plan']),100.,360.),(*key(b['route_plan']),320.,500.))
   e.configure_reconstruction(True,replace(cfg,reconstruction_intervals=intervals));f=e.frame(np.array([[10.,5.,0.]]))['reconstruction']
   self.assertEqual(len(f['pieces']),1);self.assertEqual(f['pieces'][0]['interval'],(100.,500.))
   self.assertTrue(f['pieces'][0]['valid'])
  finally:e.close()
 def test_all_branch_pairs_have_source_controls(self):
  for moving,fixed in [([(-300,0)],[(300,180),(300,-180)]),([(-300,180),(-300,-180)],[(300,0)]),([(-300,180),(-300,-180)],[(300,180),(300,-180)])]:
   source=shapely.union_all([LineString([p,(0,0)]).buffer(20) for p in moving+fixed])
   source=source.union(shapely.union_all([Point(p).buffer(35) for p in moving+fixed]))
   shapes={'127/0':[box(-600,-400,600,400)],'128/0':[box(-360,-250,-240,250)],'3/0':[Point(p).buffer(15) for p in moving],
           '4/0':[Point(p).buffer(8) for p in moving],'5/0':[source],'6/0':[Point(p).buffer(8) for p in fixed]}
   e,_,_=make_engine(shapes,Parameters(single_bound_x_um=5,single_bound_y_um=5,bound_angle_deg=0,annulus_um=5),ViewSettings(reconstruction_boundary_mode='perpendicular',reconstruction_forced=(('5/0',0),)))
   try:
    r=e.reconstructor;f=r.frame(np.zeros((1,3)));entries=r._plans
    # A connected terminal tree covers all moving-to-fixed connectivity;
    # duplicate Cartesian routes are no longer independent overlapping zones.
    self.assertEqual(len(entries),len(moving)+len(fixed)-1)
    self.assertEqual({tuple(np.round(v['center'],6)) for q in entries for v in q['mine']},set(moving))
    self.assertTrue(all(q['plan'] is not None for q in entries))
    self.assertLess(f['replacements']['5/0'][0]['geometry'].symmetric_difference(source).area,.01)
    moved=r.frame(np.array([[4.,3.,0.]]));g=moved['replacements']['5/0'][0]['geometry']
    self.assertTrue(g.is_valid)
    self.assertEqual(g.geom_type,'Polygon','all terminal branches must remain connected')
    for p in moving:self.assertLess(transform(Point(p).buffer(8),e.bound.centers[0],np.array([4.,3.,0.])).difference(g).area,.01)
    for p in fixed:self.assertLess(Point(p).buffer(8).difference(g).area,.01)
   finally:e.close()
 def test_data_only_sidecar_roundtrips_real_frame(self):
  from die_shift_studio.project_results import encode,decode
  e,_,_=fixture()
  try:
   f=e.frame(np.array([[5.,2.,0.]]));got=decode(json.loads(json.dumps(encode(f))))
   np.testing.assert_array_equal(f['states'],got['states'])
   self.assertTrue(got['reconstruction']['pieces'][0]['bridge'].equals(f['reconstruction']['pieces'][0]['bridge']))
   self.assertEqual(got['reconstruction']['pieces'][0]['route_plan'].identity,f['reconstruction']['pieces'][0]['route_plan'].identity)
   with self.assertRaises(ValueError):decode(['python:eval','bad'])
  finally:e.close()
 def test_terminal_trigger_needs_connected_pad_and_drag_margin(self):
  e,_,_=fixture()
  try:
   q=e.frame(np.zeros((1,3)))['reconstruction']['pieces'][0];plan=q['route_plan'];a,b=q['interval']
   self.assertFalse(plan.pad_hit((600,300),1))
   self.assertFalse(plan.pad_hit((600,0),1))
   at=plan.snap(plan.fixed_start+1,1,a)
   self.assertLess(at,plan.length)
   self.assertEqual(plan.snap(plan.fixed_start+10,1,a),at)
   stopped=plan.edit_pose(a,b,1,plan.length,e.bound.centers[0],np.zeros(3),'c2',cursor=(600,300))[0]
   self.assertNotEqual(stopped['far'].geom_type,'Point')
  finally:e.close()
 def test_other_feature_deformation_is_reused_after_one_edit(self):
  from unittest.mock import patch
  from die_shift_studio.reconstruction_edits import key
  shapes={'127/0':[box(-200,-200,800,500)],'128/0':[box(-60,-80,60,330)],
   '3/0':[Point(0,y).buffer(20) for y in (0,250)],'4/0':[Point(0,y).buffer(10) for y in (0,250)],
   '5/0':[LineString([(0,y),(600,y)]).buffer(30) for y in (0,250)],'6/0':[Point(600,y).buffer(10) for y in (0,250)]}
  cfg=ViewSettings(reconstruction_boundary_mode='perpendicular',reconstruction_forced=(('5/0',0),('5/0',1)))
  e,_,_=make_engine(shapes,Parameters(annulus_um=5),cfg)
  try:
   z=np.array([[10.,5.,.005]]);first=e.frame(z)['reconstruction'];q=next(q for q in first['pieces'] if q['index']==0)
   old=next(v['geometry'].wkb for v in first['replacements']['5/0'] if v['indices']==(1,))
   cfg=replace(cfg,reconstruction_intervals=((*key(q['route_plan']),q['interval'][0]+30,q['interval'][1]),))
   e.configure_reconstruction(True,cfg);calls=[]
   import die_shift_studio.reconstruction_angles as angles
   original=angles.solve
   def counted(solver,plan,*args,**kwargs):calls.append(plan.index);return original(solver,plan,*args,**kwargs)
   with patch.object(angles,'solve',counted):f=e.frame(z)['reconstruction']
   self.assertTrue(calls);self.assertEqual(set(calls),{0})
   self.assertEqual(next(v['geometry'].wkb for v in f['replacements']['5/0'] if v['indices']==(1,)),old)
  finally:e.close()
 def test_live_branch_edit_is_local_and_defers_native_search(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  from die_shift_studio.reconstruction_zones import live_serial
  from die_shift_studio.reconstruction_edits import key
  from die_shift_studio.reconstruction_rules import FixedRules
  e,_,_=fixture()
  try:
   old=e.frame(np.array([[5.,3.,.002]]));q=old['reconstruction']['pieces'][0];plan=q['route_plan']
   cfg=replace(e.reconstructor.settings,reconstruction_intervals=((*key(plan),q['interval'][0]+20,q['interval'][1]),))
   with patch.object(FixedRules,'best_regions',side_effect=AssertionError('native search on pointer move')):
    frame=live_serial(e.reconstructor,cfg,old,('5/0',0))
   self.assertFalse(frame['reconstruction']['constraints_complete'])
   self.assertEqual(frame['reconstruction']['pieces'][0]['interval'][0],q['interval'][0]+20)
   self.assertTrue(frame['reconstruction']['replacements']['5/0'][0]['geometry'].is_valid)
   self.assertIs(old['reconstruction']['pieces'][0],q)
  finally:e.close()
 def test_discrete_source_cache_reuses_unchanged_geometry(self):
  from unittest.mock import patch
  from die_shift_studio.session_cache import SessionCache
  from die_shift_studio.statistics import ExtremeCatalog
  from die_shift_studio.reconstruction_events import scan
  from die_shift_studio.reconstruction_edits import key
  e,_,_=fixture();cache=SessionCache()
  try:
   p=replace(e.p,single_bound_x_um=2,single_bound_y_um=0,bound_angle_deg=0)
   e.p=p;e.configure_reconstruction(True,replace(e.reconstructor.settings,reconstruction_forced=(('5/0',0),)));e.reconstructor.p=p;grid=ExtremeCatalog(e.contact.case,p)
   a=scan(e.reconstructor,grid,source_cache=cache)
   with patch.object(e.reconstructor,'frame',side_effect=AssertionError('cached source recalculated')):
    b=scan(e.reconstructor,grid,source_cache=cache)
   self.assertEqual(a['red_factors'],b['red_factors']);self.assertEqual(a['zones'].keys(),b['zones'].keys())
   q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0]
   cfg=replace(e.reconstructor.settings,reconstruction_intervals=((*key(q['route_plan']),q['interval'][0]+10,q['interval'][1]),))
   e.configure_reconstruction(True,cfg)
   with patch.object(e.reconstructor,'frame',wraps=e.reconstructor.frame) as calls:
    c=scan(e.reconstructor,grid,source_cache=cache)
   self.assertGreater(calls.call_count,0)
  finally:cache.close();e.close()
 def test_numerical_overlay_failure_preserves_copper_and_marks_failure(self):
  from unittest.mock import patch
  from die_shift_studio.branch_regions import consolidate
  from die_shift_studio.reconstruction import compose_feature
  e,_,_=fixture()
  try:
   r=e.reconstructor;q=r.frame(np.zeros((1,3)))['pieces'][0]
   q=dict(q,graph_branch=True)
   with patch('die_shift_studio.branch_regions.assemble',side_effect=shapely.errors.GEOSException('synthetic overlay failure')):
    pieces=consolidate(r,[q],np.zeros((1,3)))
   self.assertTrue(all(v['connection_failed'] and not v['valid'] for v in pieces))
   copper,_=compose_feature(q['source'],pieces,r.tol,False)
   self.assertTrue(copper.equals(q['source']))
   with patch('die_shift_studio.reconstruction._compose_feature',side_effect=shapely.errors.GEOSException('overlay failure')):
    copper,_=compose_feature(q['source'],[dict(q)],r.tol,True)
   self.assertTrue(copper.equals(q['source']))
  finally:e.close()
 def test_unresolved_route_and_disconnected_ignored_copper_are_failures(self):
  from die_shift_studio.reconstruction import compose_feature
  from die_shift_studio.reconstruction_events import current_failures
  e,_,_=fixture()
  try:
   r=e.reconstructor;r._plans_for_all_poses(lambda *_:None)
   r._plans=[dict(r._plans[0],plan=None,orphan=False,warning='无法形成路径')]
   frame=r.frame(np.zeros((1,3)))
   self.assertFalse(current_failures(frame));self.assertTrue(frame['replacements']['5/0'][0]['geometry'].equals(r._plans[0]['source']))
   self.assertTrue(frame['pieces'][0]['routing_recovered'])
   source=box(0,0,20,10);empty=shapely.Polygon();pieces=[]
   for die,a,b,dy in ((0,0,10,10),(1,10,20,-10)):
    nominal=box(a,0,b,10);moved=box(a,dy,b,10+dy)
    pieces.append(dict(die=die,nominal_regions=dict(A=empty,B=empty,C=nominal),regions=dict(A=empty,B=empty,C=moved),fallback_regions=None,connection_failed=False,valid=True))
   geometry,_=compose_feature(source,pieces,.001,True)
   self.assertTrue(geometry.is_valid);self.assertTrue(all(q['connection_failed'] for q in pieces))
  finally:e.close()
 def test_geometry_pool_does_not_reuse_another_rotation_center(self):
  from die_shift_studio.reconstruction import Reconstructor
  from die_shift_studio.reconstruction_status import clone_plan_pool
  from die_shift_studio.session_cache import EphemeralCache
  e,_,_=fixture();pool=EphemeralCache(max_entries=2048)
  try:
   cfg=replace(e.reconstructor.settings,reconstruction_boundary_mode='perpendicular',reconstruction_forced=(('5/0',0),))
   first=Reconstructor(e.contact,e.p,cfg,geometry_pool=pool);z=np.array([[0.,0.,.02]])
   old=first.frame(z,diagnostics=False)
   e.contact.bound.centers[0]=np.array([500.,200.])
   warm=Reconstructor(e.contact,e.p,cfg,geometry_pool=pool,plan_pool=clone_plan_pool(first.plan_pool))
   cold=Reconstructor(e.contact,e.p,cfg,plan_pool=clone_plan_pool(first.plan_pool))
   a=warm.frame(z,diagnostics=False)['replacements']['5/0'][0]['geometry']
   b=cold.frame(z,diagnostics=False)['replacements']['5/0'][0]['geometry']
   self.assertEqual(a.wkb,b.wkb)
   self.assertFalse(a.equals(old['replacements']['5/0'][0]['geometry']))
   first.close();warm.close();cold.close()
  finally:pool.clear();e.close()
 def test_empty_failed_region_never_erases_source_or_crashes_border_check(self):
  from die_shift_studio.reconstruction import compose_feature
  from die_shift_studio.edge_clearance import edge_violations
  from unittest.mock import patch
  e,_,_=fixture()
  try:
   source=box(0,0,100,50);empty=shapely.Polygon();q=dict(valid=False,connection_failed=True)
   with patch('die_shift_studio.reconstruction._compose_feature',return_value=(empty,source)):
    copper,_=compose_feature(source,[q],.001,True)
   self.assertTrue(copper.equals(source));self.assertTrue(q['connection_failed'])
   self.assertEqual(edge_violations(e.contact,np.zeros((1,3)),e.p,ViewSettings(),{'5/0':[dict(indices=(0,),geometry=empty)]},replacements_only=True),[])
  finally:e.close()
 def test_result_sidecar_can_be_on_another_windows_drive(self):
  from unittest.mock import patch
  from die_shift_studio.project_results import portable_path
  target=Path(tempfile.gettempdir())/'results.dsresults'
  with patch('die_shift_studio.project_results.os.path.relpath',side_effect=ValueError('different drives')):
   self.assertEqual(portable_path(target,Path.cwd()),str(target.resolve()))
 def test_defaults_and_removed_patch_options(self):
  self.assertEqual(Parameters().package_clearance_um,10)
  self.assertEqual(ViewSettings().reconstruction_boundary_mode,'wide')
  self.assertFalse(hasattr(ViewSettings.from_dict({'repair_landing_pad':True}),'repair_landing_pad'))

if __name__=='__main__':unittest.main()
