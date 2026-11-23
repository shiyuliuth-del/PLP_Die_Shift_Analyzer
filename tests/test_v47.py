import unittest
from unittest.mock import patch
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import shapely
from die_shift_studio.core import Parameters,GDSData,build_case
from die_shift_studio.settings import ViewSettings
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.measured_core import Session
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.statistics import ExtremeCatalog

class V47Tests(unittest.TestCase):
 def test_focus_covers_complete_die_pairs_including_approach_and_separation(self):
  case={'dies':[None]*4};p=Parameters(per_die_enabled=True,die_bounds=[[35,35,1],[25,0,1],[0,0,0],[35,0,0]])
  grid=ExtremeCatalog(case,p);focus=RepresentativeExtremes(case,p,grid)
  self.assertGreater(len(focus),26)
  for owner,local in enumerate(grid.locals):
   digits={grid.choices(i)[owner] for i in focus.indices}
   self.assertEqual(digits,set(range(len(local))))
  self.assertEqual(len(set(focus.indices)),len(focus))
  from itertools import combinations,product
  choices=[[0]*grid.count,*[grid.choices(i) for i in focus.indices]]
  for i,j in combinations(range(grid.count),2):self.assertEqual({(d[i],d[j]) for d in choices},set(product(range(grid.radices[i]),range(grid.radices[j]))))
 def engine(self,source,moving,fixed,keepout=None):
  shapes={'127/0':[shapely.box(-200,-200,700,500)],'128/0':[shapely.box(-60,-70,160,270)],
    '3/0':[g.buffer(4) for g in moving],'4/0':moving,'5/0':[source],'6/0':fixed}
  roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
  if keepout is not None:shapes['90/0']=[keepout];roles['90/0']='禁布区'
  data=GDSData('synthetic.gds','v47-'+str(source.wkb_hex),'TOP',['TOP'],.001,{k:[np.asarray(g.exterior.coords) for g in v] for k,v in shapes.items()},roles)
  case=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0'])
  p=Parameters(single_bound_x_um=25,single_bound_y_um=25,bound_angle_deg=1,line_width_um=10,line_space_um=10,annulus_um=8,moving_annulus_um=4)
  e=PreviewEngine(GeometryCache(data),case,roles,{},p,None,case['connections']);e.configure_reconstruction(True,ViewSettings())
  return e,data,case
 def test_common_skew_seam_drags_both_contributors_not_a_separate_fork(self):
  from types import SimpleNamespace
  from die_shift_studio.branch_regions import editable_piece
  def member(cut,y=0):
   plan=SimpleNamespace(route=shapely.LineString([(0,y),(100,y)]),length=100.,tolerance=.001)
   return dict(route_plan=plan,interval=(30.,80.),moved_cut=cut,extra_cut=shapely.LineString([(80,y-10),(80,y+10)]),moving_terminals=shapely.Point(0,y).buffer(3))
  a=member(shapely.LineString([(30,-10),(30,10)]));b=member(shapely.LineString([(29.9,-10),(30.1,10)]))
  separate=member(shapely.LineString([(30,90),(30,110)]),100)
  seam=shapely.LineString([(30,-10),(30,0),(30.1,10)])
  piece=dict(a,graph_members=(a,b,separate),endpoint_weights=(1.,0.),moved_cut=shapely.MultiLineString([seam,separate['moved_cut']]))
  for t in (.15,.85):
   picked=editable_piece(piece,seam.interpolate(t,normalized=True).coords[0],'moved')
   self.assertEqual({id(q['route_plan']) for q in picked['drag_members']},{id(a['route_plan']),id(b['route_plan'])})
  picked=editable_piece(piece,(30,100),'moved');self.assertEqual(len(picked['drag_members']),1)
 def test_overlapping_fixed_port_does_not_create_spurious_short_route(self):
  e,_,_=self.engine(shapely.box(-30,-30,330,30),[shapely.Point(0,0).buffer(5)],[shapely.Point(0,.3).buffer(5),shapely.Point(300,0).buffer(5)])
  try:
   from dataclasses import replace
   e.configure_reconstruction(True,replace(e.reconstructor.settings,reconstruction_allow_moving_pad=True))
   r=e.reconstructor;rows=r._plans_for_all_poses(lambda *_:None);plans=[q['plan'] for q in rows if q.get('plan')]
   self.assertEqual(len(plans),1);self.assertGreater(plans[0].length,290)
   # Both real fixed ports remain in the electrical and enclosure checks.
   self.assertEqual(len(r.rules.vias[next(iter(r.rules.vias))]),3)
   plan=plans[0];a,b=plan.nominal_first,plan.nominal_second
   point,_=plan.edit_pose(a,b,0,0.,(0,0),np.zeros(3),'linear',cursor=(0,0))
   self.assertEqual(point['near'].geom_type,'Point')
   line,_=plan.edit_pose(point['a'],point['b'],0,80.,(0,0),np.zeros(3),'linear',cursor=(80,0))
   self.assertEqual(line['near'].geom_type,'LineString');self.assertAlmostEqual(line['a'],80)
  finally:e.close()
 def test_common_moving_trunk_is_preferred_but_branches_remain_editable(self):
  source=shapely.LineString([(0,0),(100,0),(100,200),(0,200),(400,200)]).buffer(20)
  e,_,_=self.engine(source,[shapely.Point(0,y).buffer(5) for y in (0,200)],[shapely.Point(400,200).buffer(5)])
  try:
   plans=[q['plan'] for q in e.reconstructor._plans_for_all_poses(lambda *_:None) if q.get('plan')]
   self.assertEqual(len(plans),2)
   for plan in plans:
    part=plan.partition(plan.nominal_first,plan.nominal_second)
    self.assertGreater(part['near'].centroid.x,100)
    self.assertTrue(part['C'].covers(shapely.Point(0,200).buffer(5)))
   long=max(plans,key=lambda p:p.length)
   self.assertLess(long.land_end,50) # manual branch extension is NOT locked at the other pad
  finally:e.close()
 def test_measured_first_command_no_yield_grid_and_parallel_routes_reused(self):
  from test_v38 import fixture
  e,d,c=fixture();s=Session(d,c,e.roles,{},e.p,e.reconstructor.settings,e.contact.cache)
  try:
   self.assertFalse(s.instances);self.assertFalse(s.plan_pool)
   with patch('die_shift_studio.width_grid.WidthGrid',side_effect=AssertionError('probability grid')),patch('die_shift_studio.reconstruction_rules.FixedRules.candidate_mass',side_effect=AssertionError('yield')):
    s.prepare(lambda *_:None)
    with patch('die_shift_studio.reconstruction_route.trace',side_effect=AssertionError('repeated route tracing')):
     with ThreadPoolExecutor(2) as pool:
      results=list(pool.map(lambda x:s.run([dict(die=1,dx_um=x,dy_um=2.,angle_deg=0.)],preview=False),(1.,2.)))
   self.assertTrue(all(q[2]['fixed_rdl_drc_checked'] for q in results))
   self.assertTrue(all(not q[0]['contact']['regions'] for q in results))
  finally:s.close();e.close()
 def test_measured_preserves_saved_alternative_route_identity(self):
  from test_v38 import fixture
  from die_shift_studio.reconstruction_route import RoutePlan
  e,d,c=fixture();candidates={};original=RoutePlan.choose
  def remember(plan,*args):
   part=original(plan,*args)
   if part is not None:candidates[plan.identity]=plan
   return part
  try:
   with patch.object(RoutePlan,'choose',remember):e.reconstructor._plans_for_all_poses(lambda *_:None)
   self.assertGreater(len(candidates),1)
   # Preserve EACH competing trace, not merely the one fast ranking likes.
   for plan in candidates.values():
    a,b=plan.nominal_first+2,plan.nominal_second-2
    cfg=replace(e.reconstructor.settings,reconstruction_intervals=((*plan.key,plan.identity,a,b),))
    session=Session(d,c,e.roles,{},e.p,cfg,e.contact.cache)
    try:
     with patch('die_shift_studio.reconstruction_rules.FixedRules.candidate_mass',side_effect=AssertionError('unrequested probability work')):session.prepare(lambda *_:None)
     r=session.model(session.p)[1];entry=next(q for q in r._plans if q.get('plan') and q['plan'].identity==plan.identity)
     part,manual=r.interval(entry);self.assertTrue(manual);self.assertAlmostEqual(part['a'],a);self.assertAlmostEqual(part['b'],b)
    finally:session.close()
  finally:e.close()
 def test_keepout_current_copper_not_visibility_and_no_spacing_margin(self):
  source=shapely.box(-30,-20,330,20);keep=shapely.box(100,20,200,60)
  e,_,_=self.engine(source,[shapely.Point(0,0).buffer(5)],[shapely.Point(300,0).buffer(5)],keep)
  try:
   r=e.reconstructor;zero=np.zeros((1,3));f=r.frame(zero,diagnostics=False)
   self.assertFalse(f['hard_failures']) # exact shared boundary allowed
   key=next(iter(r.rules.nets));self.assertFalse(any(k=='进入禁布区' for k,g in r.rules.local(key,source,zero)))
   intrusion=shapely.union_all([source,shapely.box(150,15,160,25)])
   self.assertTrue(any(k=='进入禁布区' for k,g in r.rules.local(key,intrusion,zero)))
   from die_shift_studio.topology_routing import resolve
   out,_,_,blocked=resolve(r,{'5/0':[dict(indices=[0],geometry=intrusion)]},f['pieces'],zero)
   if not blocked:self.assertFalse(shapely.relate_pattern(out['5/0'][0]['geometry'],keep,'T********'))
   else:self.assertTrue(any('禁布区' in reason for _,reason in blocked))
  finally:e.close()
if __name__=='__main__':unittest.main()
