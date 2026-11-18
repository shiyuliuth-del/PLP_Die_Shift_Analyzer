"""Finished-copper rules and multi-zone terminal geometry regressions."""
from types import SimpleNamespace
import unittest

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio.branch_regions import assemble, editable_piece
from die_shift_studio.geometry_rules import angle_regions
from die_shift_studio.reconstruction_route import RoutePlan
from die_shift_studio.topology_checks import same_connections
from die_shift_studio.topology_routing import connected_candidate, signature


class V54Tests(unittest.TestCase):
    def test_final_outline_has_no_cut_angles(self):
        from shapely.ops import split
        from test_v38 import fixture
        from die_shift_studio.settings import ViewSettings

        source = box(0, 0, 200, 50).difference(
            shapely.Polygon([(100, 25), (115, 30), (105, 30)]))
        pieces = list(split(source, LineString([(0, 5), (200, 45)])).geoms)
        self.assertTrue(any(not angle_regions(g, 90, .001).is_empty for g in pieces))
        final = shapely.union_all(pieces)
        self.assertTrue(final.equals(source))
        self.assertTrue(angle_regions(final, 90, .001).is_empty)

        e, _, _ = fixture()
        try:
            settings = ViewSettings(drc_checks=('angle',), drc_min_angle_deg=90)
            states = np.zeros((1, 3))
            replacement = {'5/0': [dict(indices=(0,), geometry=final)]}
            from die_shift_studio.drc import GlobalDRC
            from die_shift_studio.current_drc import locations
            checker = GlobalDRC(e.contact, e.p)
            self.assertFalse(checker.frame(states, replacement, settings))
            acute = shapely.Polygon([(0, 0), (200, 0), (200, 50), (0, 50), (-25, 25)])
            replacement['5/0'][0]['geometry'] = acute
            rows = locations(checker.frame(states, replacement, settings))
            self.assertFalse(rows)  # Exactly 90° passes.
            replacement['5/0'][0]['geometry'] = shapely.Polygon(
                [(0, 0), (200, 0), (200, 50), (0, 50), (-50, 25)])
            rows = locations(checker.frame(states, replacement, settings))
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]['actual_deg'], 53.130102, places=5)
        finally:
            e.close()

    def test_virtual_ownership_seam_anchors_two_independent_zones(self):
        source = box(-20, -12, 220, 12)
        moving = Point(0, 0).buffer(6)
        seam = box(219.98, -12, 220, 12)
        anchor = box(195, -12, 220, 12)
        plan = RoutePlan('2/0', 0, 0, (0,), source, LineString([(0, 0), (210, 0)]),
                         moving, (anchor,), .001, 3.)
        plan.terminal_vias = ((moving,), ())
        rows = []
        for name, interval in [('first', (35., 85.)), ('second', (118., 176.))]:
            section = plan.partition(*interval)
            self.assertIsNotNone(section)
            rows.append(dict(die=0, route_plan=plan, route_identity=name, warning='',
                             via_refs=(('4/0', 0),), ownership_seam=seam,
                             interval=interval, moving_nominal=moving, fixed_nominal=anchor,
                             nominal_regions={k: section[k] for k in 'ABC'},
                             moved_cut=section['near'], extra_cut=section['far']))
        solver = SimpleNamespace(tol=.001, _rule_width=15.,
                                 settings=SimpleNamespace(reconstruction_style='linear'),
                                 model=SimpleNamespace(bound=SimpleNamespace(centers=np.array([[0., 0.]]))))
        result = assemble(solver, source, rows, np.array([[4., 9., .01]]))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['endpoint_weights'][0], 1.)
        self.assertLess(result[0]['endpoint_weights'][1], 1.)
        self.assertGreater(result[1]['endpoint_weights'][0], 0.)
        self.assertEqual(result[1]['endpoint_weights'][1], 0.)
        for q in result:
            self.assertTrue(q['valid'])
            near, far = q['moved_cut'], q['extra_cut']
            self.assertEqual((near.geom_type,far.geom_type), ('LineString','LineString'))
            self.assertGreater(near.distance(far), 10.)
            self.assertEqual(editable_piece(q,far.centroid.coords[0],'extra')['interval'], q['interval'])
        self.assertLess(result[0]['regions']['A'].intersection(result[1]['regions']['A']).area, 1e-6)

    def test_cut_slivers_inherit_adjacent_rigid_motion(self):
        from die_shift_studio.branch_regions import merge_rigid_cells
        left = box(0, 0, 30, 70)
        sliver = shapely.Polygon([(30, 0), (30.6, 0), (30, 35)])
        corner = box(30, 70, 40, 80)
        groups = merge_rigid_cells([left,sliver,corner], .001)
        self.assertEqual(len(groups), 2)
        self.assertTrue(any(g.equals(left.union(sliver)) for g in groups))
        self.assertTrue(any(g.equals(corner) for g in groups))

    def test_point_contact_needs_copper_and_preserves_every_port(self):
        source = box(0, 0, 100, 20)
        left = box(0, 0, 50, 20)
        right = shapely.Polygon([(50, 0), (100, 0), (100, 20), (50.001, 20)])
        current = shapely.union_all([left, right])
        self.assertEqual(signature(current), (2, 0))
        ports = [Point(x, y).buffer(3) for x, y in ((10, 5), (22, 15), (80, 5), (92, 15))]
        for count, fixed in [(1, 2), (2, 1), (2, 2)]:
            group = ports[:count] + ports[-fixed:]
            repaired = connected_candidate(source,current,group,shapely.Polygon(),None,10.,.001)
            self.assertIsNotNone(repaired)
            self.assertEqual(signature(repaired), (1, 0))
            self.assertTrue(all(repaired.covers(p) for p in group))
            self.assertLess(repaired.difference(current).area, .2)

    def test_two_disconnected_groups_cannot_exchange_terminal_incidence(self):
        source = shapely.union_all([box(0, 0, 20, 15), box(40, 0, 60, 15)])
        ports = [Point(x, 7).buffer(2) for x in (3, 12, 45, 54)]
        self.assertTrue(same_connections(source, source, ports, ports, .001))
        self.assertFalse(same_connections(source, source, ports,
                                          [ports[0],ports[2],ports[1],ports[3]], .001))


if __name__ == '__main__':
    unittest.main()
