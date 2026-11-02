"""Straight-line fixed-RDL repairs and the DRC of the repaired layout."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.repair_patch import (Repair, attribute_violations, merge_replacements,
                                           moving_via_repairs, new_violations, repair_frame,
                                           repair_replacements, repair_summary, via_width_um,
                                           violation_owners)

ROOT = Path(__file__).resolve().parents[1]
NAME = '示例N_三线路窄通道宽度'


def example(name=NAME):
    project = json.loads((ROOT / 'examples' / f'{name}.json').read_text(encoding='utf-8'))
    data = read_gds(ROOT / 'examples' / project['gds_path'])
    case = build_case(data, project['roles'], layer_order=project['layer_order'])
    parameters = Parameters.from_dict(project['parameters'])
    engine = PreviewEngine(GeometryCache(data), case, project['roles'], {}, parameters, None,
                           case['connections'])
    return engine, parameters, case


def states_for(count, dx):
    states = np.zeros((count, 3))
    states[0, 0] = dx
    return states


class RepairPatchTests(unittest.TestCase):
    def test_patch_appears_only_once_the_via_loses_contact(self):
        engine, parameters, case = example()
        try:
            model = engine.contact
            self.assertTrue(model.records)
            # A via that shifted while still landing on the copper needs no patch.
            for small in (0., 5., 35.):
                self.assertEqual(moving_via_repairs(model, states_for(len(case['dies']), small),
                                                    15., annulus_um=parameters.annulus_um), [])
            far = moving_via_repairs(model, states_for(len(case['dies']), 400.),
                                     15., annulus_um=parameters.annulus_um)
            self.assertEqual(len(far), 1)
        finally:
            engine.close()

    def test_patch_diameter_defaults_to_the_via_width_and_honours_an_override(self):
        engine, parameters, case = example()
        try:
            model = engine.contact
            states = states_for(len(case['dies']), 400.)
            record = model.by_feature[model.records[0]['key'], model.records[0]['index']]
            own = via_width_um(record['poly'])
            self.assertAlmostEqual(own, 60., places=6)
            for requested in (None, 0.):
                automatic = moving_via_repairs(model, states, requested, annulus_um=parameters.annulus_um)
                self.assertEqual([round(q.width_um, 6) for q in automatic], [60.])
            for requested in (15., 25.5, 60., 200.):
                fixed = moving_via_repairs(model, states, requested, annulus_um=parameters.annulus_um)
                self.assertEqual([q.width_um for q in fixed], [requested])
                self.assertGreater(fixed[0].patch.area, 0.)
            wide = moving_via_repairs(model, states, 120., annulus_um=parameters.annulus_um)[0]
            narrow = moving_via_repairs(model, states, 60., annulus_um=parameters.annulus_um)[0]
            # Same straight line, wider buffer: containment holds exactly.
            self.assertGreater(wide.patch.area, narrow.patch.area)
            self.assertTrue(wide.patch.covers(narrow.patch))
            length = float(np.linalg.norm(np.asarray(narrow.target) - np.asarray(narrow.anchor)))
            cap = shapely.Point(0., 0.).buffer(30., quad_segs=16).area
            self.assertAlmostEqual(narrow.patch.area, length * 60. + cap, places=6)
            with self.assertRaises(ValueError):
                moving_via_repairs(model, states, -1., annulus_um=parameters.annulus_um)
            summary = repair_frame(model, states, ViewSettings(repair_patch_width_um=25.5), parameters)['summary']
            self.assertEqual(summary['widths_um'], [25.5])
            self.assertEqual(summary['rows'][0]['width_um'], 25.5)
            automatic = repair_frame(model, states, ViewSettings(), parameters)['summary']
            self.assertEqual(automatic['widths_um'], [60.])
        finally:
            engine.close()

    def test_patch_is_a_straight_line_with_the_requested_diameter(self):
        engine, parameters, case = example()
        try:
            model = engine.contact
            states = states_for(len(case['dies']), 400.)
            # An explicit diameter keeps this check independent of the via shape.
            patch = moving_via_repairs(model, states, 15., annulus_um=parameters.annulus_um)[0]
            copper = model.cache.layers[patch.layer]['geometry'][patch.index]
            start, end = np.asarray(patch.anchor), np.asarray(patch.target)
            # offset_um is the via's displacement; the patch spans from the copper
            # to the via, so its length is the gap, not the displacement.
            self.assertAlmostEqual(patch.offset_um, 400., places=9)
            length = float(np.linalg.norm(end - start))
            self.assertTrue(copper.covers(shapely.Point(start)))
            self.assertAlmostEqual(length, float(copper.distance(shapely.Point(end))), places=6)
            self.assertLess(length, patch.offset_um)
            # Round caps: length x width plus one full disc across both ends.
            cap = shapely.Point(0., 0.).buffer(7.5, quad_segs=16).area
            self.assertAlmostEqual(patch.patch.area, length * 15. + cap, places=6)
            self.assertTrue(patch.geometry.covers(copper))
            self.assertTrue(patch.geometry.covers(patch.patch))
            # The anchor sits on the copper edge, so one round cap half is buried
            # in existing copper; the repair adds exactly the rest.
            self.assertAlmostEqual(shapely.area(copper.intersection(patch.patch)), cap / 2, places=6)
            self.assertAlmostEqual(shapely.area(patch.geometry.difference(copper)),
                                   shapely.area(patch.patch.difference(copper)), places=6)
            # The record's own via really is the one that was reconnected.
            record = model.by_feature[patch.via_key, patch.via_index]
            moved = model.moved(record['poly'], record['owner'], states)
            self.assertFalse(copper.intersects(moved))
            self.assertLessEqual(patch.geometry.distance(moved), 1e-9)
        finally:
            engine.close()

    def test_replacements_match_the_drc_contract(self):
        engine, parameters, case = example()
        try:
            model = engine.contact
            far = moving_via_repairs(model, states_for(len(case['dies']), 400.), 15.,
                                     annulus_um=parameters.annulus_um)
            mapping = repair_replacements(far)
            self.assertEqual(list(mapping), [far[0].layer])
            entry = mapping[far[0].layer][0]
            self.assertEqual(entry['indices'], (far[0].index,))
            self.assertIsInstance(entry['net'], int)
            self.assertEqual(entry['net'], model.nodes[model.ids[far[0].layer, far[0].index]]['net'])
            self.assertTrue(entry['geometry'].covers(far[0].geometry))
            self.assertEqual(merge_replacements(None, mapping)[far[0].layer][0]['net'], entry['net'])
            self.assertEqual(len(merge_replacements({'5/0': [{'indices': (0,)}]}, mapping)['5/0']), 2)
            # Two patches on one feature collapse into a single replacement.
            doubled = repair_replacements([far[0], far[0]])
            self.assertEqual(len(doubled[far[0].layer]), 1)
        finally:
            engine.close()

    def test_negative_diameter_is_refused_and_no_motion_is_skipped(self):
        engine, parameters, case = example()
        try:
            model = engine.contact
            # 0 means "as wide as the via", so only a negative request is invalid.
            with self.assertRaises(ValueError):
                moving_via_repairs(model, states_for(len(case['dies']), 400.), -1.)
            self.assertEqual(moving_via_repairs(model, np.zeros((len(case['dies']), 3)), 15.), [])
            self.assertEqual(moving_via_repairs(model, np.zeros((len(case['dies']), 3)), 0.), [])
        finally:
            engine.close()

    def test_post_patch_drc_blames_only_the_violations_the_patch_created(self):
        engine, parameters, case = example()
        try:
            result = repair_frame(engine.contact, states_for(len(case['dies']), 400.),
                                  ViewSettings(), parameters)
            summary = result['summary']
            self.assertEqual(summary['count'], 1)
            self.assertEqual(summary['layers'], ['5/0'])
            # Default diameter follows the via, not the fixed-RDL line width.
            self.assertEqual(summary['widths_um'], [60.])
            self.assertEqual(summary['rows'][0]['width_um'], 60.)
            # The straight line crosses four other nets on the same layer.
            self.assertEqual(summary['added_kinds'], {'异网短接 / 线距不足': 4})
            self.assertEqual(summary['added'], 4)
            self.assertEqual(summary['on_patch'], 4)
            self.assertEqual(summary['elsewhere'], 0)
            self.assertEqual(summary['preexisting'], summary['violations'] - 4)
            self.assertGreaterEqual(summary['preexisting'], 1)
            self.assertEqual(summary['rows'][0]['new_violations'], 4)
            self.assertEqual(summary['owners'], [0, 0, 0, 0])
            self.assertAlmostEqual(summary['window_um'], 30., places=9)
            self.assertAlmostEqual(summary['rows'][0]['offset_um'], 400., places=9)
            self.assertAlmostEqual(summary['max_offset_um'], 400., places=9)
            patch = result['repairs'][0]
            for violation in result['new_violations']:
                self.assertGreater(violation['area_um2'], 1.)
                self.assertLessEqual(patch.patch.distance(violation['geometry']), 30.)
                box = patch.patch.bounds
                bounds = violation['geometry'].bounds
                self.assertGreaterEqual(bounds[0], box[0] - 30.)
                self.assertLessEqual(bounds[2], box[2] + 30.)
        finally:
            engine.close()

    def test_no_patch_reports_no_added_violations(self):
        engine, parameters, case = example()
        try:
            result = repair_frame(engine.contact, states_for(len(case['dies']), 35.),
                                  ViewSettings(), parameters)
            summary = result['summary']
            self.assertEqual(result['repairs'], [])
            self.assertEqual(summary['count'], 0)
            self.assertEqual(summary['added'], 0)
            self.assertEqual(summary['on_patch'], 0)
            self.assertEqual(summary['preexisting'], summary['violations'])
            self.assertGreaterEqual(summary['violations'], 1)
        finally:
            engine.close()

    def test_landing_option_shrinks_the_enclosure_gap(self):
        engine, parameters, case = example()
        try:
            states = states_for(len(case['dies']), 400.)
            # The landing disc only adds copper while the patch is narrower than
            # the annulus, so compare the two options at a fixed narrow diameter.
            narrow = ViewSettings(repair_patch_width_um=15.)
            bare = repair_frame(engine.contact, states, narrow, parameters)
            landed = repair_frame(engine.contact, states, replace(narrow, repair_landing_pad=True),
                                  parameters)
            self.assertEqual(len(landed['repairs']), 1)
            target = shapely.Point(landed['repairs'][0].target)
            # The optional landing disc really covers the annulus at the via.
            self.assertTrue(landed['repairs'][0].patch.covers(target.buffer(parameters.annulus_um - 1e-6)))
            self.assertFalse(bare['repairs'][0].patch.covers(target.buffer(parameters.annulus_um)))
            self.assertGreater(landed['repairs'][0].patch.area, bare['repairs'][0].patch.area)
            self.assertLess(sum((v['geometry'].area for v in landed['violations'] if v['kind'] == '孔盘包围不足'), 0.),
                            sum((v['geometry'].area for v in bare['violations'] if v['kind'] == '孔盘包围不足'), 0.))
        finally:
            engine.close()

    def test_summary_is_json_safe_and_carries_no_geometry(self):
        engine, parameters, case = example()
        try:
            summary = repair_frame(engine.contact, states_for(len(case['dies']), 400.),
                                   ViewSettings(), parameters)['summary']
            json.dumps(summary, allow_nan=False)
            self.assertNotIn('geometry', repr(summary))
            self.assertIn('不替代工厂DRC', summary['scope'])
            self.assertIn('设备方简化模型', summary['scope'])
        finally:
            engine.close()

    def test_new_violation_diff_ignores_pure_reshaping(self):
        base = [dict(kind='线宽不足', geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))]
        # Sub-threshold reshaping of an existing marker is not a new violation.
        nudged = [dict(kind='线宽不足', geometry=Polygon([(0, 0), (10.05, 0), (10.05, 10), (0, 10)]))]
        self.assertEqual(new_violations(base, nudged, absolute_um2=1.), [])
        # A growth beyond the threshold is reported, at its real extra area.
        grown_here = [dict(kind='线宽不足', geometry=Polygon([(0, 0), (12, 0), (12, 10), (0, 10)]))]
        found = new_violations(base, grown_here, absolute_um2=1.)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]['area_um2'], 20., places=6)
        # A genuinely new area elsewhere is reported, split into its parts.
        grown = [dict(kind='线宽不足', geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
                 dict(kind='线宽不足', geometry=Polygon([(40, 0), (60, 0), (60, 10), (40, 10)]))]
        found = new_violations(base, grown, absolute_um2=1.)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]['area_um2'], 200., places=6)
        self.assertEqual(found[0]['kind'], '线宽不足')
        # A kind that only exists with the patch is reported in full.
        only = new_violations([], base, absolute_um2=1.)
        self.assertEqual(len(only), 1)
        self.assertAlmostEqual(only[0]['area_um2'], 100., places=6)
        self.assertEqual(new_violations(base, [], absolute_um2=1.), [])
        self.assertEqual(new_violations([], [], absolute_um2=1.), [])

    def test_violation_attribution_prefers_the_nearest_patch(self):
        near = Repair('5/0', 0, 1, 0, '4/0', 0, 10., (0., 0.), (10., 0.),
                      Polygon([(0, -5), (10, -5), (10, 5), (0, 5)]),
                      Polygon([(0, -5), (10, -5), (10, 5), (0, 5)]))
        far = Repair('5/0', 1, 1, 0, '4/0', 1, 10., (500., 0.), (510., 0.),
                     Polygon([(500, -5), (510, -5), (510, 5), (500, 5)]),
                     Polygon([(500, -5), (510, -5), (510, 5), (500, 5)]))
        rows = [dict(kind='异网短接 / 线距不足', geometry=Polygon([(12, -2), (18, -2), (18, 2), (12, 2)])),
                dict(kind='异网短接 / 线距不足', geometry=Polygon([(495, -2), (499, -2), (499, 2), (495, 2)])),
                dict(kind='异网短接 / 线距不足', geometry=Polygon([(300, -2), (305, -2), (305, 2), (300, 2)]))]
        hits, elsewhere = attribute_violations([near, far], rows, window_um=30.)
        self.assertEqual(hits, [1, 1])
        self.assertEqual(len(elsewhere), 1)
        self.assertEqual(elsewhere[0]['geometry'].bounds[0], 300.)
        # The click-through mapping and the counts must come from one rule.
        self.assertEqual(violation_owners([near, far], rows, window_um=30.), [0, 1, None])
        self.assertEqual(violation_owners([near, far], rows, window_um=.5), [None, None, None])
        # A window narrower than every real gap rejects all three.
        hits, elsewhere = attribute_violations([near, far], rows, window_um=.5)
        self.assertEqual(hits, [0, 0])
        self.assertEqual(len(elsewhere), 3)
        summary = repair_summary([near, far], rows, rows[:2], hits=[1, 1], elsewhere=[rows[2]])
        self.assertEqual((summary['on_patch'], summary['elsewhere']), (2, 1))
        self.assertEqual(summary['added'], 2)
        self.assertEqual(summary['preexisting'], 1)
        json.dumps(summary, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
