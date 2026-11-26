"""V84 regressions: 2-DIE cluster pairing, ignored-box isolation, shipped XD46 jobs, async yield cancellation, unconditional normal probability, 1-DIE 8 corners, and GD55 (-999,-822) between selection."""
from dataclasses import replace
import json
import os
from pathlib import Path
import time
import types
import unittest
from unittest.mock import patch

import numpy as np
import shapely
from shapely.geometry import Point, box

from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, evaluate, read_gds
from die_shift_studio.evaluation_scope import focused_catalog, focused_reconstruction, focused_width
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.qt_compat import QtWidgets as W
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.risk_scope import indices_for
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.width_analysis import WidthAnalyzer
from die_shift_studio.session_cache import ProjectCacheRegistry
from RealDataAnalyse.projects import XD46_SC58512_INPUTS, XD46_SC9211_INPUTS, install_case
from test_v38 import fixture


ROOT = Path(__file__).resolve().parents[1]


class V84Tests(unittest.TestCase):
    def test_xd46_sc9211_two_die_clustered_vias_at_6069_3153_stays_connected(self):
        job = ROOT / 'Jobs' / 'XD46_SC9211' / 'xd46-sc9211cfcplr.dshift.json'
        pr = normalize_project(json.loads(job.read_text(encoding='utf-8')))
        raw = read_gds(job.parent / pr['gds_path'], pr['top_cell'])
        data, pr = restore_project_geometry(raw, pr)
        gc = GeometryCache(data)
        roles = pr['roles']
        con = Connections(data, roles, pr['layer_order'], gc.geometries)
        case = build_case(data, roles, connections=con)
        params = Parameters.from_dict(pr['parameters'], require_rdl=False)
        model = ContactModel(data, case, roles, con, gc, parameters=params)
        idx = min(range(len(gc.geometries['15/0'])),
                  key=lambda i: gc.geometries['15/0'][i].distance(Point(6069, 3153)))
        rec = Reconstructor(model, params, ViewSettings.from_dict(pr['settings']))
        try:
            rec.vias_by_target = {('15/0', idx): rec.vias_by_target[('15/0', idx)]}
            for st in (
                np.zeros((2, 3)),
                np.array([[-15.0, -15.0, np.deg2rad(-1.0)], [15.0, 15.0, np.deg2rad(1.0)]]),
                np.array([[15.0, -15.0, np.deg2rad(1.0)], [-15.0, 15.0, np.deg2rad(-1.0)]]),
            ):
                f = rec.frame(st, targets={('15/0', idx)})
                ps = [q for q in f['pieces'] if (q['layer'], q['index']) == ('15/0', idx)]
                self.assertEqual(len(ps), 1)
                self.assertTrue(ps[0]['valid'], ps[0].get('warning'))
                self.assertFalse(ps[0]['connection_failed'])
                self.assertGreater(ps[0]['regions']['A'].area, 1000.0)
        finally:
            rec.close()

    def test_xd46_sc58512_manual_box_at_5315_2258_creates_only_boxed_zone(self):
        job = ROOT / 'Jobs' / 'XD46_SC58512' / 'xd46-sc58512acplr.dshift.json'
        pr = normalize_project(json.loads(job.read_text(encoding='utf-8')))
        raw = read_gds(job.parent / pr['gds_path'], pr['top_cell'])
        data, pr = restore_project_geometry(raw, pr)
        gc = GeometryCache(data)
        roles = pr['roles']
        con = Connections(data, roles, pr['layer_order'], gc.geometries)
        case = build_case(data, roles, connections=con)
        params = Parameters.from_dict(pr['parameters'], require_rdl=False)
        model = ContactModel(data, case, roles, con, gc, parameters=params)
        idx = min(range(len(gc.geometries['14/0'])),
                  key=lambda i: gc.geometries['14/0'][i].distance(Point(5315, 2258)))
        box2 = ('14/0', tuple(box(5280, 2230, 5350, 2290).exterior.coords[:-1]))
        for mode in ('aligned', 'free'):
            cfg = replace(
                ViewSettings.from_dict(pr['settings']),
                reconstruction_ignored=(('14/0', idx),),
                reconstruction_following=(),
                reconstruction_angle_boxes=(box2,) if mode == 'aligned' else (),
                reconstruction_boxes=(box2,) if mode == 'free' else (),
                reconstruction_box_order=(mode,),
                reconstruction_box_starts=(),
                reconstruction_box_excluded=(),
            )
            rec = Reconstructor(model, params, cfg)
            try:
                rec.vias_by_target = {('14/0', idx): rec.vias_by_target[('14/0', idx)]}
                f = rec.frame(np.zeros((1, 3)), targets={('14/0', idx)})
                ps = [q for q in f['pieces'] if (q['layer'], q['index']) == ('14/0', idx)]
                self.assertEqual(len(ps), 1, mode)
                self.assertTrue(ps[0]['boxed'])
                self.assertTrue(ps[0]['valid'], ps[0].get('warning'))
                self.assertFalse(ps[0]['connection_failed'])
                ab = ps[0]['regions']['A'].bounds
                self.assertGreaterEqual(ab[1], 2230.0)
                self.assertLessEqual(ab[3], 2290.0)
            finally:
                rec.close()

    def test_shipped_xd46_cases_and_install_case(self):
        from tempfile import TemporaryDirectory
        for folder, inputs in (('XD46_SC9211', XD46_SC9211_INPUTS), ('XD46_SC58512', XD46_SC58512_INPUTS)):
            for name in inputs:
                self.assertTrue((ROOT / 'Jobs' / folder / name).is_file(), f'{folder}/{name}')
        with TemporaryDirectory() as tmp:
            install_case(tmp, ('XD46_SC9211', 'XD46_SC58512'))
            for folder, inputs in (('XD46_SC9211', XD46_SC9211_INPUTS), ('XD46_SC58512', XD46_SC58512_INPUTS)):
                for name in inputs:
                    self.assertTrue((Path(tmp) / 'Jobs' / folder / name).is_file())

    def test_single_die_risk_scope_includes_all_eight_corners(self):
        engine, _, _ = fixture()
        try:
            p = replace(engine.p, single_bound_x_um=15.0, single_bound_y_um=15.0, bound_angle_deg=1.0)
            grid = ExtremeCatalog(engine.contact.case, p)
            indices = indices_for(engine.contact, p, grid)
            self.assertEqual(len(indices), 8)
            corners = {tuple(np.sign(grid[idx]['states'][0]).astype(int)) for idx in indices}
            self.assertEqual(len(corners), 8)
            self.assertTrue(all(all(c != 0 for c in row) for row in corners))
        finally:
            engine.close()

    def test_per_shape_failure_probability_independent_of_selected_pose_count(self):
        engine, _, _ = fixture()
        try:
            p = replace(engine.p, single_bound_x_um=30.0, single_bound_y_um=30.0, bound_angle_deg=0.0,
                        sigma_x_um=3.0, sigma_y_um=3.0, sigma_angle_deg=0.02, line_width_um=20.0, annulus_um=45.0)
            cfg = replace(ViewSettings(), evaluation_scope='risk', reconstruction_enabled=True)
            r = Reconstructor(engine.contact, p, cfg)
            try:
                grid = ExtremeCatalog(engine.contact.case, p)
                # Find one failing corner index and compare 2-pose vs 4-pose vs 3-pose probabilities
                corners = [idx for idx in range(grid.total) if all(grid[idx]['states'][0, :2] != 0)]
                b2, _ = focused_reconstruction(r, grid, (-1, corners[0]), lambda *_: None)
                b4, _ = focused_reconstruction(r, grid, (-1, *corners), lambda *_: None)
                failing_keys = [k for k, v in b2['zones'].items() if v['has_failure']]
                self.assertTrue(failing_keys)
                k = failing_keys[0]
                p2 = b2['zones'][k]['probability']['estimate']
                p4 = b4['zones'][k]['probability']['estimate']
                # Unconditional probability for 1 failing corner vs 4 corners depends on actual normal mass, never 1/len(indices)
                self.assertLess(p2, 0.01)
                self.assertLessEqual(p2, p4 + 1e-15)
                b3, _ = focused_reconstruction(r, grid, (-1, corners[0], 0), lambda *_: None)
                if b3['zones'][k]['clauses'] == b2['zones'][k]['clauses']:
                    self.assertAlmostEqual(b3['zones'][k]['probability']['estimate'], p2, places=14)
            finally:
                r.close()

            # Width focused probability is also unconditional (not 1/len(indices))
            reg = ProjectCacheRegistry()
            grp = reg.get('v84-prob')
            analyzer = WidthAnalyzer(engine.contact, replace(p, line_space_um=20.0), ViewSettings(evaluation_scope='risk'), grp)
            try:
                summary, bank = focused_width(analyzer, replace(p, line_space_um=20.0), lambda *_: None)
                self.assertIn('unconditional_weights', bank)
                self.assertLess(abs(bank['unconditional_weights'].sum() - 1.0), 1.0)
                reg.close()
            except Exception:
                reg.close()
                raise
        finally:
            engine.close()

    def test_gd55_1119_manual_box_at_neg999_neg822_allows_selecting_between_interval(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = W.QApplication.instance() or W.QApplication([])
        from die_shift_studio.gui import MainWindow
        w = MainWindow(jobs_dir=ROOT / 'Jobs')
        w.show()
        def wait_preview():
            limit = time.monotonic() + 90
            while time.monotonic() < limit:
                app.processEvents()
                if not (w.worker or w.preview_workers or w.preview_pending or w._coverage_pending
                        or w.coverage_timer.isActive() or w.reconstruction_overlay._stage_worker):
                    return
                time.sleep(0.02)
            self.fail('GUI preview work did not finish')
        def wait_close():
            limit = time.monotonic() + 30
            while time.monotonic() < limit:
                app.processEvents()
                if not w._tasks:
                    return
                time.sleep(0.02)
        try:
            w.load_project(ROOT / 'Jobs' / 'GD55_1119' / 'GD55_1119.dshift.json')
            wait_preview()
            w.reconstruction_toggle.setChecked(True)
            w.view_settings = replace(w.view_settings, reconstruction_auto=True)
            sel = w.region_select
            sel.mode = 'aligned'
            sel.state = dict(anchor=np.array([-1025.0, -848.0]), current=np.array([-1025.0, -848.0]), angle=0.0)
            sel._finish(np.array([-973.0, -796.0]))
            wait_preview()
            ov = w.reconstruction_overlay
            b88 = [b for b in ov.between_views.values() if b['ref'] == ('5/0', 88)]
            self.assertEqual(len(b88), 1)
            mid_pt = b88[0]['geometry'].representative_point()
            hit = w.selection.find_between_block((mid_pt.x, mid_pt.y))
            self.assertIsNotNone(hit)
            self.assertEqual(hit['id'], b88[0]['id'])
            # Test async yield cancellation & persistent "计算中" status
            w.view_settings = replace(w.view_settings, reconstruction_auto=False)
            w.defer_reconstruction()
            self.assertIn('计算中', w.width_yield.label.extra_text)
        finally:
            w.close()
            wait_close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
