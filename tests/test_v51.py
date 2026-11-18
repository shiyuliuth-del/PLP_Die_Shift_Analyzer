"""Boundary and measurement contracts added after the V50 controller."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from dataclasses import replace
import shapely
from die_shift_studio.settings import ViewSettings
from die_shift_studio.manual_regions import move_side
from die_shift_studio.geometry_rules import angle_regions
from die_shift_studio.straight_controls import representative
from die_shift_studio.pose_filter import filter_rules

class V51Tests(unittest.TestCase):
    def test_angle_inside_material_excludes_hole_and_concave_corner(self):
        g=shapely.Polygon([(0,0),(100,0),(100,60),(50,40),(0,60)],holes=[[(10,10),(20,11),(20,12)]])
        a=angle_regions(g,91,.001)
        self.assertFalse(a.intersects(shapely.Point(50,40).buffer(.05)))
        self.assertFalse(a.intersects(shapely.Point(10,10).buffer(.05)))
        self.assertTrue(a.intersects(shapely.Point(0,0).buffer(.05)))
        self.assertTrue(a.equals(angle_regions(shapely.reverse(g),91,.001)))

    def test_box_slide_changes_only_pressed_side_and_keeps_one_blade(self):
        land=shapely.box(-2,-2,2,2);anchor=shapely.box(98,-2,102,2)
        plan=SimpleNamespace(layer='5/0',source=shapely.box(-3,-4,103,4),tolerance=.001,route=shapely.LineString([(0,0),(100,0)]),length=100,land=land,anchors=[anchor],tangent=lambda s:(1.,0.))
        cfg=replace(ViewSettings(),reconstruction_boxes=(('5/0',((20,-12),(50,-12),(50,12),(20,12))),))
        moved,q=move_side(cfg,plan,(20,1),(28,3));self.assertEqual(q['near'].bounds,(28.,-4.,28.,4.));self.assertEqual(q['far'].bounds,(50.,-4.,50.,4.));self.assertFalse(q['box_invalid']);self.assertAlmostEqual(q['A'].area,22*8)

        from shapely.affinity import rotate
        from die_shift_studio.manual_regions import partition
        tilted=rotate(shapely.Polygon(cfg.reconstruction_boxes[0][1]),17)
        q=partition(plan,tilted)
        for name in ('near','far'):
            self.assertEqual(q[name].geom_type,'LineString');self.assertAlmostEqual(q[name].length,8/math.cos(math.radians(17)),places=6)

    def test_complex_handle_and_failure_filters(self):
        g=shapely.GeometryCollection([shapely.Point(0,0),shapely.Point(99,0)]);opp=shapely.LineString([(100,-1),(100,1)])
        self.assertTrue(representative(g,opp,.001).equals(shapely.Point(99,0)))
        e=dict(complete=True,connection_factors=['disconnected'],red_factors=['spacing']);base=dict(checked=True)
        with patch('die_shift_studio.pose_filter.current_drc_factors',return_value=['metal']):
            self.assertEqual(filter_rules(2,True,base,e),['disconnected']);self.assertEqual(set(filter_rules(1,True,base,e)),{'metal','disconnected','spacing'})
            self.assertIsNone(filter_rules(1,True,base,None))
