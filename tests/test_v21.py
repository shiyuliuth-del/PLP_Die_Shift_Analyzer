"""Physical ownership and local cut-capacity contracts, including strict edges."""
import unittest,json,math
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import numpy as np,shapely
from shapely.geometry import box,LineString
from die_shift_studio.core import GDSData,Parameters,build_case,read_gds
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import WidthAnalyzer
ROOT=Path(__file__).resolve().parents[1]

def example(name):
 q=json.loads((ROOT/'examples'/(name+'.json')).read_text(encoding='utf-8'));d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters'])
 e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections']);return d,q,c,p,e

class V21Tests(unittest.TestCase):
 def test_nonuniform_chip_stacks_and_external_pads_move_independently(self):
  d,q,c,p,e=example('示例M_不同芯片底层与Pad随动')
  try:
   np.testing.assert_array_equal(c['ownership']['4/0'],[0,1])
   roots=c['connections'].summary()['chip_terminal_planes_by_die'];self.assertEqual([r['layers'] for r in roots],[['2/0'],['3/0']])
   states=np.array([[35.,-12.,0.],[70.,-20.,math.pi/2]])
   f=e.frame(states);old=d.layers['4/0'][1];expected=np.column_stack((1070-old[:,1],old[:,0]-1020))
   np.testing.assert_allclose(f['layers']['4/0']['polygons'][1],expected,atol=1e-8)
   np.testing.assert_allclose(f['layers']['4/0']['polygons'][0],d.layers['4/0'][0]+[35,-12],atol=1e-8)
   np.testing.assert_allclose(f['layers']['5/0']['polygons'][1],d.layers['5/0'][1]);self.assertFalse(f['motion_issues'])
  finally:e.close()
 def test_missing_local_via_is_not_bridged_and_via_to_via_remains_connected(self):
  arrays=lambda *bounds:[np.asarray(box(*b).exterior.coords) for b in bounds]
  layers={'9/0':arrays((-1500,-900,1500,900)),'8/0':arrays((-1300,-300,-700,300),(700,-300,1300,300)),
   '4/0':arrays((-1010,-10,-990,10),(990,-10,1010,10)),
   '3/0':arrays((-1020,-20,-980,20)),
   '2/0':arrays((-1030,-30,-970,30),(970,-30,1030,30))}
  roles={'9/0':'封装边框','8/0':'芯片边框','4/0':'运动过孔','3/0':'运动过孔','2/0':'运动RDL'}
  d=GDSData('local.gds','local','TOP',['TOP'],.001,layers,roles);c=build_case(d,roles)
  np.testing.assert_array_equal(c['ownership']['4/0'],[0,1])
  self.assertTrue(any(v['upper']=='4/0' and v['lower']=='3/0' and v['upper_index']==0 for v in c['connections'].links))
  self.assertFalse(any(v['upper']=='4/0' and v['lower']=='2/0' for v in c['connections'].links))
 def test_empty_metal_is_ignored_and_unconnected_motion_follows_the_nearest_die(self):
  d,q,c,p,e=example('示例M_不同芯片底层与Pad随动');e.close()
  d.layers['1/0']=[];roles=dict(q['roles'],**{'1/0':'运动过孔'});order=q['layer_order']+['1/0'];c=build_case(d,roles,layer_order=order)
  self.assertIn('1/0',c['connections'].empty_metal_layers);np.testing.assert_array_equal(c['ownership']['4/0'],[0,1])
  extra=box(100,-900,120,-880)
  d.layers['4/0'].append(np.asarray(extra.exterior.coords));c=build_case(d,roles,layer_order=order)
  positional=[v for v in c['connections'].summary()['positional_ownership'] if v['layer']=='4/0' and v['index']==3]
  self.assertEqual(len(positional),1);owner=positional[0]['die']-1
  self.assertEqual(int(c['ownership']['4/0'][2]),owner)
  self.assertEqual(owner,int(np.argmin([extra.distance(die) for die in c['dies']])))
  e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections'])
  try:
   f=e.frame(np.array([[35.,0.,0.],[0.,0.,0.]]));self.assertFalse(f['motion_issues'])
   shift=np.array([35.,0.]) if owner==0 else np.zeros(2)
   np.testing.assert_allclose(f['layers']['4/0']['polygons'][2],np.asarray(extra.exterior.coords)+shift)
  finally:e.close()
 def test_nonuniform_stack_respects_per_die_rotation_pivots(self):
  from die_shift_studio.pivots import rotation_centers
  d,q,c,p,e=example('示例M_不同芯片底层与Pad随动');e.close()
  p=replace(p,per_die_enabled=True,die_bounds=[[35,35,1],[20,30,2]],die_pivots=[['top_left',0,0],['custom',120,-50]])
  e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections'])
  try:
   z=np.array([[35,-35,-math.pi/180],[-20,30,2*math.pi/180]]);f=e.frame(z);centers=rotation_centers(c,p)
   for i in range(2):
    a=z[i,2];rotation=np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])
    np.testing.assert_allclose(f['layers']['4/0']['polygons'][i],(d.layers['4/0'][i]-centers[i])@rotation.T+centers[i]+z[i,:2],atol=1e-9)
  finally:e.close()
 def test_fixed_via_between_moving_via_and_rdl_does_not_block_the_contact(self):
  arrays=lambda *b:[np.asarray(box(*q).exterior.coords) for q in b]
  base={'9/0':arrays((-1500,-900,1500,900)),'8/0':arrays((-600,-300,600,300)),
        '6/0':arrays((-20,-20,20,20)),'7/0':arrays((-100,-100,100,100))}
  roles={'9/0':'封装边框','8/0':'芯片边框','6/0':'运动过孔','7/0':'固定RDL','2/0':'固定过孔'}
  order=['6/0','2/0','7/0','9/0','8/0']
  d=GDSData('local.gds','local','TOP',['TOP'],.001,dict(base,**{'2/0':arrays((400,-10,420,10))}),roles)
  c=build_case(d,roles,layer_order=order)['connections']
  self.assertEqual(c.fixed_interfaces('6/0',0),[('7/0',0)])
  d=GDSData('local.gds','local','TOP',['TOP'],.001,dict(base,**{'2/0':arrays((-20,-20,20,20))}),roles)
  c=build_case(d,roles,layer_order=order)['connections']
  self.assertEqual(c.fixed_interfaces('6/0',0),[('7/0',0)])
 def test_a_wire_connecting_two_dies_follows_its_nearest_die(self):
  arrays=lambda *b:[np.asarray(box(*q).exterior.coords) for q in b]
  layers={'9/0':arrays((-1500,-900,1500,900)),'8/0':arrays((-1300,-300,-700,300),(700,-300,1300,300)),
          '3/0':arrays((-1000,-20,1000,20))}
  roles={'9/0':'封装边框','8/0':'芯片边框','3/0':'运动RDL'}
  d=GDSData('local.gds','local','TOP',['TOP'],.001,layers,roles);c=build_case(d,roles)
  owner=int(c['ownership']['3/0'][0]);self.assertIn(owner,(0,1))
  shared=[q for q in c['connections'].summary()['positional_ownership'] if q.get('mode')=='shared']
  self.assertEqual(len(shared),1);self.assertEqual(shared[0]['dies'],[1,2]);self.assertEqual(shared[0]['die'],owner+1)
  e=PreviewEngine(GeometryCache(d),c,roles,{},Parameters(),None,c['connections'])
  try:
   states=np.array([[35.,0.,0.],[0.,0.,0.]]);f=e.frame(states)
   nominal=layers['3/0'][0];moved=np.asarray(f['layers']['3/0']['polygons'][0])
   np.testing.assert_allclose(moved-nominal,np.tile(states[owner,:2],(len(nominal),1)),atol=1e-9)
  finally:e.close()
 def test_actual_preview_deformation_is_accepted_without_new_markers(self):
  from die_shift_studio.extreme_validation import frame_status
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.result={};e.configure_width(True,ViewSettings());e.configure_deformation(True,ViewSettings())
  try:
   baseline=e.frame(np.zeros((2,3)))['deformation']['violations']
   f=e.frame(np.array([[-35.,0,0],[0,0,0]]))
   self.assertEqual(f['deformation']['accepted_count'],1)
   # Existing conservative buffer discretization warnings are not new DRC.
   before=shapely.union_all([v['geometry'] for v in baseline]);after=shapely.union_all([v['geometry'] for v in f['deformation']['violations']])
   self.assertLess(after.difference(before).area,1e-8)
   quick=e.frame(np.array([[-17.5,0,0],[0,0,0]]),quick=True)
   self.assertFalse(quick['width_analysis']['complete']);self.assertIsNot(frame_status(quick)['safe'],True)
  finally:e.close()
if __name__=='__main__':unittest.main()
