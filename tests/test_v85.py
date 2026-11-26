"""V85 regressions: SH22-only example provisioning, via-tangent boundary limits, XJ88 ignored-static shapes, and posed copper on failures."""
from dataclasses import replace
import json
import os
from pathlib import Path
import time
import unittest

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_diagnostic import display
from die_shift_studio.reconstruction_route import RoutePlan
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from die_shift_studio.statistics import ExtremeCatalog
from RealDataAnalyse.projects import EXAMPLE_CASES, CASE_INPUTS, install_case


ROOT = Path(__file__).resolve().parents[1]


def _load_case(relative):
    job = ROOT / relative
    project = normalize_project(json.loads(job.read_text(encoding='utf-8')))
    data = read_gds(job.parent / project['gds_path'], project['top_cell'])
    data, project = restore_project_geometry(data, project)
    cache = GeometryCache(data)
    roles = project['roles']
    con = Connections(data, roles, project['layer_order'], cache.geometries)
    case = build_case(data, roles, connections=con)
    params = Parameters.from_dict(project['parameters'], require_rdl=False)
    model = ContactModel(data, case, roles, con, cache, parameters=params)
    return project, data, cache, case, params, model


class V85Tests(unittest.TestCase):
    def test_packaged_app_provisions_only_sh22_example(self):
        self.assertEqual(EXAMPLE_CASES, ('SH22',))
        self.assertIn('SH22', CASE_INPUTS)
        self.assertIn('GD55_1119', CASE_INPUTS)
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            install_case(tmp)
            jobs = Path(tmp) / 'Jobs'
            self.assertEqual(sorted(p.name for p in jobs.iterdir()), ['SH22'])
            install_case(tmp, ('GD55_1119',))
            self.assertTrue((jobs / 'GD55_1119' / 'GD55_1119.dshift.json').is_file())

    def test_boundary_can_reach_via_tangent_not_just_pad_edge(self):
        route = LineString([(0, 0), (200, 0)])
        source = route.buffer(8).union(Point(0, 0).buffer(30)).union(Point(200, 0).buffer(30))
        plan = RoutePlan('2/0', 0, 0, (), source, route, Point(0, 0).buffer(30),
                         (Point(200, 0).buffer(30),), .001, 3.)
        plan.terminal_vias = ((Point(0, 0).buffer(10),), (Point(200, 0).buffer(10),))
        plan.pad_modes = (False, False)
        self.assertAlmostEqual(plan.land_end, 30.0)
        self.assertAlmostEqual(plan.fixed_start, 170.0)
        lo, hi = plan.via_limits()
        self.assertAlmostEqual(lo, 10.0)
        self.assertAlmostEqual(hi, 190.0)
        # Moving-side drag stops tangent to the via, inside the pad annulus.
        self.assertGreater(plan.snap(15.0, 0, 100.0), 9.9)
        self.assertLess(plan.snap(15.0, 0, 100.0), 10.01)
        # Fixed-side drag stops tangent to the fixed via, not at the pad edge.
        self.assertGreater(plan.snap(195.0, 1, 20.0), 189.9)
        self.assertLess(plan.snap(195.0, 1, 20.0), 190.0)
        # A cut through the pad annulus is a valid partition now.
        plan.cut_mode = 'manual'
        part = plan.partition(12.0, 185.0)
        self.assertIsNotNone(part)
        self.assertAlmostEqual(part['a'], 12.0)
        self.assertAlmostEqual(part['b'], 185.0)
        self.assertTrue(part['C'].covers(Point(0, 0)))
        self.assertTrue(part['B'].covers(Point(200, 0)))
        self.assertLess(shapely.union_all([part[k] for k in 'ABC']).symmetric_difference(source).area, 1e-6)
        # Automatic candidate selection still starts outside the pad annulus so
        # the rectangular continuous certificate keeps applying.
        self.assertGreaterEqual(plan.snap(plan.land_end + 5, 0, 100.0), plan.land_end + 5 - 1e-9)

    def test_xj88_ignored_static_shapes_stay_static_at_every_extreme(self):
        project, data, cache, case, params, model = _load_case('Jobs/XJ88_GDM32311/xj88.dshift.json')
        settings = ViewSettings.from_dict(project['settings'])
        refs = (('5/0', 3), ('5/0', 13))
        self.assertTrue(set(refs) <= set(settings.reconstruction_ignored))
        self.assertFalse(set(refs) & set(settings.reconstruction_following))
        rec = Reconstructor(model, params, settings)
        catalogue = ExtremeCatalog(case, params)
        try:
            for pose in catalogue.locals[0]:
                frame = rec.frame(np.array([pose]))
                for ref in refs:
                    replacement = next(x['geometry'] for x in frame['replacements']['5/0'] if x['indices'] == (ref[1],))
                    source = cache.geometries['5/0'][ref[1]]
                    self.assertLess(replacement.symmetric_difference(source).area, 1e-6, (ref, pose))
                    piece = next(q for q in frame['pieces'] if (q['layer'], q['index']) == ref)
                    self.assertTrue(piece['ignored'])
                    self.assertFalse(piece['ignored_following'])
                    self.assertEqual(piece['regions']['C'].area, 0.0)
                    self.assertAlmostEqual(piece['regions']['B'].area, source.area)
        finally:
            rec.close()

    def test_failed_shape_keeps_posed_rigid_copper_and_only_zone_is_dashed(self):
        project, data, cache, case, params, model = _load_case('Jobs/GD55_1119/GD55_1119.dshift.json')
        settings = ViewSettings.from_dict(project['settings'])
        rec = Reconstructor(model, params, settings)
        try:
            states = np.zeros((len(case['dies']), 3))
            states[1] = [0.0, 30.0, -np.deg2rad(2.0)]
            frame = rec.frame(states)
            failed = [q for q in frame['pieces'] if q.get('connection_failed') and not q.get('ignored')]
            self.assertTrue(failed)
            for q in failed:
                # The moving rigid side follows its DIE; it is not frozen.
                self.assertGreater(q['regions']['C'].area, 0.0)
                self.assertGreater(q['regions']['C'].symmetric_difference(q['nominal_regions']['C']).area, 1.0)
                self.assertFalse(q['regions']['B'].is_empty)
        finally:
            rec.close()

    def test_diagnostic_display_removes_only_the_failed_zone(self):
        source = box(0, 0, 100, 20)
        nominal = dict(A=box(40, 0, 60, 20), B=box(60, 0, 100, 20), C=box(0, 0, 40, 20))
        posed = dict(A=box(42, 0, 62, 20), B=box(60, 0, 100, 20), C=box(2, 0, 42, 20))
        piece = dict(layer='5/0', index=0, die=0, regions=dict(posed), nominal_regions=dict(nominal),
                     missing_region=box(40, 0, 62, 20), connection_failed=True, valid=False,
                     route_plan=None, warning='短路')
        result = dict(hard_failures=[((('5/0', 0)), '短路')],
                      pieces=[piece], replacements={'5/0': [dict(indices=(0,), geometry=source)]})
        shown = display(result, np.zeros((1, 2)), np.zeros((1, 3)))
        q = shown['pieces'][0]
        self.assertTrue(q.get('diagnostic'))
        self.assertTrue(q['regions']['A'].is_empty)
        self.assertIn('diagnostic_outline', q)
        copper = shown['replacements']['5/0'][0]['geometry']
        expected = posed['B'].union(posed['C'])
        self.assertLess(copper.symmetric_difference(expected).area, 1e-6)
        # The moving side stays at its posed location; the nominal tip is gone
        # and the zone between the two rigid sides is what gets dashed.
        self.assertTrue(copper.covers(Point(41.0, 10.0)))
        self.assertFalse(copper.covers(Point(1.0, 10.0)))


if __name__ == '__main__':
    unittest.main()
