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
from die_shift_studio.width_analysis import WidthAnalyzer,capacity
ROOT=Path(__file__).resolve().parents[1]

def example(name):
 q=json.loads((ROOT/'examples'/(name+'.json')).read_text(encoding='utf-8'));d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters'])
 e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections']);return d,q,c,p,e

def throat(frame):
 return next(v for v in frame['width_analysis']['rows'] if v['plane']=='5' and set(v['pair'])=={'pad:4/0:0','pad:4/0:1'})

class V21Tests(unittest.TestCase):
 def test_capacity_counts_both_wall_spacings_and_inclusive_equality(self):
  self.assertEqual(capacity(105,15,15,3)['capacity'],3)
  self.assertEqual(capacity(104.999,15,15,3)['status'],'不足')
  self.assertEqual(capacity(135,15,15,3)['status'],'充足')
  self.assertEqual(capacity(75,15,15,3)['capacity'],2)
  self.assertEqual(capacity(0,15,15,3)['capacity'],0)
  with self.assertRaises(ValueError):capacity(100,0,15,2)
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
  np.testing.assert_array_equal(c['ownership']['4/0'],[0,-2])
  self.assertTrue(any(v['upper']=='4/0' and v['lower']=='3/0' and v['upper_index']==0 for v in c['connections'].links))
  self.assertFalse(any(v['upper']=='4/0' and v['lower']=='2/0' for v in c['connections'].links))
 def test_empty_metal_is_ignored_but_unknown_motion_is_not_silent(self):
  d,q,c,p,e=example('示例M_不同芯片底层与Pad随动');e.close()
  d.layers['1/0']=[];roles=dict(q['roles'],**{'1/0':'运动过孔'});order=q['layer_order']+['1/0'];c=build_case(d,roles,layer_order=order)
  self.assertIn('1/0',c['connections'].empty_metal_layers);np.testing.assert_array_equal(c['ownership']['4/0'],[0,1])
  d.layers['4/0'].append(np.asarray(box(100,-900,120,-880).exterior.coords));c=build_case(d,roles,layer_order=order)
  e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections'])
  try:
   f=e.frame(np.array([[35.,0.,0.],[0.,0.,0.]]));self.assertTrue(f['motion_issues']);self.assertTrue(all(r['problem_percent']==100 for r in f['checks'].values()))
   self.assertTrue(any('4/0 #3' in v['kind'] for v in f['checks'][1]['failures']))
  finally:e.close()
 def test_nominal_lanes_remain_required_when_shift_covers_a_trace(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.configure_width(True,ViewSettings())
  try:
   start=e.frame(np.zeros((2,3)));a=throat(start);self.assertEqual((a['gap_um'],a['strands'],a['capacity'],a['status']),(110.,3,3,'临界'))
   frame=e.frame(np.array([[35.,0.,0.],[0.,0.,0.]]));a=throat(frame)
   self.assertEqual((a['gap_um'],a['strands'],a['capacity'],a['status']),(75.,3,2,'不足'));self.assertAlmostEqual(a['margin_um'],-30)
   self.assertEqual(len(a['refs']),3)
   with patch.object(e.width_analyzer,'_cut',side_effect=AssertionError('Cached widths recomputed')):self.assertIs(e.frame(frame['states']),frame)
  finally:e.close()
 def test_actual_deformed_fixed_metal_updates_capacity_and_cache(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.configure_width(True,ViewSettings());z=np.array([[-35.,0.,0.],[0.,0.,0.]])
  try:
   f=e.frame(z);self.assertEqual(throat(f)['gap_um'],110.) # old fixed landing still occupies space
   original=shapely.Polygon(d.layers['5/0'][0]);shifted=shapely.affinity.translate(original,-35,0)
   after=dict(f,deformation={'replacements':{'5/0':[dict(indices=(0,),geometry=shifted)]}})
   analysis=e.width_analyzer.frame(after);a=throat(dict(width_analysis=analysis))
   self.assertEqual((a['gap_um'],a['capacity'],a['status']),(145.,4,'充足'))
   self.assertIs(e.width_analyzer.frame(after),analysis)
  finally:e.close()
 def test_rule_changes_boundary_and_fixed_graphic_sections_are_analyzed(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度')
  try:
   e.configure_width(True,ViewSettings());base=e.frame(np.zeros((2,3)))['width_analysis']
   kinds={k for r in base['rows'] for k in r['ends']};self.assertIn('固定图形',kinds);self.assertIn('边框',kinds)
   e.configure_width(True,ViewSettings(drc_tolerance_um=-1));a=throat(e.frame(np.zeros((2,3))))
   self.assertEqual((a['width_um'],a['space_um'],a['required_um'],a['status']),(16.,16.,112.,'不足'))
  finally:e.close()
 def test_repeated_same_net_strands_are_not_collapsed_to_one_net(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.close()
  # Join two vertical traces well away from the gate: one electrical net still
  # needs two separate strands through the original narrow channel.
  d.layers['5/0'].append(np.asarray(box(-37.5,450,7.5,465).exterior.coords));c=build_case(d,q['roles'],layer_order=q['layer_order'])
  e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections']);e.configure_width(True,ViewSettings())
  try:self.assertEqual(throat(e.frame(np.zeros((2,3))))['strands'],3)
  finally:e.close()
 def test_newly_opened_current_channel_is_discovered(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.close()
  for key,bounds in [('128/0',(-300,-890,300,-510)),('2/0',(-15,-715,15,-685)),('3/0',(-7.5,-700,7.5,0)),('4/0',(-10,-150,10,150))]:
   d.layers[key].append(np.asarray(box(*bounds).exterior.coords))
  c=build_case(d,q['roles'],layer_order=q['layer_order']);self.assertEqual(c['ownership']['4/0'][2],1)
  e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections']);e.configure_width(True,ViewSettings())
  try:
   initial=e.frame(np.zeros((3,3)))['width_analysis']
   target={'pad:4/0:0','pad:4/0:1'}
   self.assertFalse(any(set(v['pair'])==target for v in initial['rows']))
   z=np.zeros((3,3));z[1,1]=300;current=e.frame(z)
   row=throat(current);self.assertEqual((row['strands'],row['capacity']),(3,3))
  finally:e.close()
 def test_existing_intermediate_metal_prevents_skipping_a_broken_via(self):
  arrays=lambda *bounds:[np.asarray(box(*b).exterior.coords) for b in bounds]
  layers={'9/0':arrays((-500,-500,500,500)),'8/0':arrays((-300,-300,300,300)),
   '4/0':arrays((-10,-10,10,10)),'3/0':arrays((100,-10,120,10)),
   '2/0':arrays((-20,-20,20,20),(90,-20,130,20))}
  roles={'9/0':'封装边框','8/0':'芯片边框','4/0':'运动过孔','3/0':'运动过孔','2/0':'运动RDL'}
  d=GDSData('broken.gds','broken','TOP',['TOP'],.001,layers,roles);c=build_case(d,roles)
  self.assertEqual(c['ownership']['4/0'][0],-2)
  self.assertFalse(any(v['upper']=='4/0' and v['lower']=='2/0' for v in c['connections'].links))
 def test_blocked_cut_keeps_new_crossings_as_well_as_nominal_demand(self):
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.close()
  d.layers['5/0'][4]=d.layers['5/0'][4]+[170,0]
  for key,bounds in [('128/0',(-300,-890,300,-510)),('2/0',(-15,-715,15,-685)),('3/0',(-7.5,-700,7.5,400)),('4/0',(-10,250,10,550))]:
   d.layers[key].append(np.asarray(box(*bounds).exterior.coords))
  c=build_case(d,q['roles'],layer_order=q['layer_order']);e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections']);e.configure_width(True,ViewSettings())
  try:
   self.assertEqual(throat(e.frame(np.zeros((3,3))))['strands'],2)
   z=np.zeros((3,3));z[1,1]=-400;f=e.frame(z)
   moved=shapely.affinity.translate(shapely.Polygon(d.layers['5/0'][4]),-170,0)
   final=dict(f,deformation={'replacements':{'5/0':[dict(indices=(4,),geometry=moved)]}})
   row=throat(dict(width_analysis=e.width_analyzer.frame(final)))
   self.assertEqual(row['strands'],3);self.assertIn('available_slots_um',row);self.assertEqual(row['status'],'不足')
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
 def test_actual_preview_deformation_opens_channel_and_quick_is_not_full_clearance(self):
  from die_shift_studio.extreme_validation import frame_status
  d,q,c,p,e=example('示例N_三线路窄通道宽度');e.result={};e.configure_width(True,ViewSettings());e.configure_deformation(True,ViewSettings())
  try:
   baseline=e.frame(np.zeros((2,3)))['deformation']['violations']
   f=e.frame(np.array([[-35.,0,0],[0,0,0]]));row=throat(f)
   self.assertEqual(f['deformation']['accepted_count'],1)
   # Existing conservative buffer discretization warnings are not new DRC.
   before=shapely.union_all([v['geometry'] for v in baseline]);after=shapely.union_all([v['geometry'] for v in f['deformation']['violations']])
   self.assertLess(after.difference(before).area,1e-8)
   self.assertAlmostEqual(row['gap_um'],145.,delta=.02);self.assertEqual(row['capacity'],4)
   with patch.object(e.width_analyzer,'_discover',side_effect=AssertionError('Animation should track known throats')):
    quick=e.frame(np.array([[-17.5,0,0],[0,0,0]]),quick=True)
   self.assertFalse(quick['width_analysis']['complete']);self.assertIsNot(frame_status(quick)['safe'],True)
  finally:e.close()
if __name__=='__main__':unittest.main()
