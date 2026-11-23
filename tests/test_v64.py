"""Verify the default straight moving-side boundary and opt-in pad controls."""
from dataclasses import asdict, replace
import unittest

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio.manual_regions import aligned_partition, usable_partition
from die_shift_studio.plane_route import PlaneRoute
from die_shift_studio.reconstruction_edits import key, retire_pad_intervals
from die_shift_studio.settings import ViewSettings
from test_v38 import fixture


class MovingPadTests(unittest.TestCase):
    def test_old_interval_restores_as_nearest_line_and_preserves_far_cut(self):
        engine, _, _ = fixture()
        try:
            nominal = np.zeros((1, 3))
            plan = engine.reconstructor.frame(nominal)['pieces'][0]['route_plan']
            old = {'reconstruction_intervals': ((*key(plan), 0., 520.),)}
            disallowed = ViewSettings.from_dict(old)
            self.assertFalse(disallowed.reconstruction_allow_moving_pad)
            allowed = replace(disallowed, reconstruction_allow_moving_pad=True)
            self.assertNotEqual(disallowed.reconstruction_key(), allowed.reconstruction_key())
            self.assertTrue(ViewSettings.from_dict(asdict(allowed)).reconstruction_allow_moving_pad)

            for config, expected in ((allowed, 'Point'), (disallowed, 'LineString')):
                engine.configure_reconstruction(True, config)
                result = engine.reconstructor.frame(nominal)
                piece, = result['pieces']
                current_plan = piece['route_plan']
                self.assertEqual(piece['moved_cut'].geom_type, expected)
                self.assertAlmostEqual(piece['interval'][1], 520.)
                self.assertEqual(piece['extra_cut'].geom_type, 'LineString')
                self.assertFalse(result['hard_failures'])
                drawn, _ = current_plan.edit(*piece['interval'], 0, 0.)
                self.assertEqual(drawn['near'].geom_type, expected)
                union = shapely.union_all(list(piece['nominal_regions'].values()))
                self.assertTrue(union.equals(current_plan.source))
                if not config.reconstruction_allow_moving_pad:
                    self.assertGreater(piece['interval'][0], current_plan.land_end)
                    self.assertLess(piece['interval'][0] - current_plan.land_end, .02)
                    migrated = retire_pad_intervals(config, engine.reconstructor)
                    self.assertEqual(migrated.reconstruction_intervals[0][5:], piece['interval'])
                    shifted = engine.reconstructor.frame(np.array([[-15., 12., .01]]))
                    self.assertFalse(shifted['hard_failures'])
                    self.assertEqual(shifted['pieces'][0]['moved_cut'].geom_type, 'LineString')
        finally:
            engine.close()

    def test_boxes_covering_the_pad_never_create_a_dot_when_disabled(self):
        source = box(-35., -30., 220., 30.)
        route = LineString([(0., 0.), (190., 0.)])
        landing = Point(0., 0.).buffer(20.)
        plan = PlaneRoute('5/0', 0, 0, (), source, route, landing, (Point(190., 0.).buffer(10.),), .001, 3.)
        selection = box(-40., -35., 100., 35.)
        for enabled, expected in ((True, 'Point'), (False, 'LineString')):
            plan.pad_modes = (enabled, False)
            for part in (plan.partition(0., 100.), usable_partition(plan, selection), aligned_partition(plan, selection)):
                self.assertIsNotNone(part)
                self.assertEqual(part['near'].geom_type, expected)
                self.assertEqual(part['far'].geom_type, 'LineString')
                self.assertAlmostEqual(part['b'], 100.)
                self.assertTrue(shapely.union_all([part[k] for k in 'ABC']).equals(source))
