import unittest, tempfile, math
from pathlib import Path
from dataclasses import replace,asdict
from unittest.mock import patch
from itertools import combinations,product
import numpy as np
import shapely
from die_shift_studio.settings import ViewSettings
from die_shift_studio.core import Parameters

class V50Tests(unittest.TestCase):
 def test_pair_die_catalog_covers_opposing_poses_and_zero_axes(self):
  from die_shift_studio.representatives import RepresentativeExtremes
  from die_shift_studio.statistics import ExtremeCatalog
  p=Parameters();grid=ExtremeCatalog({'dies':[None]*3},p);focused=RepresentativeExtremes({'dies':[None]*3},p,grid)
  rows=[[0]*3,*[grid.choices(i) for i in focused.indices]]
  self.assertEqual(len(rows),729);self.assertEqual(grid.total+1,19683)
  for i,j in combinations(range(3),2):self.assertEqual({(r[i],r[j]) for r in rows},set(product(range(27),repeat=2)))
  frozen=replace(p,per_die_enabled=True,die_bounds=[[0,0,0],[15,0,0],[15,15,0]])
  g=ExtremeCatalog({'dies':[None]*3},frozen);f=RepresentativeExtremes({'dies':[None]*3},frozen,g)
  self.assertTrue(all(g.choices(i)[0]==0 for i in f.indices));self.assertEqual({(g.choices(i)[1],g.choices(i)[2]) for i in f.indices}|{(0,0)},set(product(range(3),range(9))))
 def test_actual_width_measurement_and_disabled_checks_skip_native(self):
  from die_shift_studio.drc_actual import measured
  from die_shift_studio.drc import GlobalDRC
  from test_v49_reconstruction import V49ReconstructionTests
  source=shapely.box(0,0,100,10);item=dict(kind='线宽不足',required_um=15,_measurement_copper=source)
  self.assertAlmostEqual(measured(item,shapely.box(20,-.001,80,10.001))['actual_um'],10)
  e,_,_=V49ReconstructionTests().fixture()
  try:
   cfg=replace(ViewSettings(),drc_checks=())
   with patch('die_shift_studio.drc.rule_region',side_effect=AssertionError('disabled native check')),patch('die_shift_studio.current_drc.edge_violations',create=True):
    self.assertEqual(GlobalDRC(e.contact,e.p).frame(np.zeros((1,3)),settings=cfg),[])
   self.assertNotEqual(cfg.reconstruction_key(),ViewSettings().reconstruction_key())
   display=replace(ViewSettings(),drc_visible=());self.assertEqual(display.reconstruction_key(),ViewSettings().reconstruction_key())
  finally:e.close()
 def test_export_uses_pose_die_boundary_with_custom_pivot(self):
  import gdstk
  from test_v49_reconstruction import V49ReconstructionTests
  from die_shift_studio.dxf_export import snapshot
  from die_shift_studio.layout_export import write,OutputOptions
  from die_shift_studio.preview import rigid_transform
  e,data,case=V49ReconstructionTests().fixture()
  try:
   p=replace(e.p,rotation_center='custom',rotation_x_um=11,rotation_y_um=-7)
   bound=e.contact.cache.bind(case,{},list(data.layers),p);state=np.array([[13.,-8.,math.radians(12)]])
   frame=dict(states=state,layers=bound.transform(state))
   frozen=snapshot(data,e.contact.cache,frame)
   expected=shapely.Polygon(rigid_transform(data.layers['128/0'][0],bound.centers[0],state[0]))
   self.assertTrue(shapely.from_wkb(frozen.layers['128/0'][0]).equals_exact(expected,1e-8))
   with tempfile.TemporaryDirectory() as folder:
    for suffix in ('gds','oas'):
     path=Path(folder)/('scene.'+suffix);write(frozen,path,.001,OutputOptions(),[dict(die_ids=[1],bounds_um=[0,0,5,5])],1)
     lib=gdstk.read_gds(path) if suffix=='gds' else gdstk.read_oas(path)
     geos=lib.top_level()[0].get_polygons(layer=128,datatype=0)
     actual=shapely.union_all([shapely.Polygon(g.points) for g in geos])
     self.assertLess(actual.hausdorff_distance(expected),.001)
     self.assertTrue(lib.top_level()[0].get_polygons(layer=65535,datatype=0))
  finally:e.close()
 def test_oriented_catalog_covers_actual_witness_without_double_pose(self):
  from die_shift_studio.measured_issues import catalogue,place
  from shapely.affinity import rotate,translate
  g=translate(rotate(shapely.box(0,0,10,2),17,origin=(0,0)),100,200)
  frame=dict(states=np.array([[100,200,math.radians(17)]]),drc=[dict(kind='线宽不足',layer='5/0',geometry=g,owner=0)])
  rows=catalogue(frame,{'board':shapely.box(-30,-40,200,300)})
  rect=shapely.Polygon(rows[0]['marker_corners_um']);self.assertTrue(rect.covers(g));self.assertAlmostEqual(rows[0]['marker_angle_deg'],17)
  result=place({'drc_issues':rows},[1000,2000])['drc_issues'][0]
  np.testing.assert_allclose(np.asarray(result['top_cell_marker_corners_um'])-rows[0]['marker_corners_um'],np.tile([1030,2040],(4,1)))
 def test_only_failed_zone_is_removed_and_marked(self):
  from die_shift_studio.reconstruction_diagnostic import display
  a=shapely.box(0,0,10,10);b=shapely.box(30,0,40,10)
  rows=[dict(layer='5/0',index=0,regions={'A':g,'B':shapely.Polygon(),'C':shapely.Polygon()},connection_failed=bad,bridge=g) for g,bad in ((a,True),(b,False))]
  result=display(dict(hard_failures=[(('5/0',0),'断开')],pieces=rows,replacements={'5/0':[dict(indices=(0,),geometry=a.union(b))]}),np.zeros((1,2)),np.zeros((1,3)))
  self.assertTrue(result['pieces'][0]['regions']['A'].is_empty);self.assertTrue(result['pieces'][1]['regions']['A'].equals(b))
  self.assertTrue(result['replacements']['5/0'][0]['geometry'].equals(b));self.assertFalse(result['pieces'][1].get('diagnostic',False))
 def test_quantization_residue_does_not_become_remote_active_spur(self):
  from die_shift_studio.topology_routing import _adopt
  a=shapely.box(0,0,10,10);b=shapely.box(10,0,100,10);tiny=shapely.box(70,10,71,10.0001)
  q=dict(regions=dict(A=a,B=b,C=shapely.Polygon()),moved_cut=shapely.LineString([(0,0),(0,10)]),extra_cut=shapely.LineString([(10,0),(10,10)]))
  geometry=shapely.union_all([a,b,tiny]);_adopt([q],geometry,.001,'test')
  self.assertLess(q['regions']['A'].bounds[2],10.01)
  self.assertLess(shapely.union_all(list(q['regions'].values())).symmetric_difference(geometry).area,1e-8)
 def test_manual_box_overrides_ignored_and_uses_straight_blades(self):
  from test_v49_reconstruction import V49ReconstructionTests
  from die_shift_studio.manual_regions import partition
  e,_,_=V49ReconstructionTests().fixture()
  try:
   polygon=shapely.box(80,-30,180,15)
   cfg=replace(e.reconstructor.settings,reconstruction_ignored=(('5/0',0),),reconstruction_boxes=(('5/0',tuple(polygon.exterior.coords)[:4]),))
   e.configure_reconstruction(True,cfg);r=e.reconstructor;frame=r.frame(np.zeros((1,3)),diagnostics=False,_raw=True)
   rows=[q for q in frame['pieces'] if q['layer']=='5/0' and q['index']==0]
   self.assertTrue(rows);self.assertFalse(any(q.get('ignored') for q in rows))
   actual=shapely.union_all([q['nominal_regions']['A'] for q in rows]);expected=r.model.nodes[r.model.ids['5/0',0]]['poly'].intersection(polygon)
   self.assertLess(actual.symmetric_difference(expected).area,1e-8)
   for q in rows:
    for key in ('moved_cut','extra_cut'):
     for line in shapely.get_parts(q[key]):
      if line.geom_type=='LineString':self.assertLess(line.hausdorff_distance(shapely.LineString([line.coords[0],line.coords[-1]])),1e-6)
  finally:e.close()
 def test_new_options_legacy_defaults_and_expand_migration(self):
  from die_shift_studio.project import normalize_project
  q=normalize_project(dict(schema_version=5,roles={},settings={},expanded_settings=['DXF','实测输出设置','安全范围','无接触良率']))
  self.assertTrue(q['settings']['reconstruction_auto']);self.assertEqual(len(q['settings']['drc_checks']),8)
  self.assertEqual(q['expanded_settings'],['DXF输出参数','实测输出参数','结果分析'])
  q['settings']['drc_checks']=['width'];q['layout_output']['dbu_um']=.01
  again=normalize_project(q);self.assertEqual(again['settings']['drc_checks'],('width',));self.assertEqual(again['layout_output']['dbu_um'],.01)
if __name__=='__main__':unittest.main()
