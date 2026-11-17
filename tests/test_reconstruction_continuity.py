"""Independent electrical and pointer invariants for connected reconstruction."""
import unittest
import numpy as np
import shapely
from shapely.geometry import Point,LineString,box
from die_shift_studio.reconstruction_route import RoutePlan


class ContinuityTests(unittest.TestCase):
 def test_landing_margin_not_via_corner_guard_controls_point_entry(self):
  route=LineString([(0,0),(200,0)]);source=route.buffer(8).union(Point(0,0).buffer(30)).union(Point(200,0).buffer(30))
  plan=RoutePlan('2/0',0,0,(),source,route,Point(0,0).buffer(30),(Point(200,0).buffer(30),),.001,3.)
  plan.terminal_vias=((Point(0,0).buffer(10),),(Point(200,0).buffer(10),));plan.pad_modes=(True,True);plan.pad_trigger=5.
  self.assertTrue(plan.pad_hit((25,0),0),'valid required enclosure lies outside via+corner guard')
  part,_=plan.edit_pose(50,150,0,25,[0,0],np.zeros(3),'c2',cursor=(25,0))
  self.assertEqual(part['a'],0.)
  part,_=plan.edit_pose(50,150,1,175,[0,0],np.zeros(3),'c2',cursor=(175,0))
  self.assertEqual(part['b'],plan.length)
  self.assertFalse(plan.pad_hit((100,100),1))

 def test_close_return_leg_cannot_steal_arclength(self):
  route=LineString([(0,0),(100,0),(100,20),(0,20)]);source=route.buffer(4)
  plan=RoutePlan('2/0',0,0,(),source,route,Point(0,0).buffer(2),(Point(0,20).buffer(2),),.001,3.)
  self.assertGreater(route.project(Point(40,11)),150.)
  self.assertAlmostEqual(plan.drag_projection((40,11),40.,(40,0)),40.)
  s=90.;cursor=np.array([90.,0.])
  for target in ((98,0),(100,5),(100,15),(95,20)):
   next_s=plan.drag_projection(target,s,cursor);self.assertGreaterEqual(next_s,s);s=next_s;cursor=np.array(target)
  self.assertGreater(s,120.)

 def test_point_hit_is_bound_to_corresponding_terminal(self):
  route=LineString([(0,0),(200,0)]);source=box(-30,-30,230,30)
  plan=RoutePlan('2/0',0,0,(),source,route,Point(0,0).buffer(30),(Point(200,0).buffer(30),Point(120,0).buffer(30)),.001,3.)
  plan.terminal_vias=((Point(0,0).buffer(10),),(Point(200,0).buffer(10),Point(120,0).buffer(10)))
  self.assertTrue(plan.pad_hit((180,0),1));self.assertFalse(plan.pad_hit((120,0),1))

 def test_branch_pick_preserves_selected_side(self):
  from die_shift_studio.branch_regions import editable_piece
  from types import SimpleNamespace
  a=dict(moved_cut=Point(0,0),extra_cut=Point(100,0),name='a',interval=(0.,100.),route_plan=SimpleNamespace(tolerance=.001,length=100.,route=LineString([(0,0),(100,0)])))
  b=dict(moved_cut=Point(101,0),extra_cut=Point(200,0),name='b',interval=(0.,99.),route_plan=SimpleNamespace(tolerance=.001,length=99.,route=LineString([(101,0),(200,0)])))
  group=dict(graph_members=(a,b),endpoint_weights=(1.,0.),moved_cut=a['moved_cut'],extra_cut=a['extra_cut'])
  self.assertEqual(editable_piece(group,(101,0),'extra')['name'],'a')

 def test_terminal_tree_preserves_all_pair_connectivity(self):
  from die_shift_studio.branch_regions import terminal_tree
  for m,n in ((1,5),(5,1),(3,4),(9,12)):
   edges=terminal_tree([Point(0,i*30) for i in range(m)],[Point(200,j*30) for j in range(n)])
   self.assertEqual(len(edges),m+n-1)
   graph={i:set() for i in range(m+n)}
   for i,j in edges:graph[i].add(m+j);graph[m+j].add(i)
   seen={0};todo=[0]
   while todo:
    i=todo.pop();next_nodes=graph[i]-seen;seen|=next_nodes;todo.extend(next_nodes)
   self.assertEqual(len(seen),m+n)

