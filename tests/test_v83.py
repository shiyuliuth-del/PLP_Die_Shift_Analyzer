"""V83 regressions: endpoint selection/cycling/forcing, unfrozen license bypass, timed licenses, per-DIE between menu & scrolling, and multi-branch zone creation."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import time
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import gdstk
from matplotlib.backend_bases import MouseEvent
import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio import license_core as license
from die_shift_studio.between_motion import ENDPOINT_MODES, STATIONARY, die_mode, mode_label
from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_edits import change_zone
from die_shift_studio.region_export import _prepare
from die_shift_studio.settings import ViewSettings
from die_shift_studio.shape_menu import _configure_scrollable_menu, show_shape_menu
from die_shift_studio.stack import Connections
from LicenseManager.manager import History, public_bytes, sign
from test_v42 import make_engine
from test_v59 import two_ended


ROOT = Path(__file__).resolve().parents[1]


def _mouse(canvas, event_name, xy, button=1):
    px, py = canvas.figure.axes[0].transData.transform(xy)
    return MouseEvent(event_name, canvas, px, py, button=button)


class V83Tests(unittest.TestCase):
    def test_sigma_defaults_match_v83(self):
        p = Parameters()
        self.assertEqual((p.sigma_x_um, p.sigma_y_um, p.sigma_angle_deg), (3.0, 3.0, 0.02))

    def test_unfrozen_skips_license_check_and_standalone_has_no_bat_dependency(self):
        verify = Mock()
        launch = Mock(return_value=0)
        frozen = types.ModuleType('die_shift_studio.frozen_support')
        frozen.prepare_runtime = Mock()
        frozen.hide_owned_console = Mock()
        frozen.smoke = Mock(return_value=0)
        deps = types.ModuleType('die_shift_studio.dependencies')
        deps.verify = verify
        gui = types.ModuleType('die_shift_studio.gui')
        gui.launch = launch
        gate = types.ModuleType('die_shift_studio.license_ui')
        gate.require_activation = Mock(side_effect=AssertionError('should not check license unfrozen'))
        core = types.ModuleType('die_shift_studio.license_core')
        core.LicenseError = license.LicenseError
        core.installed_authorization = Mock(side_effect=AssertionError('should not check license unfrozen'))
        modules = {m.__name__: m for m in (frozen, deps, gui, gate, core)}
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', [str(ROOT / 'main.py')]):
            with patch.object(sys, 'frozen', False, create=True):
                with self.assertRaises(SystemExit) as res:
                    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
        self.assertEqual(res.exception.code, 0)
        launch.assert_called_once_with(None, False, None, authorization=None)

        # RealDataAnalyse batch skips license check when unfrozen
        from RealDataAnalyse.main import main as rda_main
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'input.json'
            cfg.write_text(json.dumps({'database': str(Path(tmp) / 'm.db')}), encoding='utf-8')
            out = Path(tmp) / 'out.json'
            with patch.object(sys, 'argv', ['RealDataAnalyse', '--import-data', str(cfg), '--result', str(out)]), \
                 patch.object(sys, 'frozen', False, create=True), \
                 patch('RealDataAnalyse.storage.configure_logging'), \
                 patch('RealDataAnalyse.excel.import_files', return_value={'state': 'completed'}), \
                 patch.object(license, 'installed_authorization', side_effect=AssertionError('unfrozen bypass')):
                self.assertEqual(rda_main(), 0)

    def test_timed_license_months_and_unlimited_default(self):
        key = Ed25519PrivateKey.generate()
        machine = 'A' * 64
        bindings = license.MachineBindings(license.IPV4_HWID, (machine,))
        issued = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)

        # Unlimited default (0)
        p0, c0 = sign(key, machine, duration_months=0, now=issued)
        self.assertNotIn('expires_at', p0)
        self.assertEqual(license.expiry_text(p0), '永久')
        self.assertEqual(license.verify(c0, key.public_key(), bindings, now=datetime(2099, 1, 1, tzinfo=timezone.utc))['machine_id'], machine)

        # 3-month license (Jan 31 + 3 months -> Apr 30)
        p3, c3 = sign(key, machine, duration_months=3, now=issued)
        self.assertEqual(p3['duration_months'], 3)
        self.assertIn('2026-04-30', p3['expires_at'])
        self.assertIn('3 个月', license.expiry_text(p3))
        self.assertEqual(license.verify(c3, key.public_key(), bindings, now=datetime(2026, 4, 30, 11, 0, 0, tzinfo=timezone.utc))['duration_months'], 3)
        with self.assertRaisesRegex(license.LicenseError, '过期'):
            license.verify(c3, key.public_key(), bindings, now=datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc))

        # History & LicenseManager spinbox default 0
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = W.QApplication.instance() or W.QApplication([])
        from LicenseManager.main import Manager
        with TemporaryDirectory() as tmp:
            hist = History(Path(tmp))
            lic_path = hist.issue(key, machine, Path(tmp) / 'out', duration_months=6)
            self.assertTrue(lic_path.is_file())
            mgr = Manager()
            try:
                self.assertEqual(mgr.duration_months.value(), 0)
            finally:
                mgr.close()
                app.processEvents()

    def test_fixed_rdl_with_fixed_via_never_auto_follows_even_with_mismatched_via_net(self):
        # Single-DIE fixed RDL covered by DIE outline but anchored by a fixed via
        # whose moving via has a different net index in foreign/branch setups.
        with TemporaryDirectory() as tmp:
            gds = Path(tmp) / 'fixed_via.gds'
            lib = gdstk.Library(unit=1e-6, precision=1e-9)
            top = lib.new_cell('TOP')
            top.add(gdstk.rectangle((-200, -200), (600, 200), layer=4, datatype=0))
            top.add(gdstk.rectangle((-100, -100), (500, 100), layer=8, datatype=0))
            top.add(gdstk.rectangle((-50, -30), (450, 30), layer=14, datatype=0))
            top.add(gdstk.regular_polygon((0, 0), 10, 24, layer=12, datatype=0))
            top.add(gdstk.regular_polygon((400, 0), 10, 24, layer=11, datatype=0))
            lib.write_gds(gds)
            data = read_gds(gds)
            roles = {'4/0': '封装边框', '8/0': '芯片边框', '14/0': '固定RDL', '12/0': '运动过孔', '11/0': '固定过孔'}
            gc = GeometryCache(data)
            con = Connections(data, roles, geometries=gc.geometries)
            case = build_case(data, roles, connections=con)
            p = Parameters(line_width_um=15.0, line_space_um=15.0, annulus_um=10.0,
                           single_bound_x_um=15.0, single_bound_y_um=15.0, bound_angle_deg=1.0)
            model = ContactModel(data, case, roles, con, gc, parameters=p)
            r = Reconstructor(model, p, ViewSettings(reconstruction_enabled=True, reconstruction_die_mode='follow'))
            try:
                self.assertNotIn(('14/0', 0), getattr(r, 'auto_following', set()))
                f = r.frame(np.array([[15.0, 15.0, np.deg2rad(1.0)]]))
                rep = next(x['geometry'] for x in f['replacements']['14/0'] if x['indices'] == (0,))
                self.assertTrue(rep.buffer(0.01).covers(Point(400, 0)))
            finally:
                r.close()

    def test_xd46_sc58512_local_fixed_via_stays_stationary(self):
        zpath = Path('/home/niubi/Download/xd46-sc58512acplr.zip')
        if not zpath.exists():
            self.skipTest('Local xd46-sc58512acplr.zip fixture not present')
        with TemporaryDirectory() as tmp:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(tmp)
            root = Path(tmp)
            proj = normalize_project(json.loads(next(root.glob('*.dshift.json')).read_text(encoding='utf-8')))
            data = read_gds(next(root.glob('*.gds')), proj.get('top_cell'))
            data, proj = restore_project_geometry(data, proj)
            roles = proj['roles']
            gc = GeometryCache(data)
            con = Connections(data, roles, proj.get('layer_order'), gc.geometries)
            case = build_case(data, roles, connections=con)
            params = Parameters.from_dict(proj['parameters'], require_rdl=False)
            model = ContactModel(data, case, roles, con, gc, parameters=params)
            cfg = ViewSettings.from_dict(proj['settings'])
            r = Reconstructor(model, params, cfg)
            try:
                self.assertFalse(getattr(r, 'auto_following', set()))
                targets = {('14/0', 6), ('14/0', 9), ('14/0', 13)}
                r.vias_by_target = {k: v for k, v in r.vias_by_target.items() if k in targets}
                f = r.frame(np.array([[15.0, 15.0, np.deg2rad(1.0)]]), targets=targets)
                for idx, pt_xy in [(6, (5349, 1227)), (9, (5259, 1718)), (13, (5362, 2069))]:
                    pt = Point(pt_xy)
                    rep = next(x['geometry'] for x in f['replacements']['14/0'] if x['indices'] == (idx,))
                    orig = gc.geometries['14/0'][idx]
                    self.assertLess(rep.intersection(pt.buffer(20.0)).symmetric_difference(orig.intersection(pt.buffer(20.0))).area, 1e-3)
            finally:
                r.close()

    def test_qt_endpoint_selection_cycling_forcing_and_per_die_between_menu(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = W.QApplication.instance() or W.QApplication([])
        from die_shift_studio.gui import MainWindow
        with TemporaryDirectory() as tmp:
            # 1. Single-ended trace for endpoint sub-region selection, cycling, and forcing
            gds = Path(tmp) / 'single.gds'
            lib = gdstk.Library(unit=1e-6, precision=1e-9)
            top = lib.new_cell('TOP')
            top.add(gdstk.rectangle((-200, -200), (700, 200), layer=127, datatype=0))
            top.add(gdstk.rectangle((-100, -100), (100, 100), layer=128, datatype=0))
            top.add(gdstk.rectangle((-50, -15), (550, 15), layer=5, datatype=0))
            top.add(gdstk.regular_polygon((0, 0), 8, 24, layer=4, datatype=0))
            top.add(gdstk.regular_polygon((500, 0), 8, 24, layer=6, datatype=0))
            lib.write_gds(gds)
            job = Path(tmp) / 'single.dshift.json'
            job.write_text(json.dumps({
                'schema_version': 5,
                'gds_path': gds.name,
                'top_cell': 'TOP',
                'roles': {'127/0': '封装边框', '128/0': '芯片边框', '5/0': '固定RDL', '4/0': '运动过孔', '6/0': '固定过孔'},
                'parameters': {'line_width_um': 15.0, 'line_space_um': 15.0, 'annulus_um': 10.0,
                               'single_bound_x_um': 10.0, 'single_bound_y_um': 10.0, 'bound_angle_deg': 0.0},
                'settings': {'reconstruction_auto': True},
                'overlays': {'reconstruction': True},
            }), encoding='utf-8')
            w = MainWindow(jobs_dir=Path(tmp))
            w.show()
            def wait():
                limit = time.monotonic() + 60
                while time.monotonic() < limit:
                    app.processEvents()
                    if not (w._tasks or w.preview_pending or w._coverage_pending
                            or w.coverage_timer.isActive() or w.reconstruction_overlay._stage_worker):
                        return
                    time.sleep(0.02)
                self.fail('GUI work did not finish')
            try:
                w.load_project(job)
                wait()
                ov = w.reconstruction_overlay
                ep_views = ov.endpoint_views
                self.assertEqual(len(ep_views), 2)
                fixed_ep = next(b for b in ep_views.values() if b['kind'] == 'fixed')
                pt = fixed_ep['geometry'].representative_point()
                px, py = w.ax.transData.transform((pt.x, pt.y))
                # Click 1 -> select endpoint sub-region
                w.selection.click((pt.x, pt.y), (px, py))
                self.assertIsNotNone(w.selection.selected_between)
                self.assertEqual(w.selection.selected_between['id'], fixed_ep['id'])
                self.assertEqual(w.selection.selected, [])
                # Click 2 at same spot -> cycle to full shape
                w.selection.click((pt.x, pt.y), (px, py))
                self.assertIsNone(w.selection.selected_between)
                self.assertEqual(w.selection.selected, [('5/0', 0)])
                # Click 3 at same spot -> cycle back to endpoint sub-region
                w.selection.click((pt.x, pt.y), (px, py))
                self.assertIsNotNone(w.selection.selected_between)
                self.assertEqual(w.selection.selected_between['id'], fixed_ep['id'])

                # Right-click menu on endpoint -> force follow
                menus = []
                with patch.object(W.QMenu, 'popup', lambda m, *_: menus.append(m)):
                    show_shape_menu(w, _mouse(w.canvas, 'button_press_event', (pt.x, pt.y), 3), (pt.x, pt.y))
                follow_act, = [a for a in menus[0].actions() if a.text().endswith('端点随动：随动')]
                follow_act.trigger()
                wait()
                self.assertTrue(any(row[4] == 'follow' for row in w.view_settings.reconstruction_endpoint_overrides))
                snap = snapshot(w.data, w.geometry_cache, w.current_frame, reconstruction=True)
                groups, _, _, _ = _prepare(snap, w.current_frame['reconstruction'], w.roles(), DxfSettings(), None, None, w.data.dbu_um)
                a_lines = [shapely.LineString(xy) for g in groups if g.layer == 'L5_D0_A' for xy in g.paths]
                self.assertTrue(any(ln.distance(Point(550, 0)) < 20.0 for ln in a_lines))
            finally:
                w.close()
                wait()
                app.processEvents()

    def test_between_menu_layout_per_die_dxf_and_wheel_scroll(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = W.QApplication.instance() or W.QApplication([])
        # Menu order & ABC DXF test on two_ended
        engine, _, _ = two_ended()
        try:
            cfg = replace(ViewSettings(),
                          reconstruction_boxes=(('2/0', tuple(box(80, -30, 150, 30).exterior.coords[:-1])),
                                                ('2/0', tuple(box(350, -30, 420, 30).exterior.coords[:-1]))),
                          reconstruction_box_order=('free', 'free'))
            r = Reconstructor(engine.contact, engine.p, cfg)
            try:
                f0 = r.frame(np.zeros((2, 3)))
                mid = f0['between_blocks'][0]
                cfg_die = replace(cfg, reconstruction_between_overrides=((*mid['id'], die_mode(0)),))
                r2 = Reconstructor(engine.contact, engine.p, cfg_die, reference=r, plan_pool=r.plan_pool)
                try:
                    f1 = r2.frame(np.array([[10.0, 5.0, 0.0], [-10.0, 0.0, 0.0]]))
                    b1 = f1['between_blocks'][0]
                    self.assertEqual(b1['mode'], die_mode(0))
                    self.assertTrue(b1['moves'])
                    # Moves exactly with DIE 1 (+10, +5)
                    np.testing.assert_allclose(
                        np.array(b1['geometry'].centroid.coords[0]) - np.array(b1['nominal'].centroid.coords[0]),
                        [10.0, 5.0], atol=1e-6)
                    # ABC DXF classifies DIE-following between block into _A (L2_D0_A)
                    f0_die = r2.frame(np.zeros((2, 3)))
                    snap = snapshot(engine.contact.cache.data, engine.contact.cache,
                                    {'states': np.zeros((2, 3)), 'reconstruction': f0_die},
                                    reconstruction=True)
                    groups, _, _, _ = _prepare(snap, f0_die, engine.contact.roles, DxfSettings(), None, None, engine.contact.cache.data.dbu_um)
                    a_lines = [shapely.LineString(xy) for g in groups if g.layer == 'L2_D0_A' for xy in g.paths]
                    self.assertTrue(any(ln.distance(b1['nominal'].centroid) < 30.0 for ln in a_lines))

                    # Verify right-click menu order: 静止不动 -> 跟随DIE1移动 -> 跟随DIE2移动 -> 特殊随动跟随
                    fake_win = types.SimpleNamespace(
                        data=engine.contact.cache.data, cache_group=None,
                        case=engine.contact.case, engine=engine,
                        roles=lambda: engine.contact.roles,
                        ordered_layers=lambda: list(engine.contact.roles),
                        reconstruction_toggle=types.SimpleNamespace(isChecked=lambda: True),
                        width_toggle=types.SimpleNamespace(isChecked=lambda: False),
                        visible_boxes={k: types.SimpleNamespace(isChecked=lambda: True) for k in engine.contact.roles},
                        selectable_boxes={k: types.SimpleNamespace(isChecked=lambda: True) for k in engine.contact.roles},
                        reconstruction_overlay=types.SimpleNamespace(
                            pick=lambda *_: None,
                            between_views={b1['id']: b1}, endpoint_views={}, data=lambda: f0_die),
                        cut_drag=types.SimpleNamespace(_tolerance=lambda: 5.0),
                        view_settings=cfg_die, current_frame={'reconstruction': f0_die},
                        canvas=W.QWidget(), ax=types.SimpleNamespace(get_xlim=lambda: (0, 500), bbox=types.SimpleNamespace(width=500)),
                    )
                    fake_win.canvas.resize(500, 500)
                    fake_win.selection = types.SimpleNamespace(
                        selected=[], selected_between=b1,
                        hits=lambda *_, **__: [('2/0', 0)],
                        find_subregion=lambda *_: b1, draw=lambda *_: None)
                    shown_menus = []
                    with patch.object(W.QMenu, 'popup', lambda m, *_: shown_menus.append(m)):
                        show_shape_menu(fake_win, types.SimpleNamespace(x=10, y=10), tuple(b1['nominal'].centroid.coords[0]))
                    texts = [a.text() for a in shown_menus[0].actions()]
                    i_stat = next(i for i, t in enumerate(texts) if t.endswith('动区间随动：静止不动'))
                    i_d1 = next(i for i, t in enumerate(texts) if t.endswith('动区间随动：跟随DIE1移动'))
                    i_d2 = next(i for i, t in enumerate(texts) if t.endswith('动区间随动：跟随DIE2移动'))
                    i_sub = next(i for i, t in enumerate(texts) if t.endswith('特殊随动跟随'))
                    self.assertLess(i_stat, i_d1)
                    self.assertLess(i_d1, i_d2)
                    self.assertLess(i_d2, i_sub)
                    sub_texts = [a.text() for a in shown_menus[0].actions()[i_sub].menu().actions()]
                    self.assertEqual(len(sub_texts), 3)
                    self.assertTrue(any('跟随幅度大的一侧' in t for t in sub_texts))
                    self.assertTrue(any('跟随幅度小的一侧' in t for t in sub_texts))
                    self.assertTrue(any('两侧平均' in t for t in sub_texts))
                finally:
                    r2.close()
            finally:
                r.close()
        finally:
            engine.close()

        # Menu order & wheel scroll test
        m = W.QMenu()
        for i in range(45):
            m.addAction(f'Item {i}')
        fake_screen = type('S', (), {'availableGeometry': lambda self: QtCore.QRect(0, 0, 1280, 320)})()
        with patch.object(W.QApplication, 'primaryScreen', return_value=fake_screen):
            _configure_scrollable_menu(m, None, None)
        self.assertLessEqual(m.maximumHeight(), 240)
        self.assertIn('menu-scrollable: 1', m.styleSheet())
        m.show()
        app.processEvents()
        wheel = QtGui.QWheelEvent(
            QtCore.QPointF(10, 10), QtCore.QPointF(10, 10), QtCore.QPoint(0, -120), QtCore.QPoint(0, -120),
            getattr(QtCore.Qt, 'MouseButton', QtCore.Qt).NoButton,
            getattr(QtCore.Qt, 'KeyboardModifier', QtCore.Qt).NoModifier,
            getattr(QtCore.Qt, 'ScrollPhase', QtCore.Qt).NoScrollPhase, False)
        W.QApplication.sendEvent(m, wheel)
        app.processEvents()
        self.assertIsNotNone(m.activeAction())
        m.hide()
        m.deleteLater()
        app.processEvents()

    def test_multi_branch_allows_adding_and_boxing_all_branches_after_trunk_and_branch1(self):
        moving = [(-300, 0)]
        fixed = [(300, 180), (300, -180)]
        source = shapely.union_all([LineString([p, (0, 0)]).buffer(20) for p in moving + fixed])
        source = source.union(shapely.union_all([Point(p).buffer(35) for p in moving + fixed]))
        shapes = {'127/0': [box(-600, -400, 600, 400)], '128/0': [box(-360, -250, -240, 250)],
                  '3/0': [Point(p).buffer(15) for p in moving], '4/0': [Point(p).buffer(8) for p in moving],
                  '5/0': [source], '6/0': [Point(p).buffer(8) for p in fixed]}
        cfg = ViewSettings(reconstruction_enabled=True, reconstruction_forced=(('5/0', 0),))
        e, _, _ = make_engine(shapes, Parameters(single_bound_x_um=5, single_bound_y_um=5, bound_angle_deg=0, annulus_um=5), cfg)
        try:
            class Win:
                remote_locked = False
                view_settings = cfg
                current_frame = None
                reconstruction_overlay = None
                undo_stack = []
                redo_stack = []
                engine = e
                def statusBar(self): return types.SimpleNamespace(showMessage=lambda *_: None)
                def invalidate_reconstruction_edit(self): pass
                def request_preview(self, **kw): pass
            win = Win()
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))
            change_zone(win, 'delete_all', refs={('5/0', 0)})
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))

            # Add trunk -> branch 1 -> branch 2 -> box branch 2
            change_zone(win, 'add', ref=('5/0', 0), point=(-150.0, 0.0))
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))
            self.assertEqual(len(win.current_frame['reconstruction']['pieces']), 1)

            change_zone(win, 'add', ref=('5/0', 0), point=(150.0, 90.0))
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))
            self.assertEqual(len(win.current_frame['reconstruction']['pieces']), 2)

            change_zone(win, 'add', ref=('5/0', 0), point=(150.0, -90.0))
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))
            ps = win.current_frame['reconstruction']['pieces']
            self.assertEqual(len(ps), 3)
            self.assertTrue(any(q['nominal_regions']['A'].covers(Point(-150, 0)) for q in ps))
            self.assertTrue(any(q['nominal_regions']['A'].covers(Point(150, 90)) for q in ps))
            self.assertTrue(any(q['nominal_regions']['A'].covers(Point(150, -90)) for q in ps))

            box_b2 = ('5/0', tuple(box(200, -160, 260, -100).exterior.coords[:-1]))
            win.view_settings = replace(win.view_settings, reconstruction_boxes=(box_b2,),
                                        reconstruction_box_order=('free',), reconstruction_box_excluded=())
            e.configure_reconstruction(True, win.view_settings)
            win.current_frame = e.frame(np.zeros((1, 3)))
            self.assertEqual(len(win.current_frame['reconstruction']['pieces']), 4)
        finally:
            e.close()


if __name__ == '__main__':
    unittest.main()
