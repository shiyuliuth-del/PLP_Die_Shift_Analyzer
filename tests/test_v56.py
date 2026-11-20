"""Independent regression tests for V56 GD55_1119 fixes, box-select marker, staged feedback, and popup culling."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

import numpy as np
import shapely
from matplotlib.backend_bases import MouseEvent

from die_shift_studio.area_filter import apply as apply_area_filter
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.project import normalize_project
from die_shift_studio.qt_compat import QtCore, QtWidgets as W
from die_shift_studio.reconstruction_edits import change_zone
from die_shift_studio.reconstruction_route import RoutePlan
from die_shift_studio.settings import ViewSettings
from RealDataAnalyse.projects import GD55_1119_INPUTS, install_case

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
    engine.configure_reconstruction(True, cfg)
    return engine, data, case, params, cfg


class V56Tests(unittest.TestCase):
    def test_gd55_1119_case_inputs_and_install(self):
        for name in GD55_1119_INPUTS:
            self.assertTrue((GD55_1119_DIR / name).is_file(), name)
        with TemporaryDirectory() as tmp:
            install_case(tmp)
            for name in GD55_1119_INPUTS:
                self.assertTrue((Path(tmp) / 'Jobs' / 'GD55_1119' / name).is_file(), name)

    def test_fast_section_matches_exact_shapely_intersection(self):
        # Non-convex L-shaped polygon with a hole; verify vectorized section matches shapely exactly.
        outer = shapely.Polygon([(0, 0), (200, 0), (200, 40), (60, 40), (60, 180), (0, 180)])
        hole = shapely.Polygon([(15, 15), (35, 15), (35, 30), (15, 30)])
        source = outer.difference(hole)
        route = shapely.LineString([(10, 10), (190, 20)])
        land = shapely.Point(10, 10).buffer(8).intersection(source)
        anchor = shapely.Point(190, 20).buffer(8).intersection(source)
        plan = RoutePlan('5/0', 0, 0, (('4/0', 0),), source, route, land, (anchor,), 0.001, 3.0)
        size = np.hypot(source.bounds[2] - source.bounds[0], source.bounds[3] - source.bounds[1]) + 1
        for s in np.linspace(15, 175, 17):
            for angle in (0.0, 0.25, -0.35):
                fast = plan.section(float(s), angle)
                point = np.asarray(route.interpolate(float(s)).coords[0])
                normal = plan.tangent(float(s))
                u = np.array([-normal[1], normal[0]])
                co, si = np.cos(angle), np.sin(angle)
                u = np.array([co * u[0] - si * u[1], si * u[0] + co * u[1]])
                chord = shapely.intersection(source, shapely.LineString([point - u * size, point + u * size]))
                lines = [q for q in shapely.get_parts(chord) if q.geom_type == 'LineString' and q.length > plan.tolerance]
                exact = min(lines, key=lambda g: float(g.distance(shapely.Point(point)))) if lines else None
                self.assertIsNotNone(fast)
                self.assertIsNotNone(exact)
                self.assertLess(fast.hausdorff_distance(exact), 1e-6)

    def test_gd55_1119_routes_and_two_die_drag_boundaries(self):
        engine, _, case, _, cfg = _load_gd55_1119()
        try:
            zero = np.zeros((len(case['dies']), 3))
            frame = engine.frame(zero)
            pieces = frame['reconstruction']['pieces']
            # Every regular (non-plane-branch) routed trace must reach its anchor (no truncated corner stubs).
            for q in pieces:
                plan = q.get('route_plan')
                if plan is None or q.get('graph_branch') or getattr(plan, 'plane_domain', False):
                    continue
                end_pt = shapely.Point(plan.route.coords[-1])
                anchor_dist = min((a.distance(end_pt) for a in plan.anchors), default=0.0)
                self.assertLessEqual(anchor_dist, 5.0, f'Stub route on {(q["layer"], q["index"], q["die"])}')

            # (1211, -1769) -> 5/0 #4 die=2 must span the long straight segment and drag through the corner.
            p4_d2 = next(q for q in pieces if (q['layer'], q['index'], q['die']) == ('5/0', 4, 2))
            self.assertLess(p4_d2['moved_cut'].centroid.x, 1200.0)
            plan4 = p4_d2['route_plan']
            self.assertGreater(plan4.length, 1000.0)
            # Drag moved_cut right across the corner (1211, -1769) toward x=1250.
            target_s = float(plan4.route.project(shapely.Point(1250.0, -1760.4)))
            part, _ = plan4.edit_pose(*p4_d2['interval'], 0, target_s, frame['rotation_centers'][2], zero[2], cfg.reconstruction_style)
            self.assertIsNotNone(part)
            self.assertGreater(part['near'].centroid.x, 1240.0)

            # (670, -1854) -> 5/0 #3: both die=0 and die=2 routes allow dragging across the 50% midpoint 672.64.
            p3_d0 = next(q for q in pieces if (q['layer'], q['index'], q['die']) == ('5/0', 3, 0))
            p3_d2 = next(q for q in pieces if (q['layer'], q['index'], q['die']) == ('5/0', 3, 2))
            s_d0_right = float(p3_d0['route_plan'].route.project(shapely.Point(780.0, -1864.3)))
            part_d0, _ = p3_d0['route_plan'].edit_pose(*p3_d0['interval'], 1, s_d0_right, frame['rotation_centers'][0], zero[0], cfg.reconstruction_style)
            self.assertIsNotNone(part_d0)
            self.assertGreater(part_d0['far'].centroid.x, 750.0)
            s_d2_right = float(p3_d2['route_plan'].route.project(shapely.Point(850.0, -1864.3)))
            part_d2, _ = p3_d2['route_plan'].edit_pose(*p3_d2['interval'], 1, s_d2_right, frame['rotation_centers'][2], zero[2], cfg.reconstruction_style)
            self.assertIsNotNone(part_d2)
            self.assertGreater(part_d2['far'].centroid.x, 840.0)
        finally:
            engine.close()

    def test_gd55_1119_gui_drag_delete_box_marker_staged_and_callout_culling(self):
        from die_shift_studio.gui import MainWindow

        app = W.QApplication.instance() or W.QApplication([])
        w = MainWindow()
        w.resize(1200, 800)

        def drain(timeout_ms=60000):
            timer = QtCore.QElapsedTimer()
            timer.start()
            while timer.elapsed() < timeout_ms:
                app.processEvents()
                if (w.worker is None and not w.preview_workers and w.cut_drag.worker is None
                        and w.motion_worker is None and w.preview_pending is None):
                    for _ in range(5):
                        app.processEvents()
                    if w.worker is None and not w.preview_workers and w.cut_drag.worker is None:
                        return
                QtCore.QThread.msleep(20)

        def send(name, x, y, button=1):
            px, py = w.ax.transData.transform((x, y))
            ev = MouseEvent(name, w.canvas, int(round(px)), int(round(py)), button=button)
            w.canvas.callbacks.process(name, ev)
            app.processEvents()
            return ev

        try:
            w.load_project(str(GD55_1119_PROJECT.resolve()))
            drain()
            w.run_evaluation()
            drain()
            if not (w.current_frame or {}).get('reconstruction'):
                w.compute_reconstruction()
                drain()
            self.assertFalse(w.view_settings.reconstruction_auto)

            # 1) (670, -1854): drag die=2 boundary right, then drag die=0 boundary right across 672.64.
            w.set_view((700, -1854), 500, 300)
            w.viewport_changed(force=True)
            app.processEvents()
            w.canvas.draw()
            send('button_press_event', 672.7, -1854.0, 1)
            self.assertIsNotNone(w.cut_drag.state)
            for tx in (720.0, 800.0, 860.0):
                send('motion_notify_event', tx, -1854.0, 1)
                QtCore.QThread.msleep(35)
                app.processEvents()
            send('button_release_event', 860.0, -1854.0, 1)
            drain()
            p3_d2 = next(q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 3, 2))
            self.assertGreater(p3_d2['extra_cut'].centroid.x, 840.0)

            send('button_press_event', 647.8, -1854.0, 1)
            self.assertIsNotNone(w.cut_drag.state)
            for tx in (690.0, 740.0, 780.0):
                send('motion_notify_event', tx, -1854.0, 1)
                QtCore.QThread.msleep(35)
                app.processEvents()
            send('button_release_event', 780.0, -1854.0, 1)
            drain()
            p3_d0 = next(q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 3, 0))
            self.assertGreater(p3_d0['extra_cut'].centroid.x, 760.0)

            # 2) (1211, -1769): delete zone -> box-select on left -> delete box-selected zone (all with auto=False).
            p4_d2 = next(q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 4, 2))
            change_zone(w, 'delete', piece=p4_d2)
            drain()
            self.assertFalse([q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 4, 2)])
            self.assertTrue(w.reconstruction_overlay.staged.get_visible())

            # 3) Box-select mode landing point marker before first click, during draw, and after exit.
            w.set_view((800, -1785), 600, 300)
            w.viewport_changed(force=True)
            app.processEvents()
            w.canvas.draw()
            w.region_mode('move', True)
            send('motion_notify_event', 650.0, -1815.0, 0)
            self.assertIsNotNone(w.region_select.marker)
            self.assertTrue(w.region_select.marker.get_visible())
            send('button_press_event', 650.0, -1815.0, 1)
            self.assertTrue(w.region_select.anchor_marker.get_visible())
            send('motion_notify_event', 950.0, -1755.0, 0)
            send('button_press_event', 950.0, -1755.0, 1)
            drain()
            boxed = [q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 4, 2)]
            self.assertEqual(len(boxed), 1)
            self.assertGreater(boxed[0]['regions']['A'].area, 5000.0)
            self.assertGreater(boxed[0]['interval'][1] - boxed[0]['interval'][0], 200.0)
            self.assertTrue(w.reconstruction_overlay.staged.get_visible())

            # Deleting the box-selected zone must actually remove it.
            change_zone(w, 'delete', piece=boxed[0])
            drain()
            self.assertFalse([q for q in w.reconstruction_overlay.data()['pieces'] if (q['layer'], q['index'], q['die']) == ('5/0', 4, 2)])
            w.exit_region_mode()
            self.assertIsNone(w.region_select.marker)

            # 4) Right-click settings with auto=False must mark shapes and fixed vias visibly.
            w.change_reconstruction_ignores([('5/0', 6)], True)
            self.assertTrue(w.reconstruction_overlay.staged.get_visible())
            self.assertIn(('5/0', 6), w.reconstruction_overlay.staged_overlay['refs'])
            w.view_settings = replace(w.view_settings, reconstruction_fixed_via_mode='individual')
            w.change_fixed_via_following([('6/0', 0)], True)
            self.assertIn(('6/0', 0), w.reconstruction_overlay.staged_overlay['refs'])

            # 5) Multi-zone shape click hides off-screen popups and responds quickly.
            w.set_view((1400, -1770), 300, 200)
            w.viewport_changed(force=True)
            app.processEvents()
            w.canvas.draw()
            t0 = time.perf_counter()
            w.selection.apply_hits([('5/0', 24)], 'replace')
            elapsed = time.perf_counter() - t0
            visible = [a for a in w.reconstruction_overlay.labels if a.get_visible()]
            self.assertLessEqual(len(visible), 2)
            self.assertLess(elapsed, 0.5)
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
