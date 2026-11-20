"""Independent regression tests for V57 GD55_1119 fixes, low-probability dimming, auto-delete on ignore, multi-op feedback, and layer selectability."""
from dataclasses import replace
import json
import math
from pathlib import Path
import unittest

import numpy as np
import shapely
from matplotlib.colors import to_rgba

from die_shift_studio.area_filter import apply as apply_area_filter
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.current_drc import locations as drc_locations
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.project import normalize_project
from die_shift_studio.qt_compat import QtCore, QtWidgets as W
from die_shift_studio.reconstruction_overlay import FOLLOW_ALPHA, FOLLOW_COLOR
from die_shift_studio.reconstruction_probability import pose_probability
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog

ROOT = Path(__file__).resolve().parents[1]
GD55_1119_DIR = ROOT / 'Jobs' / 'GD55_1119'
GD55_1119_PROJECT = GD55_1119_DIR / 'GD55_1119.dshift.json'


def _load_gd55_1119():
    proj = normalize_project(json.loads(GD55_1119_PROJECT.read_text(encoding='utf-8')))
    raw = read_gds(str(GD55_1119_DIR / proj['gds_path']), proj['top_cell'])
    data, proj = apply_area_filter(raw, proj)
    params = Parameters(**proj['parameters'])
    cfg = ViewSettings.from_dict(proj['settings'])
    case = build_case(data, proj['roles'], layer_order=proj['layer_order'])
    engine = PreviewEngine(GeometryCache(data), case, proj['roles'], {}, params, None, case['connections'])
    engine.configure_drc(True, cfg)
    engine.configure_reconstruction(True, cfg)
    return engine, data, case, params, cfg


