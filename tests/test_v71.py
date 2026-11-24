"""V71 regressions: additive/expanding box selection, between-zone motion logic
(max/min/average + per-segment right-click override), fixed-via label layout,
stackup-agnostic ABC DXF import, right-click layer style editing, and large-layout perf."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import gdstk
import numpy as np
import shapely
from shapely.geometry import Point, box

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from die_shift_studio.between_motion import (
    LABELS,
    anchor_of,
    lookup_mode,
    pair_middle_pose,
    solve_graph_weights,
    solve_serial_weights,
)
from die_shift_studio.core import GDSData, Parameters, build_case
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_edits import key
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_import import (
    configure,
    metadata_for,
    read,
    read_domains,
    recover_metadata,
    resolve_stackup,
)
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from test_v38 import fixture
from test_v59 import two_ended


class V71Tests(unittest.TestCase):
    def test_req1_box_selection_adds_and_expands_without_deleting_existing_zones(self):
        engine, data, case = fixture()
        try:
            r0 = engine.reconstructor
            f0 = r0.frame(np.zeros((1, 3)), diagnostics=False, _raw=True)
            p0 = next(q for q in f0['pieces'] if q['layer'] == '5/0' and q['index'] == 0)
            plan = p0['route_plan']
            # Automatic generation creates <= 2 zones on a single trace interval.
            self.assertLessEqual(len([q for q in f0['pieces'] if (q['layer'], q['index']) == ('5/0', 0)]), 2)

            # Seed an existing zone [80, 160] on ('5/0', 0)
            base_cfg = replace(
                r0.settings,
                reconstruction_intervals=((*key(plan), 80., 160.),),
                reconstruction_forced=(('5/0', 0),),
            )
            # 1) Draw a disjoint aligned box covering [300, 380]: must ADD a 2nd zone without deleting [80, 160]!
            b1 = tuple(map(tuple, box(300, -40, 380, 40).exterior.coords[:-1]))
            cfg1 = replace(base_cfg, reconstruction_angle_boxes=(('5/0', b1),))
            r1 = Reconstructor(r0.model, r0.p, cfg1)
            try:
                f1 = r1.frame(np.zeros((1, 3)), diagnostics=False, _raw=True)
            finally:
                r1.close()
            z1 = sorted((q['interval'][0], q['interval'][1]) for q in f1['pieces'] if (q['layer'], q['index']) == ('5/0', 0))
            self.assertEqual(len(z1), 2)
            self.assertAlmostEqual(z1[0][0], 80., delta=2.)
            self.assertAlmostEqual(z1[0][1], 160., delta=2.)
            self.assertAlmostEqual(z1[1][0], 300., delta=5.)
            self.assertAlmostEqual(z1[1][1], 380., delta=5.)

            # 2) Draw a 3rd disjoint box [440, 520]: arbitrary N=3 zones coexist on the same graphic!
            b2 = tuple(map(tuple, box(440, -40, 520, 40).exterior.coords[:-1]))
            cfg2 = replace(cfg1, reconstruction_angle_boxes=(('5/0', b1), ('5/0', b2)))
            r2 = Reconstructor(r0.model, r0.p, cfg2)
            try:
                f2 = r2.frame(np.zeros((1, 3)), diagnostics=False, _raw=True)
            finally:
                r2.close()
            z2 = sorted((q['interval'][0], q['interval'][1]) for q in f2['pieces'] if (q['layer'], q['index']) == ('5/0', 0))
            self.assertEqual(len(z2), 3)

            # 3) Draw a box [130, 220] overlapping existing [80, 160]: expands [80, 160] -> [80, 220] while keeping b1 and b2!
            b3 = tuple(map(tuple, box(130, -40, 220, 40).exterior.coords[:-1]))
            cfg3 = replace(cfg2, reconstruction_angle_boxes=(('5/0', b1), ('5/0', b2), ('5/0', b3)))
            r3 = Reconstructor(r0.model, r0.p, cfg3)
            try:
                f3 = r3.frame(np.zeros((1, 3)), diagnostics=False, _raw=True)
            finally:
                r3.close()
            z3 = sorted((q['interval'][0], q['interval'][1]) for q in f3['pieces'] if (q['layer'], q['index']) == ('5/0', 0))
            self.assertEqual(len(z3), 3)
            self.assertAlmostEqual(z3[0][0], 80., delta=2.)
            self.assertAlmostEqual(z3[0][1], 220., delta=5.)
            self.assertAlmostEqual(z3[1][0], 300., delta=5.)
            self.assertAlmostEqual(z3[2][0], 440., delta=5.)
        finally:
            engine.close()

    def test_req2_and_req3_between_motion_math_serial_and_two_die(self):
        # Pure solver verification
        self.assertEqual(ViewSettings().reconstruction_between_motion, 'max')
        self.assertEqual(solve_serial_weights(['max']), [1.0, 1.0, 0.0])
        self.assertEqual(solve_serial_weights(['min']), [1.0, 0.0, 0.0])
        self.assertEqual(solve_serial_weights(['average']), [1.0, 0.5, 0.0])
        w_avg3 = solve_serial_weights(['average', 'average'])
        self.assertAlmostEqual(w_avg3[1], 2. / 3., places=6)
        self.assertAlmostEqual(w_avg3[2], 1. / 3., places=6)
        self.assertEqual(solve_serial_weights(['max', 'min']), [1.0, 1.0, 0.0, 0.0])
        self.assertEqual(solve_serial_weights(['max', 'average']), [1.0, 1.0, 0.5, 0.0])

        # pair_middle_pose: both moving vs one moving + one stationary
        mid_poly = box(100, -10, 200, 10)
        c1, s1 = np.array([0., 0.]), np.array([20., 0., 0.])
        c2, s2 = np.array([300., 0.]), np.array([-6., 0., 0.])
        _, st_max = pair_middle_pose(mid_poly, c1, s1, c2, s2, 'max')
        _, st_min = pair_middle_pose(mid_poly, c1, s1, c2, s2, 'min')
        _, st_avg = pair_middle_pose(mid_poly, c1, s1, c2, s2, 'average')
        np.testing.assert_allclose(st_max, [20., 0., 0.])
        np.testing.assert_allclose(st_min, [-6., 0., 0.])
        np.testing.assert_allclose(st_avg, [7., 0., 0.])
        # One side moving (+20), one side stationary (0): average moves by +10
        _, st_half = pair_middle_pose(mid_poly, c1, s1, c2, np.zeros(3), 'average')
        np.testing.assert_allclose(st_half, [10., 0., 0.])

        # Serial 2-zone trace: verify actual middle block displacement for max / min / average & per-block override
        engine, data, case = fixture()
        try:
            r0 = engine.reconstructor
            f0 = r0.frame(np.zeros((1, 3)), diagnostics=False, _raw=True)
            plan = next(q['route_plan'] for q in f0['pieces'] if (q['layer'], q['index']) == ('5/0', 0))
            b1 = tuple(map(tuple, box(340, -40, 440, 40).exterior.coords[:-1]))
            base = replace(
                r0.settings,
                reconstruction_intervals=((*key(plan), 80., 160.),),
                reconstruction_angle_boxes=(('5/0', b1),),
                reconstruction_forced=(('5/0', 0),),
            )
            shift = np.array([[20., 0., 0.]])
            displacements = {}
            block_id = None
            for mode in ('max', 'min', 'average'):
                r = Reconstructor(r0.model, r0.p, replace(base, reconstruction_between_motion=mode))
                try:
                    fr = r.frame(shift, diagnostics=False, _raw=True)
                finally:
                    r.close()
                self.assertTrue(fr.get('between_blocks'))
                blk = fr['between_blocks'][0]
                block_id = blk['id']
                self.assertEqual(blk['mode'], mode)
                dx = blk['geometry'].centroid.x - blk['nominal'].centroid.x
                displacements[mode] = dx
            self.assertAlmostEqual(displacements['max'], 20., delta=0.5)
            self.assertAlmostEqual(displacements['min'], 0., delta=0.5)
            self.assertAlmostEqual(displacements['average'], 10., delta=0.5)

            # Per-block override switches this specific block from global 'max' to 'average'
            override_cfg = replace(base, reconstruction_between_motion='max', reconstruction_between_overrides=((*block_id, 'average'),))
            r_ov = Reconstructor(r0.model, r0.p, override_cfg)
            try:
                fr_ov = r_ov.frame(shift, diagnostics=False, _raw=True)
            finally:
                r_ov.close()
            blk_ov = fr_ov['between_blocks'][0]
            self.assertEqual(blk_ov['mode'], 'average')
            self.assertTrue(blk_ov['custom'])
            self.assertAlmostEqual(blk_ov['geometry'].centroid.x - blk_ov['nominal'].centroid.x, 10., delta=0.5)
        finally:
            engine.close()

        # Two-DIE trace (both ends moving): verify max / min / average moves the shared middle core!
        e2, _, _ = two_ended()
        try:
            r2 = e2.reconstructor
            pose_2d = np.array([[16., 0., 0.], [-4., 0., 0.]])
            two_die_dx = {}
            for mode in ('max', 'min', 'average'):
                r = Reconstructor(r2.model, r2.p, replace(r2.settings, reconstruction_between_motion=mode))
                try:
                    fr = r.frame(pose_2d, diagnostics=False, _raw=True)
                finally:
                    r.close()
                self.assertTrue(fr.get('between_blocks'))
                blk = fr['between_blocks'][0]
                two_die_dx[mode] = blk['geometry'].centroid.x - blk['nominal'].centroid.x
            self.assertAlmostEqual(two_die_dx['max'], 16., delta=0.5)
            self.assertAlmostEqual(two_die_dx['min'], -4., delta=0.5)
            self.assertAlmostEqual(two_die_dx['average'], 6., delta=0.5)
        finally:
            e2.close()

    def test_req5_region_import_ignores_stackup_mismatch_and_updates_roles(self):
        engine, data, case = fixture()
        try:
            r0 = engine.reconstructor
            f0 = r0.frame(np.zeros((1, 3)))
            src = f0['pieces'][0]['route_plan']
            cfg = replace(
                r0.settings,
                reconstruction_forced=(('5/0', 0),),
                reconstruction_intervals=((*key(src), 110., 470.),),
            )
            solver = Reconstructor(r0.model, r0.p, cfg)
            try:
                frame = solver.frame(np.zeros((1, 3)))
            finally:
                solver.close()
            frame['pieces'] = [dict(q, regions=q['nominal_regions']) for q in frame['pieces']]
            dxf = DxfSettings()
            offsets = {'5/0': 0.5}
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / 'stackup_abc.dxf'
                write_regions(
                    snapshot(data, r0.model.cache), frame, engine.roles, path, settings=dxf,
                    compensation=offsets, dbu=.001,
                    edit_metadata=metadata_for(data, engine.roles, case['connections'].order, r0.p, cfg, ['5/0'], dxf, offsets),
                )
                contours, record = read(path, with_metadata=True)
                # Deliberately mismatched software stackup (5/0 marked 不参与, reversed order, zero compensation)
                wrong_roles = dict(engine.roles)
                wrong_roles['5/0'] = '不参与'
                wrong_order = list(reversed(case['connections'].order))
                wrong_comp = {'5/0': 0.0}

                # 1) With DXF metadata: resolves full DXF stackup & recovers edits cleanly
                t_roles, t_order, t_motions, t_comp = resolve_stackup(contours, record, data, wrong_roles, wrong_order, wrong_comp)
                self.assertEqual(t_roles['5/0'], '固定RDL')
                self.assertEqual(t_order, list(case['connections'].order))
                self.assertAlmostEqual(t_comp['5/0'], 0.5)
                restored = recover_metadata(record, contours, data, t_roles, t_order, r0.p, r0.settings)
                self.assertIsNotNone(restored)
                self.assertEqual(restored.reconstruction_intervals, cfg.reconstruction_intervals)

                # 2) Without metadata (external/plain DXF): still auto-promotes 5/0 to 固定RDL without error!
                t_roles2, _, _, _ = resolve_stackup(contours, None, data, wrong_roles, wrong_order, wrong_comp)
                self.assertEqual(t_roles2['5/0'], '固定RDL')
                domains = read_domains(contours, r0.model.cache, wrong_roles, offsets, dxf.scale, .001)
                self.assertIn(('5/0', 0), domains)
                imported = configure(r0.model, r0.p, r0.settings, domains)
                self.assertTrue(imported.reconstruction_intervals)
        finally:
            engine.close()

    def test_req6_large_stack_ownership_vectorized(self):
        board = box(-500, -500, 5000, 5000)
        die = box(0, 0, 4200, 4200)
        m1 = [np.array([[i * 80., j * 80.], [i * 80. + 30., j * 80.], [i * 80. + 30., j * 80. + 30.], [i * 80., j * 80. + 30.]])
              for i in range(40) for j in range(40)]
        v1 = [np.array([[i * 80. + 10., j * 80. + 10.], [i * 80. + 20., j * 80. + 10.], [i * 80. + 20., j * 80. + 20.], [i * 80. + 10., j * 80. + 20.]])
              for i in range(40) for j in range(40)]
        roles = {'127/0': '封装边框', '128/0': '芯片边框', '5/0': '固定RDL', '4/0': '运动过孔'}
        raw = GDSData(
            path='perf.gds', top_name='TOP', top_names=['TOP'], sha256='perf', dbu_um=0.001,
            layers={'127/0': [np.asarray(board.exterior.coords)], '128/0': [np.asarray(die.exterior.coords)], '5/0': m1, '4/0': v1},
            auto_roles=roles,
        )
        gc = GeometryCache(raw)
        con = Connections(raw, roles, ['5/0', '4/0', '128/0', '127/0'], gc.geometries)
        case = build_case(raw, roles, connections=con)
        self.assertEqual(len(con.ownership['4/0']), 1600)
        self.assertTrue(np.all(con.ownership['4/0'] == 0))

    def test_gui_req2_req3_req4_req5_req7_end_to_end(self):
        import importlib
        from die_shift_studio.gui import MainWindow, W
        from die_shift_studio.layer_controls import LayerStyleDialog
        from die_shift_studio.qt_compat import Qt, QtCore
        from qt_shutdown import close_window

        app = W.QApplication.instance() or W.QApplication([])
        qtest = importlib.import_module(QtCore.__name__.split('.')[0] + '.QtTest').QTest
        buttons = getattr(Qt, 'MouseButton', Qt)

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            w = MainWindow(jobs_dir=folder / 'Jobs')
            w.show()
            errors = []
            w.error = lambda m: errors.append(str(m))

            def wait():
                end = time.monotonic() + 120
                while time.monotonic() < end:
                    app.processEvents()
                    if not w._tasks and not w.preview_pending and not w.coverage_timer.isActive() and not w._coverage_pending:
                        if errors:
                            raise AssertionError(errors)
                        return
                    time.sleep(.01)
                raise AssertionError('GUI wait timeout')

            try:
                # Req 4 & Req 2 UI: 固定过孔随动逻辑 on the LEFT of 动区间随动逻辑, default='max'
                fixed_combo = w.setting_fields['reconstruction_fixed_via_mode']
                between_combo = w.setting_fields['reconstruction_between_motion']
                self.assertEqual(between_combo.currentData(), 'max')
                self.assertEqual(
                    [between_combo.itemText(i) for i in range(between_combo.count())],
                    ['跟随幅度大的一侧', '跟随幅度小的一侧', '两侧平均'],
                )
                row_parent = fixed_combo.parentWidget()
                self.assertIs(row_parent, between_combo.parentWidget())
                row_labels = [lbl.text() for lbl in row_parent.findChildren(W.QLabel)]
                self.assertIn('固定过孔随动逻辑', row_labels)
                self.assertIn('动区间随动逻辑', row_labels)
                self.assertLess(row_labels.index('固定过孔随动逻辑'), row_labels.index('动区间随动逻辑'))

                engine, data, _ = fixture()
                p, roles = engine.p, engine.roles
                engine.close()
                lib = gdstk.Library(unit=1e-6, precision=1e-9)
                cell = lib.new_cell('TOP')
                for name, rows in data.layers.items():
                    layer, datatype = map(int, name.split('/'))
                    cell.add(*(gdstk.Polygon(xy, layer=layer, datatype=datatype) for xy in rows))
                layout = folder / 'case.gds'
                lib.write_gds(layout)
                job = folder / 'case.dshift.json'
                job.write_text(json.dumps(dict(
                    schema_version=5, gds_path=str(layout), top_cell='TOP', roles=roles,
                    layer_order=['6/0', '5/0', '4/0', '3/0', '128/0', '127/0'], parameters=asdict(p),
                    settings={'animation_enabled': False, 'reconstruction_auto': True},
                    overlays={'reconstruction': True},
                )))
                w.load_project(job)
                wait()

                # Req 7: Left-click on col 2 does NOT open LayerStyleDialog; Right-click DOES!
                logical = w._layer_keys.index('5/0')
                pt = w.layer_table.visualItemRect(w.layer_table.item(logical, 2)).center()
                opened = []
                def fake_dialog(dialog):
                    opened.append(dialog.windowTitle())
                    return 1
                with patch('die_shift_studio.gui.run_dialog', side_effect=fake_dialog), \
                     patch.object(LayerStyleDialog, 'value', return_value=dict(color='#123456', alpha=.4, fill=True, width=1.0, linestyle='-')):
                    qtest.mouseClick(w.layer_table.viewport(), buttons.LeftButton, pos=pt)
                    app.processEvents()
                    self.assertEqual(opened, [])
                    qtest.mouseClick(w.layer_table.viewport(), buttons.RightButton, pos=pt)
                    app.processEvents()
                    self.assertEqual(len(opened), 1)
                    self.assertEqual(w.layer_styles['5/0']['color'], '#123456')

                # Req 3: Multi-zone intermediate block can be individually selected and switched via change_between_motion
                p0 = next(q for q in w.current_frame['reconstruction']['pieces'] if (q['layer'], q['index']) == ('5/0', 0))
                plan = p0['route_plan']
                b1 = tuple(map(tuple, box(340, -40, 440, 40).exterior.coords[:-1]))
                w.view_settings = replace(
                    w.view_settings,
                    reconstruction_intervals=((*key(plan), 80., 160.),),
                    reconstruction_angle_boxes=(('5/0', b1),),
                    reconstruction_forced=(('5/0', 0),),
                )
                w.invalidate_reconstruction_edit()
                w.request_preview(quiet=True)
                wait()
                blocks = w.current_frame['reconstruction'].get('between_blocks', ())
                self.assertEqual(len(blocks), 1)
                mid_pt = blocks[0]['geometry'].representative_point()
                w.selection.click((mid_pt.x, mid_pt.y), (10, 10), mode='replace')
                self.assertIsNotNone(w.selection.selected_between)
                self.assertEqual(w.selection.selected, [])
                self.assertEqual(w.selection.selected_between['mode'], 'max')
                w.change_between_motion(w.selection.selected_between['id'], 'average')
                wait()
                blocks_after = w.current_frame['reconstruction'].get('between_blocks', ())
                self.assertEqual(blocks_after[0]['mode'], 'average')
                self.assertTrue(blocks_after[0]['custom'])

                # Req 5: apply_stackup updates software roles & order cleanly
                new_roles = dict(roles)
                new_roles['3/0'] = '不参与'
                new_order = ['127/0', '128/0', '6/0', '5/0', '4/0', '3/0']
                w.apply_stackup(new_roles, new_order, {'5/0': 0.25})
                self.assertEqual(w.roles()['3/0'], '不参与')
                self.assertEqual(w.ordered_layers(), new_order)
                self.assertAlmostEqual(w.output_compensation()['5/0'], 0.25)
            finally:
                close_window(app, w)


if __name__ == '__main__':
    unittest.main()