class RoutingTests(unittest.TestCase):
 def test_incremental_field_rejects_conflicting_rigid_material(self):
  from die_shift_studio.continuous_warp import domain
  source=box(0,0,200,20)
  result,steps=domain(source,[box(0,0,130,20),box(80,0,200,20)],[1.,0.],[0,0],[0,30,0],15,.001)
  self.assertIsNone(result);self.assertEqual(steps,0)
 def test_incremental_field_keeps_real_end_interfaces_connected(self):
  from die_shift_studio.continuous_warp import domain
  source=box(-10,-10,210,10)
  result,steps=domain(source,[box(-10,-10,10,10),box(190,-10,210,10)],[1.,0.],[0,0],[0,30,0],15,.001)
  self.assertIsNotNone(result);self.assertTrue(result.is_valid)
  self.assertEqual(result.geom_type,'Polygon');self.assertFalse(result.interiors)
  self.assertTrue(result.covers(Point(0,30)));self.assertTrue(result.covers(Point(200,0)))
 def test_free_space_connector_keeps_ports_and_avoids_foreign_wall(self):
  from die_shift_studio.topology_routing import connected_candidate
  source=LineString([(0,0),(200,0)]).buffer(10)
  a=Point(0,0).buffer(12);b=Point(200,0).buffer(12);wall=box(90,-30,110,30)
  result=connected_candidate(source,a.union(b),[Point(0,0).buffer(5),Point(200,0).buffer(5)],wall,box(-50,-80,250,80),15,.001)
  self.assertIsNotNone(result);self.assertEqual(result.geom_type,'Polygon');self.assertEqual(len(result.interiors),0)
  self.assertFalse(result.intersects(wall));self.assertTrue(result.covers(Point(0,0)));self.assertTrue(result.covers(Point(200,0)))
 def test_joint_pad_separation_preserves_both_connections(self):
  from die_shift_studio.topology_routing import separate_pair
  a=Point(0,0).buffer(25);b=Point(0,30).buffer(25)
  pair=separate_pair(a,b,a,b,[Point(0,0).buffer(7)],[Point(0,30).buffer(7)],.001,10)
  self.assertIsNotNone(pair);self.assertFalse(pair[0].intersects(pair[1]));self.assertGreaterEqual(pair[0].distance(pair[1]),9.99)
  self.assertTrue(pair[0].covers(Point(0,0)));self.assertTrue(pair[1].covers(Point(0,30)))
 def test_impossible_barrier_does_not_invent_a_connection(self):
  from die_shift_studio.topology_routing import connected_candidate
  source=LineString([(0,0),(200,0)]).buffer(10)
  value=connected_candidate(source,Point(0,0).buffer(12).union(Point(200,0).buffer(12)),[Point(0,0).buffer(5),Point(200,0).buffer(5)],box(90,-200,110,200),box(-50,-50,250,50),15,.001)
  self.assertIsNone(value)
 def test_only_sub_grid_derived_seam_holes_are_removed(self):
  from die_shift_studio.reconstruction import compose_feature
  source=box(0,0,100,20);nominal=dict(A=box(40,0,60,20),B=box(60,0,100,20),C=box(0,0,40,20))
  tiny=box(49,5,49.0004,15);regions=dict(nominal,A=nominal['A'].difference(tiny))
  q=dict(die=0,nominal_regions=nominal,regions=regions,connection_failed=False,valid=True,warning='')
  g,_=compose_feature(source,[q],.001,True)
  self.assertEqual(g.geom_type,'Polygon');self.assertEqual(len(g.interiors),0);self.assertFalse(q['connection_failed'])
  self.assertLess(g.symmetric_difference(source).area,.001)

if __name__=='__main__':unittest.main()
