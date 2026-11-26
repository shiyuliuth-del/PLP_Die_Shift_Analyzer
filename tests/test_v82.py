"""V82 regressions: sigma parameters, LicenseManager exe-dir defaults, save progress, GD55 2-zone middle block, and two-sided cluster routing."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import gdstk
import numpy as np
import shapely

from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache, compiled_polygons
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.license_core import build_public_key_path
from die_shift_studio.project import normalize_project
from die_shift_studio.project_results import preload as preload_results, save as save_results
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_incremental import reusable, source_settings
from die_shift_studio.reconstruction_overlay import ReconstructionOverlay
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from die_shift_studio.statistics import statistical_state
from die_shift_studio.width_grid import WidthGrid
from die_shift_studio.width_sampling import mixture_batches


ROOT = Path(__file__).resolve().parents[1]


def _two_sided_cluster_gds(path):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    top = lib.new_cell('TOP')
    # Package and 1 DIE around left moving cluster
    top.add(gdstk.rectangle((-200, -200), (800, 200), layer=127, datatype=0))
    top.add(gdstk.rectangle((-150, -150), (150, 150), layer=128, datatype=0))
    # Fixed RDL spanning from moving cluster on left to fixed cluster on right
    top.add(gdstk.rectangle((-100, -60), (0, 60), layer=5, datatype=0))
    top.add(gdstk.rectangle((0, -10), (500, 10), layer=5, datatype=0))
    top.add(gdstk.rectangle((500, -60), (600, 60), layer=5, datatype=0))
    # Two moving vias on left DIE (60 um apart, cross distance 500 um)
    for y in (-30, 30):
        top.add(gdstk.regular_polygon((-50, y), 10, 24, layer=4, datatype=0))
    # Two fixed vias on right (60 um apart, cross distance 500 um)
    for y in (-30, 30):
        top.add(gdstk.regular_polygon((550, y), 10, 24, layer=6, datatype=0))
    lib.write_gds(path)


class V82Tests(unittest.TestCase):
    def test_sigma_defaults_per_die_validation_and_probability_laws(self):
        p = Parameters()
        self.assertEqual((p.mean_x_um, p.mean_y_um, p.mean_angle_deg), (0.0, 0.0, 0.0))
        self.assertEqual((p.sigma_x_um, p.sigma_y_um, p.sigma_angle_deg), (3.0, 3.0, 0.02))
        self.assertFalse(p.per_die_sigma_enabled)

        # Decoupled from bounds / target_nsigma
        p_global = replace(p, single_bound_x_um=24.0, single_bound_y_um=18.0, bound_angle_deg=1.0,
                           target_nsigma=2.0, sigma_x_um=4.0, sigma_y_um=2.5, sigma_angle_deg=0.15)
        mu, cov, _ = statistical_state(p_global, 1)
        np.testing.assert_allclose(mu, [0.0, 0.0, 0.0])
        np.testing.assert_allclose(cov[0, 0], 4.0 ** 2)
        np.testing.assert_allclose(cov[1, 1], 2.5 ** 2)
        np.testing.assert_allclose(cov[2, 2], np.deg2rad(0.15) ** 2)

        # Per-DIE sigma mode
        p_per = replace(p, multi_bound_x_um=20.0, multi_bound_y_um=20.0, bound_angle_deg=1.0,
                        per_die_sigma_enabled=True, die_sigmas=[[2.0, 1.5, 0.05], [5.0, 4.0, 0.2]])
        _, cov0, _ = statistical_state(p_per.for_die(0), 1)
        _, cov1, _ = statistical_state(p_per.for_die(1), 1)
        np.testing.assert_allclose([cov0[0, 0], cov0[1, 1]], [4.0, 2.25])
        np.testing.assert_allclose([cov1[0, 0], cov1[1, 1]], [25.0, 16.0])

        # WidthGrid and mixture_batches reflect per-DIE sigma
        grid = WidthGrid(p_per, 2, 3)
        self.assertEqual(len(grid.weights), 2)
        self.assertGreater(grid.weights[1][grid.locals[1][:, 0] > 0].sum(),
                           grid.weights[0][grid.locals[0][:, 0] > 0].sum())
        batches = list(mixture_batches(p_per, 2, ViewSettings()))
        self.assertGreater(sum(w.sum() for _, w, _, _ in batches), 0.9)

        # Round-trip & validation
        restored = Parameters.from_dict(asdict(p_per), require_rdl=False)
        self.assertTrue(restored.per_die_sigma_enabled)
        self.assertEqual(restored.die_sigmas, [[2.0, 1.5, 0.05], [5.0, 4.0, 0.2]])
        with self.assertRaises(ValueError):
            replace(p, sigma_x_um=-1.0).validate()
        with self.assertRaises(ValueError):
            replace(p, per_die_sigma_enabled=True, die_sigmas=[[1.0, -1.0, 0.1]]).validate()

    def test_license_manager_defaults_use_exe_directory(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from die_shift_studio.qt_compat import QtWidgets as W
        from LicenseManager.main import Manager
        app = W.QApplication.instance() or W.QApplication([])
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'dist_admin' / 'LicenseManager.exe'
            exe.parent.mkdir(parents=True)
            exe.write_text('', encoding='utf-8')
            with patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, 'executable', str(exe)):
                mgr = Manager()
                try:
                    self.assertEqual(mgr.home, exe.parent)
                    self.assertEqual(Path(mgr.data.text()), exe.parent)
                    self.assertEqual(Path(mgr.output.text()), exe.parent / 'licenses')
                    self.assertEqual(mgr.universal_dir, exe.parent)
                    self.assertEqual(mgr.default_key_dir(), exe.parent / 'keys')
                finally:
                    mgr.close()
                    app.processEvents()

            root = Path(tmp)
            key_file = exe.parent / 'keys' / 'public_key.pem'
            key_file.parent.mkdir(parents=True)
            key_file.write_text('dummy', encoding='utf-8')
            with patch.dict(os.environ, {'DIE_SHIFT_LICENSE_PUBLIC_KEY': ''}):
                self.assertEqual(build_public_key_path(root), key_file)

    def test_ui_rename_sigma_controls_and_save_progress(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from die_shift_studio.qt_compat import QtWidgets as W
        from die_shift_studio.gui import MainWindow
        app = W.QApplication.instance() or W.QApplication([])
        w = MainWindow()
        try:
            labels = [lbl.text() for lbl in w.findChildren(W.QLabel)]
            self.assertIn('两侧运动动区数量', labels)
            self.assertNotIn('两侧运动的动区生成数量', labels)
            self.assertIn('sigma_x_um', w.fields)
            self.assertIn('sigma_y_um', w.fields)
            self.assertIn('sigma_angle_deg', w.fields)
            self.assertTrue(hasattr(w, 'per_die_sigma_toggle'))
            self.assertTrue(hasattr(w, 'per_die_sigma_table'))

            with TemporaryDirectory() as tmp:
                gds = Path(tmp) / 'cluster.gds'
                _two_sided_cluster_gds(gds)
                proj = Path(tmp) / 'cluster.dshift.json'
                roles = {'5/0': '固定RDL', '4/0': '运动过孔', '6/0': '固定过孔', '127/0': '封装边框', '128/0': '芯片边框'}
                proj.write_text(json.dumps({
                    'schema_version': 5,
                    'gds_path': gds.name,
                    'top_cell': 'TOP',
                    'roles': roles,
                    'parameters': {'line_width_um': 15.0, 'line_space_um': 15.0, 'annulus_um': 10.0},
                }), encoding='utf-8')
                w.load_project(proj)
                limit = time.monotonic() + 30
                while time.monotonic() < limit:
                    app.processEvents()
                    if not (w._tasks or w.preview_pending or w._coverage_pending or w.coverage_timer.isActive()):
                        break
                    time.sleep(0.02)
                seen = []
                orig_set = w.progress.setValue
                def spy_val(val):
                    seen.append(val)
                    return orig_set(val)
                with patch.object(w.progress, 'setValue', side_effect=spy_val):
                    w.save_project()
                self.assertIn(5, seen)
                self.assertIn(88, seen)
                self.assertIn(95, seen)
                saved = json.loads(proj.read_text(encoding='utf-8')).get('saved_results')
                if saved is not None:
                    self.assertIsNotNone(preload_results(dict(saved_results=saved), proj))
        finally:
            w.close()
            app.processEvents()

    def test_compiled_polygons_cache_hit(self):
        with TemporaryDirectory() as tmp:
            gds = Path(tmp) / 'cluster.gds'
            _two_sided_cluster_gds(gds)
            data = read_gds(gds)
            g1 = compiled_polygons(data, '5/0', data.layers['5/0'])
            g2 = compiled_polygons(data, '5/0', data.layers['5/0'])
            self.assertIs(g1, g2)

    def test_gd55_two_moving_zones_switch_invalidates_and_selects_between_block(self):
        job = ROOT / 'Jobs' / 'GD55_1119' / 'GD55_1119.dshift.json'
        pr = normalize_project(json.loads(job.read_text(encoding='utf-8')))
        raw = read_gds(job.parent / pr['gds_path'], pr['top_cell'])
        data, pr = restore_project_geometry(raw, pr)
        gc = GeometryCache(data)
        roles = pr['roles']
        con = Connections(data, roles, pr['layer_order'], gc.geometries)
        case = build_case(data, roles, connections=con)
        params = Parameters.from_dict(pr['parameters'], require_rdl=False)
        model = ContactModel(data, case, roles, con, gc, parameters=params)
        idx89 = min(range(len(gc.geometries['5/0'])),
                    key=lambda i: gc.geometries['5/0'][i].distance(shapely.Point(-1079, -674)))
        ref = ('5/0', idx89)
        cfg1 = replace(ViewSettings.from_dict(pr['settings']), reconstruction_two_moving_zones=1)
        cfg2 = replace(cfg1, reconstruction_two_moving_zones=2)
        self.assertNotEqual(source_settings(cfg1, ref), source_settings(cfg2, ref))
        r1 = Reconstructor(model, params, cfg1)
        try:
            r1.vias_by_target = {ref: r1.vias_by_target[ref]}
            zero = np.zeros((len(case['dies']), 3))
            r1.frame(zero, targets={ref})
            r2 = Reconstructor(model, params, cfg2, reference=r1, plan_pool=r1.plan_pool)
            r2.predecessor = r1
            try:
                _, pcs, *_ = reusable(r2, zero, {ref})
                self.assertEqual(pcs, [])
                f2 = r2.frame(zero, targets={ref})
                p89 = [q for q in f2['pieces'] if (q['layer'], q['index']) == ref]
                b89 = [b for b in f2['between_blocks'] if b['ref'] == ref]
                self.assertEqual(len(p89), 2)
                self.assertEqual(len(b89), 1)
                self.assertEqual(b89[0]['geometry'].geom_type, 'Polygon')
                self.assertGreater(b89[0]['geometry'].area, 100.0)
            finally:
                r2.close()
        finally:
            r1.close()

    def test_two_sided_clustered_vias_produce_single_middle_zone(self):
        with TemporaryDirectory() as tmp:
            gds = Path(tmp) / 'cluster.gds'
            _two_sided_cluster_gds(gds)
            data = read_gds(gds)
            roles = {'5/0': '固定RDL', '4/0': '运动过孔', '6/0': '固定过孔', '127/0': '封装边框', '128/0': '芯片边框'}
            gc = GeometryCache(data)
            con = Connections(data, roles, geometries=gc.geometries)
            case = build_case(data, roles, connections=con)
            params = Parameters(line_width_um=15.0, line_space_um=15.0, annulus_um=10.0)
            model = ContactModel(data, case, roles, con, gc, parameters=params)
            r = Reconstructor(model, params, ViewSettings(reconstruction_enabled=True))
            try:
                f = r.frame(np.array([[10.0, 0.0, 0.0]]))
                ps = [q for q in f['pieces'] if (q['layer'], q['index']) == ('5/0', 0)]
                self.assertEqual(len(ps), 1)
                self.assertFalse(ps[0].get('graph_branch'))
                self.assertFalse(ps[0].get('connection_failed'))
                # Moving zone lies on the middle segment (0..500), bounded cleanly without cluster fragments
                bounds = ps[0]['nominal_regions']['A'].bounds
                self.assertGreaterEqual(bounds[0], -1.0)
                self.assertLessEqual(bounds[2], 501.0)
                self.assertGreaterEqual(ps[0]['moved_cut'].bounds[0], 0.0)
                self.assertLessEqual(ps[0]['extra_cut'].bounds[2], 500.0)
            finally:
                r.close()

    def test_xd46_local_regression_if_present(self):
        proj_path = Path('/home/niubi/Download/xd46-sc9211cfcplr.dshift.json')
        gds_path = Path('/home/niubi/Download/PDS-XD46-SC9211CFCPLR-A0_.gds')
        if not proj_path.exists() or not gds_path.exists():
            self.skipTest('Local XD46 fixtures not present')
        proj = normalize_project(json.loads(proj_path.read_text(encoding='utf-8')))
        data = read_gds(gds_path, proj.get('top_cell'))
        data, proj = restore_project_geometry(data, proj)
        roles = proj['roles']
        gc = GeometryCache(data)
        con = Connections(data, roles, proj.get('layer_order'), gc.geometries)
        case = build_case(data, roles, connections=con)
        params = Parameters.from_dict(proj['parameters'], require_rdl=False)
        model = ContactModel(data, case, roles, con, gc, parameters=params)
        cfg = ViewSettings(reconstruction_enabled=True, reconstruction_forced=(('15/0', 11), ('15/0', 16)))
        r = Reconstructor(model, params, cfg)
        try:
            targets = {('15/0', 11), ('15/0', 16), ('15/0', 18)}
            f = r.frame(np.array([[0., 0., 0.], [15., -15., 0.]]), targets=targets)
            for idx in (11, 16, 18):
                ps = [q for q in f['pieces'] if (q['layer'], q['index']) == ('15/0', idx)]
                self.assertEqual(len(ps), 1, idx)
                self.assertFalse(ps[0].get('connection_failed'), idx)
                self.assertEqual(ps[0]['moved_cut'].geom_type, 'LineString')
                self.assertEqual(ps[0]['extra_cut'].geom_type, 'LineString')
            p18 = next(q for q in f['pieces'] if (q['layer'], q['index']) == ('15/0', 18))
            dragged = r.edited(p18, 0, (7045.0, 4280.0))
            self.assertFalse(dragged[1]['clamped'])
        finally:
            r.close()


if __name__ == '__main__':
    unittest.main()
