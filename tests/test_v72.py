"""Regression for drafted zones, posed selection, SH22 routing and ABC assignment."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest

import gdstk
import numpy as np
import shapely

from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.manual_regions import ordered_boxes
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_overlay import between_color
from die_shift_studio.settings import ViewSettings
from test_v38 import fixture
from test_v59 import two_ended

ROOT = Path(__file__).resolve().parents[1]


def vertices(*bounds):
    return tuple(shapely.box(*bounds).exterior.coords[:-1])


class V72Tests(unittest.TestCase):
    def test_sh22_cross_die_and_branch_boxes(self):
        path = ROOT / 'Jobs' / 'SH22' / 'SH22.dshift.json'
        job = normalize_project(json.loads(path.read_text(encoding='utf-8')))
        raw = read_gds(path.parent / job['gds_path'], job['top_cell'])
        data, job = restore_project_geometry(raw, job)
        params = Parameters.from_dict(job['parameters'])
        settings = ViewSettings.from_dict(job['settings'])
        cache = GeometryCache(data)
        case = build_case(data, job['roles'], layer_order=job['layer_order'])
        model = ContactModel(data, case, job['roles'], case['connections'], cache, parameters=params)
        zero = np.zeros((2, 3))
        angle = np.deg2rad(1.)
        away = np.array([[-15., 15., -angle], [15., -15., angle]])
        cross = ('2/0', vertices(-241990, 245728, -241929, 245820))
        right = ('2/0', vertices(-241500, 245970, -241390, 246040))
        left = ('2/0', vertices(-242710, 245133, -242665, 245197))
        trunk = ('2/0', vertices(-242585, 245131, -242495, 245191))
        branch = ('2/0', vertices(-242930, 246167, -242840, 246227))

        def record_at(point):
            return ('2/0', min(range(len(cache.geometries['2/0'])),
                               key=lambda i: cache.geometries['2/0'][i].distance(shapely.Point(point))))

        paired = record_at((-241964, 245776))
        trunk_ref = record_at((-242540, 245161))
        branch_ref = record_at((-242885, 246197))
        self.assertEqual(len({paired, trunk_ref, branch_ref}), 3)
        original = Reconstructor(model, params, settings)
        try:
            for boxes, aligned, ref, ignored in (
                ((cross,), (), paired, True),
                ((cross,), (right,), paired, True),
                ((trunk, left), (), trunk_ref, False),
                ((branch,), (), branch_ref, False),
            ):
                with self.subTest(ref=ref, boxes=len(boxes) + len(aligned)):
                    cfg = replace(settings, reconstruction_boxes=boxes, reconstruction_angle_boxes=aligned,
                                  reconstruction_box_order=('free',)*len(boxes)+('aligned',)*len(aligned),
                                  reconstruction_ignored=(ref,) if ignored else ())
                    solver = Reconstructor(model, params, cfg, reference=original, plan_pool=original.plan_pool)
                    try:
                        nominal = solver.frame(zero, targets=(ref,), diagnostics=False, _raw=True)
                        zones = [q for q in nominal['pieces'] if (q['layer'], q['index']) == ref and q.get('boxed')]
                        self.assertEqual(len(zones), len(boxes) + len(aligned))
                        for zone in zones:
                            self.assertFalse(zone['connection_failed'])
                            self.assertEqual(zone['moved_cut'].geom_type, 'LineString')
                            self.assertEqual(zone['extra_cut'].geom_type, 'LineString')
                        if ref == trunk_ref:
                            self.assertGreaterEqual(min(q['bridge'].bounds[0] for q in zones), -242711.)
                        actual = solver.frame(away, targets=(ref,), diagnostics=False, _raw=True)
                        replacement = next(q['geometry'] for q in actual['replacements'][ref[0]] if q['indices'] == (ref[1],))
                        self.assertTrue(replacement.is_valid)
                        self.assertEqual(replacement.geom_type, 'Polygon')
                        if ref == paired:
                            for v in solver.vias_by_target[ref]:
                                posed_via = model.moved(v['poly'], v['owner'], away)
                                self.assertTrue(replacement.buffer(data.dbu_um).covers(posed_via))
                            self.assertFalse(replacement.equals(cache.geometries[ref[0]][ref[1]]))
                            if aligned:
                                middle, = [b for b in nominal['between_blocks'] if b['ref'] == ref]
                                posed, = [b for b in actual['between_blocks'] if b['ref'] == ref]
                                self.assertEqual(middle['id'], posed['id'])
                                self.assertGreater(middle['geometry'].centroid.distance(posed['geometry'].centroid), 1.)
                    finally:
                        solver.close()
            from die_shift_studio.reconstruction_edits import change_zone
            for ref, point in ((trunk_ref, (-242540, 245161)), (branch_ref, (-242885, 246197))):
                base = original.frame(zero, targets=(ref,), diagnostics=False, _raw=True)
                messages = []
                window = SimpleNamespace(
                    current_frame=dict(states=zero, rotation_centers=model.bound.centers, reconstruction=base),
                    view_settings=settings, statusBar=lambda: SimpleNamespace(showMessage=messages.append),
                    invalidate_reconstruction_edit=lambda: None, request_preview=lambda **_: None,
                )
                change_zone(window, 'add', ref=ref, point=point)
                self.assertFalse(messages, messages)
                self.assertNotEqual(window.view_settings, settings)
                solver = Reconstructor(model, params, window.view_settings, reference=original, plan_pool=original.plan_pool)
                try:
                    frame = solver.frame(zero, targets=(ref,), diagnostics=False, _raw=True)
                    zone = min(frame['pieces'], key=lambda row: row['bridge'].distance(shapely.Point(point)))
                    self.assertLess(zone['bridge'].distance(shapely.Point(point)), 2.)
                    self.assertLess(zone['bridge'].bounds[2]-zone['bridge'].bounds[0], 100.)
                    self.assertFalse(zone['connection_failed'])
                finally:
                    solver.close()
        finally:
            original.close()

    def test_dxf_between_sides_follow_configured_amplitudes(self):
        from die_shift_studio.dxf_settings import DxfSettings
        from die_shift_studio.region_export import write_regions
        from die_shift_studio.region_import import metadata_for, read, recover_metadata

        engine, settings, _ = two_ended()
        try:
            model = engine.reconstructor.model
            source = model.data
            order = model.connections.order
            exported = snapshot(source, model.cache)
            boxes = (('2/0', vertices(200, -40, 218, 40)),)
            aligned = (('2/0', vertices(286, -40, 307, 40)),)
            expected = [('max', (0, 20), 'A'), ('min', (0, 20), 'B'),
                        ('min', (10, 20), 'A'), ('average', (0, 20), 'A'),
                        ('average', (0, 0), 'B')]
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / 'regions.dxf'
                for mode, (dx1, dx2), side in expected:
                    with self.subTest(mode=mode, amplitudes=(dx1, dx2)):
                        p = replace(engine.p, per_die_enabled=True, die_bounds=((dx1, 0., 0.), (dx2, 0., 0.)))
                        cfg = replace(settings, reconstruction_between_motion=mode,
                                      reconstruction_boxes=boxes, reconstruction_angle_boxes=aligned,
                                      reconstruction_box_order=('aligned', 'free'), reconstruction_box_starts=(('2/0', 0, 0),),
                                      reconstruction_ignored=(('2/0', 0),))
                        r = Reconstructor(model, p, cfg)
                        try:
                            f = r.frame(np.zeros((2, 3)), _raw=True)
                        finally:
                            r.close()
                        block, = f['between_blocks']
                        opts = DxfSettings()
                        write_regions(exported, f, engine.roles, path, settings=opts, edit_metadata=metadata_for(
                            source, engine.roles, order, p, cfg, exported.layers, opts, {}))
                        contours, metadata = read(path, with_metadata=True)
                        probe = shapely.Point(block['nominal'].centroid.x, block['nominal'].bounds[3])
                        self.assertLess(min(line.distance(probe) for line in contours['2/0'][side]), 1e-6)
                        restored = recover_metadata(metadata, contours, source, engine.roles, order, p, settings=cfg)
                        self.assertEqual(ordered_boxes(restored), ordered_boxes(cfg))
                        self.assertEqual(restored.reconstruction_box_starts, cfg.reconstruction_box_starts)
        finally:
            engine.close()

    def test_qt_stages_both_edits_and_selection_follows_pose(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.colors import to_rgba
        from die_shift_studio.gui import MainWindow, W
        from die_shift_studio.reconstruction_edits import change_zone
        from qt_shutdown import close_window

        app = W.QApplication.instance() or W.QApplication([])
        engine, data, _ = fixture()
        params, roles = engine.p, engine.roles
        engine.close()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = gdstk.Library(unit=1e-6, precision=1e-9)
            cell = lib.new_cell('TOP')
            for key, rows in data.layers.items():
                layer, datatype = map(int, key.split('/'))
                cell.add(*(gdstk.Polygon(xy, layer=layer, datatype=datatype) for xy in rows))
            layout = root / 'case.gds'
            lib.write_gds(layout)
            job = root / 'case.dshift.json'
            job.write_text(json.dumps(dict(
                schema_version=5, gds_path=str(layout), top_cell='TOP', roles=roles,
                layer_order=['6/0', '5/0', '4/0', '3/0', '128/0', '127/0'], parameters=asdict(params),
                settings={'animation_enabled': False, 'reconstruction_auto': False, 'reconstruction_box_mode': 'aligned'},
                overlays={'reconstruction': True},
            )), encoding='utf-8')
            window = MainWindow(jobs_dir=root / 'Jobs')
            window.show()
            errors = []
            window.error = lambda message: errors.append(str(message))

            def wait():
                limit = time.monotonic() + 120
                while time.monotonic() < limit:
                    app.processEvents()
                    if not (window._tasks or window.preview_pending or window._coverage_pending
                            or window.coverage_timer.isActive() or window.reconstruction_overlay._stage_worker):
                        self.assertFalse(errors, errors)
                        return
                    time.sleep(.01)
                self.fail('GUI work did not finish')

            def box_zone(start, end, mode='aligned'):
                window.view_settings = replace(window.view_settings, reconstruction_box_mode=mode)
                selector = window.region_select
                selector.state = dict(anchor=np.array(start), current=np.array(start), angle=0.)
                selector._finish(np.array(end))

            def click(x, y):
                px, py = window.ax.transData.transform((x, y))
                for event in ('button_press_event', 'button_release_event'):
                    MouseEvent(event, window.canvas, px, py, button=1)._process()

            try:
                window.load_project(job)
                wait()
                overlay = window.reconstruction_overlay
                change_zone(window, 'delete_all', refs={('5/0', 0)})
                box_zone((85., -30.), (140., 30.))
                box_zone((340., -30.), (410., 30.), mode='free')
                wait()
                self.assertTrue(window.reconstruction_pending)
                self.assertEqual(window.view_settings.reconstruction_box_order, ('aligned', 'free'))
                midpoint = next(iter(overlay.between_views.values()))['geometry'].representative_point()
                click(midpoint.x, midpoint.y)
                selected = window.selection.selected_between
                self.assertIsNotNone(selected)
                self.assertEqual(selected['mode'], 'max')
                window.change_between_motion(selected['id'], 'average')
                wait()
                self.assertEqual(len(overlay.staged_overlay['boxes']), 2)
                self.assertIn('待算：两侧平均', [label.get_text() for label in overlay.staged_tags if label.get_visible()])
                self.assertEqual(window.preview_settings().reconstruction_box_order, ())
                window.compute_reconstruction()
                wait()
                self.assertFalse(window.reconstruction_pending)
                self.assertIsNone(overlay.staged_overlay)
                settled = window.selection.selected_between
                self.assertEqual(settled['mode'], 'average')
                old = settled['geometry']
                window.manual_states = np.array([[0., 14., 0.]])
                window.request_preview(direct=True, pose_change=True)
                wait()
                posed = window.selection.selected_between
                self.assertEqual(posed['id'], selected['id'])
                self.assertAlmostEqual(posed['geometry'].centroid.y - old.centroid.y, 7., delta=.05)
                x, y = posed['geometry'].representative_point().coords[0]
                self.assertTrue(window.selection.deformed_artist.get_paths()[0].contains_point((x, y)))
                click(x, y+90.)
                self.assertIsNone(window.selection.selected_between)
                self.assertFalse(window.selection.deformed_artist.get_paths())
                rendered = np.asarray(window.canvas.buffer_rgba()).copy()
                window.render_scene()
                px, py = window.ax.transData.transform((x, y)).astype(int)
                row = len(rendered)-1-py
                patch = np.s_[row-2:row+3,px-2:px+3]
                np.testing.assert_allclose(rendered[patch], np.asarray(window.canvas.buffer_rgba())[patch], atol=2)
                colors = []
                for policy in ('min', 'max'):
                    window.change_between_motion(posed['id'], policy)
                    window.compute_reconstruction()
                    wait()
                    color, = overlay.artists['5/0', 'C'].get_facecolors()
                    style = window.style_for('5/0')
                    self.assertAlmostEqual(color[3], style['alpha'])
                    np.testing.assert_allclose(color[:3], between_color(to_rgba(style['color'])[:3], policy))
                    colors.append(color[:3])
                self.assertTrue(np.all(colors[0] < colors[1]))
                change_zone(window, 'delete_all', refs={('5/0', 0)})
                box_zone((247., -30.), (292., 30.))
                wait()
                self.assertEqual(window.view_settings.reconstruction_box_starts, (('5/0', 0, 2),))
                saved = window.view_settings
                window.edit_history.step()
                self.assertIn(('5/0', 0), window.view_settings.reconstruction_box_excluded)
                window.edit_history.step(forward=True)
                self.assertEqual(window.view_settings.reconstruction_key(), saved.reconstruction_key())
                wait()
            finally:
                close_window(app, window)


if __name__ == '__main__':
    unittest.main()
