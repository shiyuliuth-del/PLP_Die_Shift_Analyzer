"""Landing air-gap: land geometry, local search, statuses and animation cache."""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely

from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import WidthAnalyzer, classify

ROOT = Path(__file__).resolve().parents[1]
PROJECT = '示例N_三线路窄通道宽度'


def example(name=PROJECT, settings=None):
    project = json.loads((ROOT / 'examples' / f'{name}.json').read_text(encoding='utf-8'))
    data = read_gds(ROOT / 'examples' / project['gds_path'])
    case = build_case(data, project['roles'], layer_order=project['layer_order'])
    engine = PreviewEngine(GeometryCache(data), case, project['roles'], {},
                           Parameters.from_dict(project['parameters']), None, case['connections'])
    engine.configure_width(True, settings or ViewSettings())
    return engine, data, case


def shifted(count, dx):
    states = np.zeros((count, 3))
    states[0, 0] = dx
    return states


def row_for(frame, key, one_based_index):
    return next(q for q in frame['width_analysis']['rows']
                if q['key'] == key and q['index'] == one_based_index - 1)


class WidthAirGapTests(unittest.TestCase):
    def test_land_is_the_via_grown_by_the_overlap_input(self):
        # The stated rule is geometric: a circular via of radius r becomes r+x.
        bounds = shapely.Point(0., 0.).buffer(10., quad_segs=32).buffer(5., quad_segs=32).bounds
        self.assertAlmostEqual(bounds[2] - bounds[0], 30., delta=.01)  # arc discretisation
        engine, data, case = example()
        try:
            bare = row_for(engine.frame(np.zeros((2, 3))), '4/0', 1)
            via = shapely.Polygon(data.layers['4/0'][0])
            self.assertTrue(bare['land'].equals(via))
            self.assertAlmostEqual(bare['land_radius_um'], math.sqrt(via.area / math.pi), places=6)
        finally:
            engine.close()
        engine, data, case = example(settings=ViewSettings(width_overlap_um=15.))
        try:
            grown = row_for(engine.frame(np.zeros((2, 3))), '4/0', 1)
            via = shapely.Polygon(data.layers['4/0'][0])
            self.assertTrue(grown['land'].equals(via.buffer(15., quad_segs=32)))
            self.assertTrue(grown['land'].contains(via))
            self.assertGreater(grown['land_radius_um'], math.sqrt(via.area / math.pi))
        finally:
            engine.close()

    def test_shift_shrinks_the_air_gap_until_the_nets_touch(self):
        engine, data, case = example()
        try:
            start = engine.frame(np.zeros((2, 3)))
            row = row_for(start, '4/0', 1)
            self.assertEqual(row['status'], '充足')
            self.assertAlmostEqual(row['gap_um'], 32.5, places=3)
            self.assertEqual(row['target']['layer'], '5/0')
            self.assertNotEqual(row['target']['net'], row['net'])
            self.assertFalse(row['draw'])
            # Only the second DIE stands still, so the gap is a pure shift effect.
            moved = engine.frame(shifted(2, 35.))
            after = row_for(moved, '4/0', 1)
            self.assertAlmostEqual(after['gap_um'], 0., places=6)
            self.assertTrue(after['land'].intersects(engine.width_analyzer.pools['5/0']['geoms'][after['target']['index']]))
            self.assertEqual(after['status'], '不足')
            self.assertTrue(after['draw'])
            self.assertIsNotNone(after['link'])
            start_of, end_of = after['link']
            self.assertAlmostEqual(float(np.linalg.norm(np.asarray(end_of) - np.asarray(start_of))), 0., places=6)
        finally:
            engine.close()

    def test_threshold_promotes_early_warning_but_never_hides_a_violation(self):
        engine, data, case = example(settings=ViewSettings(width_overlap_um=15., width_gap_threshold_um=60.))
        try:
            row = row_for(engine.frame(np.zeros((2, 3))), '4/0', 1)
            self.assertAlmostEqual(row['gap_um'], 17.5, places=3)
            self.assertEqual(row['status'], '临界')
            self.assertTrue(row['draw'])
            self.assertAlmostEqual(row['threshold_um'], 60., places=9)
            self.assertAlmostEqual(row['rule_space_um'], 15., places=9)
        finally:
            engine.close()
        engine, data, case = example(settings=ViewSettings(width_overlap_um=15., width_gap_threshold_um=10.))
        try:
            row = row_for(engine.frame(np.zeros((2, 3))), '4/0', 1)
            # Below the threshold but still above the rule: no warning asked for.
            self.assertEqual(row['status'], '充足')
            self.assertFalse(row['draw'])
            # A real rule violation is reported whatever the threshold says.
            short = row_for(engine.frame(shifted(2, 35.)), '4/0', 1)
            self.assertEqual(short['status'], '不足')
            self.assertTrue(short['draw'])
        finally:
            engine.close()
        self.assertEqual(classify(None, 15., 60.), '充足')
        self.assertEqual(classify(14.9, 15., 60.), '不足')
        self.assertEqual(classify(15., 15., 60.), '临界')

    def test_local_radius_limits_the_search_and_is_reported(self):
        engine, data, case = example(settings=ViewSettings(width_search_radius_um=1.))
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            self.assertEqual(out['total_vias'], len(engine.width_analyzer.vias))
            self.assertEqual(out['beyond_radius'], out['total_vias'])
            self.assertTrue(all(q['gap_um'] is None for q in out['rows']))
            self.assertEqual(out['counts']['充足'], out['total_vias'])
            self.assertTrue(all(not q['draw'] for q in out['rows']))
        finally:
            engine.close()

    def test_same_net_copper_is_never_the_risk(self):
        engine, data, case = example()
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            measured = [q for q in out['rows'] if q['target'] is not None]
            self.assertTrue(measured)
            for q in measured:
                self.assertNotEqual(q['target']['net'], q['net'])
                self.assertGreaterEqual(q['gap_um'], 0.)
                self.assertIn(q['target']['layer'], q['layers'])
            self.assertEqual(out['parameters']['radius_um'], 100.)
            self.assertEqual(out['examined'], out['total_vias'])
        finally:
            engine.close()

    def test_quick_pass_only_updates_the_risks_it_located(self):
        engine, data, case = example()
        try:
            full = engine.frame(shifted(2, 35.))
            risky = [q for q in full['width_analysis']['rows'] if q['draw']]
            self.assertEqual([(q['key'], q['via_index']) for q in risky], [('4/0', 1)])
            calls = []
            original = WidthAnalyzer._measure
            def counted(self, via, states, replaced):
                calls.append((via['key'], via['index']))
                return original(self, via, states, replaced)
            with patch.object(WidthAnalyzer, '_measure', counted):
                quick = engine.frame(shifted(2, 17.5), quick=True)
            self.assertEqual(calls, [('4/0', 0)])
            self.assertFalse(quick['width_analysis']['complete'])
            self.assertEqual(quick['width_analysis']['examined'], 1)
            self.assertIn('动画', quick['width_analysis']['scope'])
            # A completed pose is memoized as a whole, not re-measured.
            with patch.object(WidthAnalyzer, '_measure', side_effect=AssertionError('cached pose re-measured')):
                self.assertIs(engine.frame(shifted(2, 35.)), full)
        finally:
            engine.close()

    def test_accepted_deformation_moves_the_measured_copper(self):
        engine, data, case = example()
        try:
            base = engine.frame(np.zeros((2, 3)))
            row = row_for(base, '4/0', 1)
            index = row['target']['index']
            copper = shapely.Polygon(data.layers['5/0'][index])
            def replaced(dx, dy):
                moved = shapely.affinity.translate(copper, dx, dy)
                return dict(base, deformation={'replacements': {'5/0': [dict(indices=(index,), geometry=moved)]}})
            # Sliding the neighbouring strip along its own length keeps the
            # shortest distance identical: the gap is a minimum, not an axis value.
            along = engine.width_analyzer.frame(replaced(0, -200))
            self.assertAlmostEqual(row_for(dict(width_analysis=along), '4/0', 1)['gap_um'],
                                   row['gap_um'], places=6)
            # Pulling it sideways widens the gap and hands the nearest spot over.
            away = replaced(200, 0)
            analysis = engine.width_analyzer.frame(away)
            updated = row_for(dict(width_analysis=analysis), '4/0', 1)
            self.assertGreater(updated['gap_um'], row['gap_um'])
            self.assertNotEqual(updated['target']['index'], index)
            self.assertIs(engine.width_analyzer.frame(away), analysis)
        finally:
            engine.close()

    def test_landing_mode_measures_against_unshifted_original_footprints(self):
        engine, data, case = example(settings=ViewSettings(width_mode='landing',
                                                           width_search_radius_um=400.,
                                                           width_gap_threshold_um=60.))
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            self.assertEqual(out['mode'], 'landing')
            self.assertEqual(out['mode_label'], '其他落盘区原始位置')
            self.assertEqual(out['target_label'], '最近异网原始落盘')
            row = row_for({'width_analysis': out}, '4/0', 1)
            self.assertEqual(row['target']['kind'], 'via')
            self.assertEqual(row['target']['layer'], '4/0')
            self.assertNotEqual(row['target']['net'], row['net'])
            self.assertAlmostEqual(row['gap_um'], 140., places=3)
            self.assertEqual(row['status'], '充足')
            # The unshifted side is static: it keeps reading the other via's
            # original footprint however far this die moves.
            moved = engine.frame(shifted(2, 90.))['width_analysis']
            near = row_for({'width_analysis': moved}, '4/0', 1)
            self.assertAlmostEqual(near['gap_um'], 50., places=3)
            self.assertEqual(near['status'], '临界')
            self.assertTrue(near['draw'])
            far = row_for({'width_analysis': moved}, '4/0', 2)
            self.assertAlmostEqual(far['gap_um'], 140., places=3)
            self.assertEqual(far['status'], '充足')
            worst = engine.frame(shifted(2, 130.))['width_analysis']
            hit = row_for({'width_analysis': worst}, '4/0', 1)
            self.assertAlmostEqual(hit['gap_um'], 10., places=3)
            self.assertEqual(hit['status'], '不足')
            self.assertGreaterEqual(worst['counts']['不足'], 1)
        finally:
            engine.close()

    def test_landing_mode_never_measures_a_via_against_itself(self):
        engine, data, case = example(settings=ViewSettings(width_mode='landing',
                                                           width_search_radius_um=400.))
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            # Measuring against its own nominal footprint would report 0 here.
            self.assertGreater(row_for({'width_analysis': out}, '4/0', 1)['gap_um'], 0.)
            for row in out['rows']:
                if row['target'] is None:
                    continue
                self.assertEqual(row['target']['kind'], 'via')
                self.assertEqual(row['target']['layer'], row['key'])
                self.assertNotEqual(row['target']['index'], row['index'])
                self.assertNotEqual(row['target']['net'], row['net'])
        finally:
            engine.close()

    def test_landing_mode_respects_the_local_radius(self):
        engine, data, case = example(settings=ViewSettings(width_mode='landing',
                                                           width_search_radius_um=100.))
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            # 140 μm apart, so nothing is comparable inside 100 μm.
            self.assertTrue(all(q['gap_um'] is None for q in out['rows'] if q['key'] == '4/0'))
            self.assertEqual(out['beyond_radius'], out['total_vias'])
            self.assertEqual(out['counts']['充足'], out['total_vias'])
            closer = engine.frame(shifted(2, 90.))['width_analysis']
            self.assertEqual(row_for({'width_analysis': closer}, '4/0', 1)['gap_um'], 50.)
        finally:
            engine.close()

    def test_mode_is_validated_and_changes_the_configuration(self):
        self.assertNotEqual(ViewSettings(width_mode='landing').width_key(),
                            ViewSettings(width_mode='vertical').width_key())
        with self.assertRaises(ValueError):
            ViewSettings.from_dict({'width_mode': 'sideways'})
        engine, data, case = example()
        try:
            self.assertEqual(engine.width_analyzer.mode, 'vertical')
            engine.configure_width(True, ViewSettings(width_mode='landing'))
            self.assertEqual(engine.width_analyzer.mode, 'landing')
            engine.configure_width(True, ViewSettings(width_mode='vertical'))
            self.assertEqual(engine.width_analyzer.mode, 'vertical')
        finally:
            engine.close()
        engine, data, case = example(settings=ViewSettings(width_mode='landing'))
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            self.assertIn('原始落盘区', out['scope'])
        finally:
            engine.close()

    def test_statuses_reach_the_extreme_state_verdict(self):
        from die_shift_studio.extreme_validation import frame_status
        engine, data, case = example()
        try:
            safe = engine.frame(np.zeros((2, 3)))
            self.assertIsNot(frame_status(safe)['safe'], False)
            unsafe = engine.frame(shifted(2, 35.))
            status = frame_status(unsafe)
            self.assertFalse(status['safe'])
            self.assertGreaterEqual(status['width_failures'], 1)
        finally:
            engine.close()

    def test_examples_without_a_vertical_neighbour_report_nothing(self):
        engine, data, case = example('示例M_不同芯片底层与Pad随动')
        try:
            out = engine.frame(np.zeros((2, 3)))['width_analysis']
            self.assertTrue(out['complete'])
            self.assertEqual(out['counts']['不足'], 0)
            self.assertEqual(out['counts']['待确认'], 0)
            self.assertEqual(out['beyond_radius'], out['total_vias'])
        finally:
            engine.close()


if __name__ == '__main__':
    unittest.main()
