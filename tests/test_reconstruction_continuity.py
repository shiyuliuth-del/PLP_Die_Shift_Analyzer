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
  a=dict(moved_cut=Point(0,0),extra_cut=Point(100,0),name='a',route_plan=SimpleNamespace(tolerance=.001,route=LineString([(0,0),(100,0)])))
  b=dict(moved_cut=Point(101,0),extra_cut=Point(200,0),name='b',route_plan=SimpleNamespace(tolerance=.001,route=LineString([(101,0),(200,0)])))
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

if __name__=='__main__':unittest.main()
