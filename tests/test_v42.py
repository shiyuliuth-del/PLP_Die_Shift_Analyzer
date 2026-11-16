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
 def test_border_is_independent_of_space_and_tolerance(self):
  from die_shift_studio.edge_clearance import edge_violations
  e,_,_=fixture()
  try:
   g=box(-194,-100,-185,100);rep={'5/0':[dict(indices=(0,),geometry=g)]};z=np.zeros((1,3))
   self.assertTrue(edge_violations(e.contact,z,replace(e.p,package_clearance_um=10),ViewSettings(drc_tolerance_um=4),rep))
   self.assertFalse(edge_violations(e.contact,z,replace(e.p,package_clearance_um=5),ViewSettings(),rep))
   self.assertFalse(edge_violations(e.contact,z,replace(e.p,package_clearance_um=0),ViewSettings(),rep))
  finally:e.close()
 def test_ignored_copper_moves_and_fixed_via_enclosure_is_checked(self):
  e,_,_=fixture()
  try:
   e.configure_reconstruction(True,ViewSettings(reconstruction_ignored=(('5/0',0),)))
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
    self.assertEqual(len(entries),len(moving)*len(fixed))
    self.assertTrue(all(q['plan'] is not None for q in entries))
    self.assertLess(f['replacements']['5/0'][0]['geometry'].symmetric_difference(source).area,.01)
    moved=r.frame(np.array([[4.,3.,0.]]));g=moved['replacements']['5/0'][0]['geometry']
    self.assertTrue(g.is_valid)
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
   self.assertTrue(plan.pad_hit((600,0),1))
   at=plan.snap(plan.fixed_start+1,1,a)
   self.assertLess(at,plan.length)
   self.assertEqual(plan.snap(plan.fixed_start+10,1,a),plan.length)
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
   e.configure_reconstruction(True,cfg);calls=[];rules=e.reconstructor.rules;original=rules.best_regions
   def counted(plan,*args,**kwargs):calls.append(plan.index);return original(plan,*args,**kwargs)
   with patch.object(rules,'best_regions',counted):f=e.frame(z)['reconstruction']
   self.assertTrue(calls);self.assertEqual(set(calls),{0})
   self.assertEqual(next(v['geometry'].wkb for v in f['replacements']['5/0'] if v['indices']==(1,)),old)
  finally:e.close()
 def test_defaults_and_removed_patch_options(self):
  self.assertEqual(Parameters().package_clearance_um,10)
  self.assertEqual(ViewSettings().reconstruction_boundary_mode,'wide')
  self.assertFalse(hasattr(ViewSettings.from_dict({'repair_landing_pad':True}),'repair_landing_pad'))

if __name__=='__main__':unittest.main()