class V57Tests(unittest.TestCase):
    def test_gd55_1119_following_missing_region_no_fake_drc_and_single_zone(self):
        engine, _, case, params, cfg = _load_gd55_1119()
        try:
            # 1a) (-1858, 237) = 5/0 #18 and (-1149, -229) = 5/0 #68 at DIE2 X- Y- O-
            st_d2 = np.zeros((3, 3))
            st_d2[1] = [-30.0, -30.0, -np.deg2rad(2.0)]
            fr_off = engine.frame(st_d2)
            for ref in (('5/0', 18), ('5/0', 68)):
                q = next(x for x in fr_off['reconstruction']['pieces'] if (x['layer'], x['index']) == ref)
                # Must never paint an unmoved red ghost at the original position.
                self.assertTrue(q['missing_region'].equals(q['regions']['C']))

            engine.configure_reconstruction(True, replace(cfg, reconstruction_fixed_via_mode='global'))
            fr_global = engine.frame(st_d2)
            self.assertEqual(len(fr_global['reconstruction']['hard_failures']), 0)
            for ref in (('5/0', 18), ('5/0', 68)):
                q = next(x for x in fr_global['reconstruction']['pieces'] if (x['layer'], x['index']) == ref)
                self.assertTrue(q['valid'])
                self.assertFalse(q['connection_failed'])

            # Restore default cfg for 1b, 1c, 7 on 5/0 #24
            engine.configure_reconstruction(True, cfg)
            pt_1b = shapely.Point(-1875.0, -2227.0)
            for d0 in ([0, 0, 0], [30, -30, 0], [30, 0, np.deg2rad(2)], [30, -30, np.deg2rad(2)], [-30, -30, -np.deg2rad(2)]):
                st = np.zeros((3, 3))
                st[0] = d0
                fr = engine.frame(st)
                # 1c & 7: Single merged zone on 5/0 #24 DIE 0 (no pair of 14um slivers, no crisscrossing blades)
                p24 = [q for q in fr['reconstruction']['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 24, 0)]
                self.assertEqual(len(p24), 1, f'Expected single zone on 5/0 #24 DIE 0 at {d0}, got {len(p24)}')
                self.assertTrue(p24[0]['valid'])
                # 1b: No spurious DRC marker near (-1875, -2227)
                locs = drc_locations(fr.get('drc', ()))
                hits = [l for l in locs if l['geometry'].distance(pt_1b) < 150]
                self.assertEqual(hits, [], f'Unexpected DRC near (-1875,-2227) at {d0}: {hits}')
        finally:
            engine.close()

    def test_ignored_shape_auto_deletes_all_zones_including_boxes(self):
        engine, _, case, _, cfg = _load_gd55_1119()
        try:
            ref = ('5/0', 0)
            box_verts = ((0.0, -2000.0), (400.0, -2000.0), (400.0, -1940.0), (0.0, -1940.0))
            cfg_ign = replace(
                cfg,
                reconstruction_boxes=(('5/0', box_verts),),
                reconstruction_ignored=(ref,),
                reconstruction_following=(ref,),
            )
            engine.configure_reconstruction(True, cfg_ign)
            fr = engine.frame(np.zeros((3, 3)))
            pieces = [q for q in fr['reconstruction']['pieces'] if (q['layer'], q['index']) == ref]
            self.assertEqual(len(pieces), 1)
            self.assertTrue(pieces[0]['ignored'])
            self.assertTrue(pieces[0]['ignored_following'])
            self.assertIsNone(pieces[0]['route_plan'])
            self.assertTrue(pieces[0]['regions']['A'].is_empty)
        finally:
            engine.close()

    def test_pose_probability_threshold_for_dimming(self):
        _, _, case, params, _ = _load_gd55_1119()
        cat = ExtremeCatalog(case, params)
        # Single-axis shift has much higher probability than multi-DIE 3-axis corner shift.
        idx_single = cat.index([cat.sign_digits[0][(1, 0, 0)], 0, 0])
        idx_corner = cat.index([cat.sign_digits[0][(1, 1, 1)], cat.sign_digits[1][(-1, -1, -1)], 0])
        p_single = pose_probability(cat, params, idx_single)
        p_corner = pose_probability(cat, params, idx_corner)
        self.assertGreater(p_single, params.failure_threshold)
        self.assertLess(p_corner, params.failure_threshold)
        self.assertIsNone(pose_probability(cat, replace(params, target_nsigma=0.0), idx_corner))
        self.assertIsNone(pose_probability(cat, replace(params, target_nsigma=math.inf), idx_corner))

    def test_gui_selectability_staged_multi_op_follow_color_and_low_prob_dimming(self):
        from die_shift_studio.gui import MainWindow

        app = W.QApplication.instance() or W.QApplication([])
        w = MainWindow()
        w.resize(1200, 800)

        def drain(timeout_ms=60000):
            t = QtCore.QElapsedTimer()
            t.start()
            while t.elapsed() < timeout_ms:
                app.processEvents()
                if (w.worker is None and not w.preview_workers and w.cut_drag.worker is None
                        and w.motion_worker is None and w.preview_pending is None):
                    for _ in range(5):
                        app.processEvents()
                    if w.worker is None and not w.preview_workers and w.cut_drag.worker is None:
                        return
                QtCore.QThread.msleep(20)

        try:
            w.load_project(str(GD55_1119_PROJECT.resolve()))
            drain()
            # Requirement 5: 4 columns ['显示','选中','层 / 类型','用途'], only 固定RDL checked by default.
            self.assertEqual(w.layer_table.columnCount(), 4)
            self.assertEqual(w.layer_table.horizontalHeaderItem(1).text(), '选中')
            for key, role in w.roles().items():
                self.assertEqual(w.selectable_boxes[key].isChecked(), role == '固定RDL', (key, role))

            # Unchecking 5/0 in selectable_boxes blocks clicking/box-selecting 5/0 and clears existing selection.
            w.run_evaluation()
            drain()
            if not (w.current_frame or {}).get('reconstruction'):
                w.compute_reconstruction()
                drain()
            src0 = w.geometry_cache.layers['5/0']['geometry'][0]
            pt = src0.representative_point()
            self.assertIn(('5/0', 0), w.selection.hits(pt.buffer(5.0), point=True))
            w.selection.apply_hits([('5/0', 0)], 'replace')
            self.assertEqual(w.selection.selected, [('5/0', 0)])
            w.selectable_boxes['5/0'].setChecked(False)
            app.processEvents()
            self.assertEqual(w.selection.selected, [])
            self.assertNotIn(('5/0', 0), w.selection.hits(pt.buffer(5.0), point=True))
            w.selectable_boxes['5/0'].setChecked(True)
            app.processEvents()
            saved = w.project_settings()
            self.assertTrue(saved['selectability']['5/0'])
            self.assertFalse(saved['selectability']['3/0'])

            # Requirement 3 + 4: With auto=False, ignore -> change to following gives distinct staged feedback
            # and after compute_reconstruction() paints with visible FOLLOW_COLOR / FOLLOW_ALPHA and zero zones.
            ref = ('5/0', 0)
            w.change_reconstruction_ignores([ref], True)
            self.assertIn('忽略·静止', w.reconstruction_overlay.staged_overlay['actions'][ref])
            w.change_reconstruction_mode([ref], True)
            self.assertIn('忽略·随动', w.reconstruction_overlay.staged_overlay['actions'][ref])
            self.assertTrue(any(t.get_visible() and '忽略·随动' in t.get_text() for t in w.reconstruction_overlay.staged_tags))
            w.compute_reconstruction()
            drain()
            self.assertFalse(any(t.get_visible() for t in w.reconstruction_overlay.staged_tags))
            b_artist = w.reconstruction_overlay.artists.get(('5/0', 'B'))
            self.assertIsNotNone(b_artist)
            expected_rgba = np.asarray(to_rgba(FOLLOW_COLOR, FOLLOW_ALPHA))
            self.assertTrue(any(np.allclose(fc, expected_rgba, atol=1e-3) for fc in b_artist.get_facecolors()))

            # Requirement 2: Low-probability pose dims DRC overlay and reconstruction fill.
            cat = w.extremes
            low_idx = cat.index([cat.sign_digits[0][(1, 1, 1)], cat.sign_digits[1][(-1, -1, -1)], 0])
            w.active_preview = low_idx
            w.manual_states = None
            is_low, prob, thr = w.is_low_probability_pose()
            self.assertTrue(is_low and prob <= thr)
            w.drc_overlay.draw()
            self.assertAlmostEqual(float(w.drc_overlay.links.get_alpha()), 0.20, places=2)
        finally:
            if hasattr(w, 'reconstruction_yield'):
                w.reconstruction_yield.reset()
            w.close()
            t = QtCore.QElapsedTimer()
            t.start()
            while t.elapsed() < 15000 and (w.isVisible() or any(th.isRunning() for th in w.findChildren(QtCore.QThread))):
                app.processEvents()
                QtCore.QThread.msleep(20)


if __name__ == '__main__':
    unittest.main()
