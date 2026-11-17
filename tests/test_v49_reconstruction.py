import unittest,tempfile,copy
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
import numpy as np
import shapely
from shapely.affinity import rotate
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

class V49ReconstructionTests(unittest.TestCase):
 def fixture(self):
  from test_v40 import make_engine
  shapes={'127/0':[shapely.box(-150,-150,500,200)],'128/0':[shapely.box(-30,-30,80,60)],
   '3/0':[shapely.Point(x,y).buffer(10) for x,y in ((0,0),(50,30))],
   '4/0':[shapely.Point(x,y).buffer(3) for x,y in ((0,0),(50,30))],
   '5/0':[shapely.box(-15,-10,315,10),shapely.box(35,20,365,40)],
   '6/0':[shapely.Point(x,y).buffer(3) for x,y in ((300,0),(350,30))]}
  return make_engine(shapes,Parameters(single_bound_x_um=25,single_bound_y_um=25,bound_angle_deg=0,line_width_um=10,line_space_um=5,annulus_um=5,moving_annulus_um=5),ViewSettings(reconstruction_boundary_mode='perpendicular'))
 def test_all_clearances_share_signed_tolerance(self):
  from die_shift_studio.drc_rules import effective_clearances,construction_rules
  p=Parameters(annulus_um=10,moving_annulus_um=8,min_die_gap_um=7,min_package_clearance_um=6,package_clearance_um=5)
  for t in (.1,-.1,20):
   q=effective_clearances(p,ViewSettings(drc_tolerance_um=t))
   for name in ('annulus_um','moving_annulus_um','min_die_gap_um','min_package_clearance_um','package_clearance_um'):self.assertAlmostEqual(getattr(q,name),max(0,getattr(p,name)-t))
  w,s=construction_rules(p,ViewSettings(drc_tolerance_um=-.1),.001)
  self.assertGreater(w,p.line_width_um+.1);self.assertGreater(s,p.line_space_um+.1)
 def test_constraint_priority_is_lexicographic_not_violation_count(self):
  from die_shift_studio.drc_rules import violation_priority as rank
  self.assertLess(rank({'angle'}),rank({'width'}));self.assertLess(rank({'width','angle'}),rank({'enclosure'}))
  self.assertLess(rank({'enclosure','width','angle'}),rank({'space'}));self.assertLess(rank({'space','width'}),rank({'short'}))
 def test_rotated_manual_box_is_exact_and_survives_settings(self):
  from die_shift_studio.reconstruction_route import RoutePlan
  from die_shift_studio.manual_regions import partition
  source=shapely.box(-20,-20,320,20);box=rotate(shapely.box(80,-60,220,60),25,origin=(150,0))
  plan=RoutePlan('5/0',0,0,(('4/0',0),),source,shapely.LineString([(0,0),(300,0)]),shapely.Point(0,0).buffer(5),(shapely.Point(300,0).buffer(5),),.001,5)
  part=partition(plan,box);self.assertFalse(part['box_invalid'])
  self.assertLess(part['A'].symmetric_difference(source.intersection(box)).area,1e-9)
  self.assertLess(shapely.union_all([part[k] for k in 'ABC']).symmetric_difference(source).area,1e-9)
  cfg=ViewSettings.from_dict({'reconstruction_boxes':[['5/0',list(box.exterior.coords)[:4]]]})
  self.assertEqual(len(cfg.reconstruction_boxes),1);self.assertNotEqual(cfg.reconstruction_key(),ViewSettings().reconstruction_key())
 def test_partial_box_is_not_silently_grown_to_cut_whole_trace(self):
  from die_shift_studio.reconstruction_route import RoutePlan
  from die_shift_studio.manual_regions import partition
  source=shapely.box(-20,-20,320,20);box=shapely.box(100,-5,200,5)
  plan=RoutePlan('5/0',0,0,(('4/0',0),),source,shapely.LineString([(0,0),(300,0)]),shapely.Point(0,0).buffer(5),(shapely.Point(300,0).buffer(5),),.001,5)
  q=partition(plan,box);self.assertTrue(q['box_invalid']);self.assertAlmostEqual(q['A'].area,1000)
 def test_parallel_automatic_regions_are_colocated(self):
  e,_,_=self.fixture()
  try:
   r=e.reconstructor;r._plans_for_all_poses(lambda *_:None)
   plans=[q['plan'] for q in r._base_plans if q['plan']]
   self.assertEqual(len(plans),2)
   endpoints=[sorted([plan.route.interpolate(s).x for s in (plan.nominal_first,plan.nominal_second)]) for plan in plans]
   np.testing.assert_allclose(endpoints[0],endpoints[1],atol=.01)
  finally:e.close()
 def test_complete_issue_pages_and_top_cell_coordinates(self):
  from die_shift_studio.measured_issues import catalogue,place,write,page
  rows=[dict(kind='线宽不足',geometry=shapely.box(i,0,i+.2,1),layer='5/0',refs=(),owner=0,dependencies=(0,)) for i in range(1025)]
  issues=catalogue({'drc':rows},{'board':shapely.box(-10,-20,2000,200)})
  self.assertEqual(issues[0]['die_ids'],[1])
  d=place(dict(drc_issues=issues,drc_count=len(issues)),[100,200])
  np.testing.assert_allclose(d['drc_issues'][0]['top_cell_bounds_um'],[110,220,110.2,221])
  with tempfile.TemporaryDirectory() as tmp:
   result=write(Path(tmp)/'issues.sqlite','task',d);self.assertEqual(len(result['drc_issues']),128);self.assertFalse(result['issues_complete'])
   all_rows=[];offset=0
   while offset is not None:
    q=page(result['issue_catalog'],offset,100);all_rows+=q['items'];offset=q['next_offset']
   self.assertEqual(all_rows,d['drc_issues'])
 def test_strict_failure_is_visible_but_explicit_partial_preserves_good_copper(self):
  from die_shift_studio.measured_core import Session
  from die_shift_studio.reconstruction_diagnostic import DiagnosticFailure
  from die_shift_studio.dxf_export import snapshot
  e,data,case=self.fixture();s=Session(data,case,e.roles,{},e.p,e.reconstructor.settings,e.contact.cache)
  try:
   s.prepare(lambda *_:None);model,r=s.model(s.p);z=np.zeros((1,3));original=r.frame(z,diagnostics=False)
   failed=copy.deepcopy(original);ref=('5/0',0)
   failed['hard_failures']=((ref,'injected local failed connection'),);failed['electrically_valid']=False
   # A subtraction can leave a sub-grid detached speck. It must not abort OAS.
   speck=shapely.box(400.0001,150.0001,400.0002,150.0002)
   for row in failed['replacements']['5/0']:
    if 0 in row['indices']:row['geometry']=row['geometry'].union(speck)
   for q in failed['pieces']:
    if (q['layer'],q['index'])==ref:q.update(connection_failed=True,hard_unroutable=True)
   rows=[dict(die=1,dx_um=0.,dy_um=0.,angle_deg=0.)]
   with patch.object(r,'frame',return_value=failed):
    with self.assertRaises(DiagnosticFailure) as caught:s.run(rows,preview=False)
    self.assertTrue(caught.exception.frame['diagnostic_only']);self.assertTrue(caught.exception.details['drc_issues']);self.assertTrue(caught.exception.frame['reconstruction']['failed_segments'])
    with self.assertRaises(ValueError):snapshot(data,e.contact.cache,caught.exception.frame,reconstruction=True)
    frame,out,details=s.run(rows,preview=False,output_policy='omit_failed_regions')
   self.assertTrue(details['partial_output']);self.assertTrue(details['fixed_rdl_drc_checked']);self.assertGreater(details['connection_failure_count'],0)
   polygons=[shapely.from_wkb(b) for b in out.layers['5/0']];before=model.nodes[model.ids['5/0',1]]['poly']
   after=min(polygons,key=lambda g:g.symmetric_difference(before).area);self.assertLess(after.symmetric_difference(before).area,1e-8)
   removed=shapely.union_all([q['regions']['A'] for q in failed['pieces'] if q['index']==0])
   actual=shapely.union_all(polygons).difference(before);source=model.nodes[model.ids[ref]]['poly']
   self.assertLess(source.symmetric_difference(actual).difference(removed).area,1e-8)
   self.assertTrue(all(g.is_valid for g in map(shapely.from_wkb,out.layers['5/0'])))
   self.assertTrue(any(q.get('quantization') for q in details['omitted_regions']))
   from die_shift_studio.oas_stream import polygons as encode
   self.assertTrue(encode(out.layers,data.dbu_um))
  finally:s.close();e.close()
if __name__=='__main__':unittest.main()
