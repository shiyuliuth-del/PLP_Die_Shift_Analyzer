"""Physical branch isolation, SH22 boxed cuts, CAD contours and short-strip picking."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

import gdstk
import numpy as np
import shapely

from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor, compose_feature
from die_shift_studio.reconstruction_events import _compact
from die_shift_studio.settings import ViewSettings
from die_shift_studio.topology_checks import BRANCH_CONTACT, preserves_branch_contacts
from test_v38 import fixture
from test_v72 import vertices

ROOT = Path(__file__).resolve().parents[1]


class V73Tests(unittest.TestCase):
    def test_box_on_two_visits_of_one_route_yields_separate_spans(self):
        from die_shift_studio.manual_regions import route_box_spans
        from types import SimpleNamespace

        route = shapely.LineString([(0, 0), (100, 0), (100, 20), (0, 20)])
        spans = route_box_spans(SimpleNamespace(route=route, tolerance=.001), shapely.box(40, -5, 60, 25))
        self.assertEqual(spans, [(40., 60.), (160., 180.)])

    def test_new_same_net_contact_is_hard_and_local(self):
        box = shapely.box
        source = shapely.union_all([box(0, 0, 80, 5), box(0, 20, 80, 25), box(0, 0, 5, 25)])
        a, b, c = box(20, 0, 35, 5), box(20, 20, 35, 25), box(55, 20, 65, 25)
        rigid = source.difference(shapely.union_all([a, b, c]))
        empty = shapely.Polygon()
        shifted_b = box(20, 5, 35, 10)  # Edge contact alone makes an unintended cross-branch join.
        rows = [dict(
            die=0, graph_members=(name,), nominal_regions=dict(A=nominal, B=rigid, C=empty),
            regions=dict(A=posed, B=rigid, C=empty), connection_failed=False,
        ) for name, nominal, posed in (('lower', a, a), ('upper', b, shifted_b), ('remote', c, c))]
        rows[0]['branch_rigid'] = tuple((g, g) for g in shapely.get_parts(rigid))
        geometry, _ = compose_feature(source, rows, .001, True)
        for q in rows[:2]:
            self.assertEqual(q['warning'], BRANCH_CONTACT)
            self.assertFalse(q['branch_conflict'].is_empty)
            self.assertTrue(q['regions']['A'].is_empty)
            self.assertIn('branch_conflict', _compact(q))
        self.assertFalse(rows[2]['connection_failed'])
        self.assertTrue(geometry.covers(c))
        self.assertFalse(geometry.covers(shifted_b.representative_point()))
        # Post-reconstruction repairs have the same protection against a
        # shortcut around the existing common trunk.
        with_shortcut = source.union(box(39, 4, 41, 21))
        cells = [(0, box(30, 0, 50, 5), box(30, 0, 50, 5)),
                 (1, box(30, 20, 50, 25), box(30, 20, 50, 25))]
        self.assertTrue(preserves_branch_contacts(source, source, cells, .001))
        self.assertFalse(preserves_branch_contacts(with_shortcut, source, cells, .001))

    def test_failed_default_partition_does_not_abort_a_valid_manual_box(self):
        from unittest.mock import patch
        from die_shift_studio.reconstruction_zones import expand

        engine, _, _ = fixture()
        try:
            original = engine.reconstructor
            original._plans_for_all_poses(None)
            entry = next(e for e in original._base_plans if e['plan'] is not None)
            box = (entry['layer'], vertices(280, -50, 360, 50))
            settings = replace(original.settings, reconstruction_angle_boxes=(box,))
            solver = Reconstructor(original.model, original.p, settings, reference=original)
            try:
                with patch.object(solver, '_interval_without_boxes', return_value=(None, False)):
                    rows = expand(solver, [entry])
                valid, = rows
                self.assertTrue(valid.get('boxed'))
                self.assertFalse(valid['merged_part']['A'].is_empty)
            finally:
                solver.close()
        finally:
            engine.close()

    def test_sh22_fork_and_ownership_boxes_and_dxf(self):
        from die_shift_studio.dxf_settings import DxfSettings
        from die_shift_studio.region_export import write_regions
        from die_shift_studio.region_import import read
        from die_shift_studio.topology_routing import resolve

        path = ROOT/'Jobs'/'SH22'/'SH22.dshift.json'
        project = normalize_project(json.loads(path.read_text(encoding='utf-8')))
        raw = read_gds(path.parent/project['gds_path'], project['top_cell'])
        data, project = restore_project_geometry(raw, project)
        params = Parameters.from_dict(project['parameters'])
        cache = GeometryCache(data)
        roles = project['roles']
        case = build_case(data, roles, layer_order=project['layer_order'])
        model = ContactModel(data, case, roles, case['connections'], cache, parameters=params)
        layer = '2/0'
        boxes = ((layer, vertices(-242623, 246145, -242531, 246189)),
                 (layer, vertices(-242004, 245389, -241924, 245469)))
        cfg = replace(ViewSettings.from_dict(project['settings']), reconstruction_boxes=boxes,
                      reconstruction_box_order=('free', 'free'))
        r = Reconstructor(model, params, cfg)
        angle = np.deg2rad(1.)
        state = np.array([[15., -15., angle], [-15., 15., -angle]])
        zero = np.zeros_like(state)
        srcs = cache.geometries[layer]

        def check_point(frame, xy, minimum_zones):
            ref = (layer, min(range(len(srcs)), key=lambda i: srcs[i].distance(shapely.Point(xy))))
            rows = [q for q in frame['pieces'] if (q['layer'], q['index']) == ref and q.get('boxed')]
            self.assertGreaterEqual(len(rows), minimum_zones)
            self.assertTrue(any(q['nominal_regions']['A'].covers(shapely.Point(xy)) for q in rows))
            for q in rows:
                self.assertFalse(q.get('box_invalid'), q['warning'])
                self.assertEqual(q['moved_cut'].geom_type, 'LineString')
                self.assertEqual(q['extra_cut'].geom_type, 'LineString')
            return ref, rows

        try:
            nominal = r.frame(zero, diagnostics=False, _raw=True)
            seam, _ = check_point(nominal, (-242577, 246167), 2)
            fork, rows = check_point(nominal, (-241964, 245429), 2)
            for i, row in enumerate(rows):
                a = row['nominal_regions']['A']
                for other in rows[i+1:]:
                    self.assertLess(a.intersection(other['nominal_regions']['A']).area, 1e-6)
            shifted = r.frame(state, diagnostics=False, _raw=True)
            conflicts = [q for q in shifted['pieces'] if q.get('branch_conflict') is not None]
            self.assertTrue(any((q['layer'], q['index']) == fork for q in conflicts))
            for q in conflicts:
                self.assertTrue(q['regions']['A'].is_empty, q['warning'])
            # The catalogue stores compact rows. A second pass must retain the
            # same failure and prohibit repair from joining the two branches.
            _, _, _, blocked = resolve(r, shifted['replacements'], [_compact(q) for q in shifted['pieces']], state)
            self.assertIn((fork, BRANCH_CONTACT), blocked)

            nominal_rows = [dict(q, regions=q['nominal_regions']) for q in nominal['pieces']
                            if (q['layer'], q['index']) in (seam, fork)]
            export_frame = dict(pieces=nominal_rows, between_blocks=[
                b for b in nominal['between_blocks'] if b['ref'] in (seam, fork)
            ])
            self.assertTrue(export_frame['between_blocks'])
            for block in export_frame['between_blocks']:
                for row in nominal_rows:
                    if (row['layer'], row['index']) == block['ref']:
                        self.assertLess(block['nominal'].intersection(row['regions']['A']).area, .001)
            layout = snapshot(data, cache)
            layout = replace(layout, layers={layer: tuple(srcs[i].wkb for _, i in (seam, fork))})
            with TemporaryDirectory() as tmp:
                target = Path(tmp)/'sh22-regions.dxf'
                result = write_regions(layout, export_frame, roles, target, settings=DxfSettings())
                self.assertTrue(result['topology']['L2_D0']['closed'])
                contours = read(target)[layer]
                union = shapely.union_all([line for lines in contours.values() for line in lines])
                expected = shapely.union_all([srcs[i].boundary for _, i in (seam, fork)])
                self.assertLess(union.hausdorff_distance(expected), data.dbu_um*.01)
                self.assertAlmostEqual(sum(g.length for lines in contours.values() for g in lines),
                                       expected.length, places=5)
                # An unusable diagnostic rectangle creates neither a deformation
                # layer nor a separator across the inside of the source.
                invalid = dict(regions=dict(A=srcs[fork[1]], B=shapely.Polygon(), C=srcs[fork[1]]),
                               layer=layer, box_invalid=True, valid=False)
                write_regions(layout, dict(pieces=[invalid]), roles, target, settings=DxfSettings())
                self.assertEqual(set(read(target)[layer]), {'B'})
        finally:
            r.close()

    def test_qt_short_between_strip_wins_a_stationary_click(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from matplotlib.backend_bases import MouseEvent
        from die_shift_studio.gui import MainWindow, W
        from die_shift_studio.reconstruction_edits import key
        from die_shift_studio.shape_menu import show_shape_menu
        from unittest.mock import patch
        from qt_shutdown import close_window

        app = W.QApplication.instance() or W.QApplication([])
        engine, data, _ = fixture()
        roles, params = engine.roles, engine.p
        engine.close()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = gdstk.Library(unit=1e-6, precision=1e-9)
            cell = lib.new_cell('TOP')
            for layer, rows in data.layers.items():
                number, datatype = map(int, layer.split('/'))
                cell.add(*(gdstk.Polygon(xy, layer=number, datatype=datatype) for xy in rows))
            source = root/'case.gds'
            lib.write_gds(source)
            job = root/'case.dshift.json'
            job.write_text(json.dumps(dict(
                schema_version=5, gds_path=str(source), top_cell='TOP', roles=roles,
                layer_order=['6/0', '5/0', '4/0', '3/0', '128/0', '127/0'], parameters=asdict(params),
                settings={'animation_enabled': False}, overlays={'reconstruction': True},
            )), encoding='utf-8')
            w = MainWindow(jobs_dir=root/'Jobs')
            w.reconstruction_yield.refresh=lambda *_:None
            w.show()
            errors = []
            w.error = lambda m: errors.append(str(m))

            def wait():
                timeout = time.monotonic()+120
                while time.monotonic()<timeout:
                    app.processEvents()
                    if not (w._tasks or w.preview_pending or w._coverage_pending or w.coverage_timer.isActive()):
                        self.assertFalse(errors, errors)
                        return
                    time.sleep(.01)
                self.fail('GUI work did not finish')

            try:
                w.load_project(job)
                wait()
                modes=w.setting_fields['reconstruction_between_motion']
                self.assertEqual(modes.itemText(0),'静止不动')
                self.assertEqual(modes.currentData(),'max')
                fixed_via=w.setting_fields['reconstruction_fixed_via_mode']
                self.assertEqual([fixed_via.itemText(i) for i in range(fixed_via.count())],
                                 ['关闭','全局随动','全局静止','分别处理'])
                self.assertIn('固定过孔随动',[label.text() for label in fixed_via.parentWidget().findChildren(W.QLabel)])
                trigger = w.setting_fields['reconstruction_pad_trigger_um']
                accuracy = w.setting_fields['reconstruction_grid_nodes']
                self.assertEqual(trigger.accessibleName(), '孔盘整体触发')
                self.assertEqual(accuracy.accessibleName(), '积分精度')
                self.assertEqual(trigger.geometry().center().y(), accuracy.geometry().center().y())
                plan = next(q['route_plan'] for q in w.current_frame['reconstruction']['pieces'] if q['route_plan'])
                w.view_settings = replace(w.view_settings, reconstruction_intervals=((*key(plan), 80., 180.),),
                                          reconstruction_angle_boxes=(('5/0', vertices(192, -40, 260, 40)),),
                                          reconstruction_forced=(('5/0', 0),))
                w.invalidate_reconstruction_edit()
                w.request_preview()
                wait()
                block, = w.reconstruction_overlay.between_views.values()
                self.assertLess(block['geometry'].bounds[2]-block['geometry'].bounds[0], 16.)
                point = block['geometry'].representative_point()
                w.set_view((point.x, point.y), 1000., 500.)
                w.plot_layout(preserve=True)
                w.canvas.draw()
                x, y = w.ax.transData.transform((point.x, point.y))
                press = MouseEvent('button_press_event', w.canvas, x, y, button=1)
                press._process()
                self.assertIsNotNone(w.cut_drag.state)  # The handle halo overlaps the strip.
                MouseEvent('button_release_event', w.canvas, x, y, button=1)._process()
                self.assertEqual(w.selection.selected_between['id'], block['id'])
                self.assertIsNone(w.cut_drag.state)
                # Right click on the strip without prior selection while the
                # near/far handle halos still cover the pointer.
                w.selection.clear()
                menu = []
                right = MouseEvent('button_press_event', w.canvas, x, y, button=3)
                with patch.object(W.QMenu, 'popup', lambda widget, *_: menu.append(widget)):
                    show_shape_menu(w, right, (point.x, point.y))
                summary,stationary,*_=menu[0].actions()
                self.assertFalse(summary.isEnabled())
                self.assertTrue(stationary.isEnabled())
                self.assertEqual(stationary.text(),'动区间随动：静止不动')
                menu[0].resize(menu[0].sizeHint())
                screen=menu[0].grab()
                image=screen.toImage();ratio=screen.devicePixelRatio()
                def row_color(action):
                    rect=menu[0].actionGeometry(action)
                    return image.pixelColor(round((rect.right()-5)*ratio),round(rect.center().y()*ratio)).name()
                self.assertEqual(row_color(summary),'#e8ebef')
                self.assertNotEqual(row_color(stationary),row_color(summary))
                stationary.trigger()
                wait()
                self.assertEqual(w.selection.selected_between['mode'],'static')
                self.assertEqual(w.view_settings.reconstruction_between_overrides,((*block['id'],'static'),))
                layer=block['ref'][0]
                w.set_view((point.x,point.y),60.,45.)
                w.plot_layout(preserve=True)
                w.canvas.draw()
                dots=w.reconstruction_overlay.stationary_artists[layer]
                self.assertTrue(dots.get_visible())
                from matplotlib.colors import to_rgba
                from die_shift_studio.reconstruction_overlay import between_color
                style=w.style_for(layer)
                rgb=to_rgba(style['color'])[:3]
                darker=np.array(between_color(rgb,'static'))
                self.assertTrue(np.all(darker<=between_color(rgb,'min')))
                self.assertTrue(np.any(darker<between_color(rgb,'min')))
                colors=w.reconstruction_overlay.artists[layer,'C'].get_facecolors()
                self.assertTrue(np.any(np.all(np.isclose(colors,(*darker,style['alpha'])),axis=1)))
                patterned=np.asarray(w.canvas.buffer_rgba()).copy()
                dots.set_visible(False)
                w.canvas.draw()
                yy,xx=np.where(np.any(patterned!=np.asarray(w.canvas.buffer_rgba()),axis=2))
                self.assertGreater(len(xx),0)
                pixels=w.ax.transData.inverted().transform(np.column_stack((xx+.5,patterned.shape[0]-yy-.5)))
                pixel_um=abs(np.diff(w.ax.get_xlim())[0])/w.ax.bbox.width
                fixed=w.reconstruction_overlay.between_views[block['id']]['geometry']
                self.assertTrue(np.all(shapely.contains_xy(fixed.buffer(2*pixel_um),pixels[:,0],pixels[:,1])))
                dots.set_visible(True)
                all_actions=[sub for a in menu[0].actions() for sub in (a.menu().actions() if a.menu() is not None else (a,))]
                action, = [a for a in all_actions if a.text().endswith('两侧平均')]
                action.trigger()
                wait()
                self.assertEqual(w.selection.selected_between['mode'], 'average')
                self.assertFalse(dots.get_visible())
                self.assertEqual(w.view_settings.reconstruction_between_overrides, ((*block['id'], 'average'),))

                # A worker exception must not keep the new override or its undo
                # entry, and the popup must not expose the full traceback.
                committed = w.current_frame
                previous = w.view_settings.reconstruction_between_overrides
                undo = tuple(tuple(s) for s in w.edit_history.stacks)
                from die_shift_studio.fast_preview import PreviewEngine
                with patch.object(PreviewEngine, 'frame', side_effect=RuntimeError('injected reconstruction failure')):
                    w.change_between_motion(block['id'], 'min')
                    end = time.monotonic()+30
                    while not errors and time.monotonic()<end:
                        app.processEvents()
                        time.sleep(.01)
                self.assertEqual(w.view_settings.reconstruction_between_overrides, previous)
                self.assertIs(w.current_frame, committed)
                self.assertEqual(tuple(tuple(s) for s in w.edit_history.stacks), undo)
                self.assertEqual(errors, ['修改动区间随动未完成，已恢复操作前状态。详情见运行信息。'])
                log = w.log_bus.path.read_text(encoding='utf-8')
                self.assertIn('RuntimeError: injected reconstruction failure', log)
                menu[0].deleteLater()
            finally:
                close_window(app, w)


if __name__ == '__main__':
    unittest.main()
