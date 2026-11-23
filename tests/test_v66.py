"""V66 regressions: ABC block layers & auto-JOIN, measured perf & zero-die fast path,
process expansion & 100um APD default, deferred delete/undo-delete, export/project
edit persistence, large-layout/multi-zone perf, and SH22 c1/joint crash fixes."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import ezdxf
import gdstk
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from die_shift_studio.area_filter import apply as area_apply
from die_shift_studio.contact import ContactModel
from die_shift_studio.core import GDSData, Parameters, build_case, read_gds
from die_shift_studio.dxf_export import snapshot, write as write_dxf
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import merge_import, restore_project_geometry
from die_shift_studio.gds_shapes import node_fingerprint, shape_nodes
from die_shift_studio.layout_export import OutputOptions, write as write_layout
from die_shift_studio.measured_core import Options as MeasuredOptions, Session
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_angles import measure, solve
from die_shift_studio.reconstruction_joint import prepare as joint_prepare
from die_shift_studio.reconstruction_zones import live_serial
from die_shift_studio.region_export import write_regions
from die_shift_studio.resources import serial_scope
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from test_v38 import expanded_outlines, fixture

ROOT = Path(__file__).resolve().parents[1]


class V66Tests(unittest.TestCase):
    def test_abc_block_entity_layers_and_auto_join_option(self):
        engine, data, _ = fixture()
        try:
            roles = engine.roles
            states = np.zeros((1, 3))
            frame = engine.frame(states)
            split = dict(frame['reconstruction'], pieces=[
                dict(q, regions=q['nominal_regions']) for q in frame['reconstruction']['pieces']
            ])
            nom = snapshot(data, engine.contact.cache, dict(states=states), reconstruction=False)
            self.assertTrue(DxfSettings().region_auto_join)
            self.assertFalse(DxfSettings.from_dict({'region_auto_join': False}).region_auto_join)
            self.assertTrue(DxfSettings.from_dict({'region_auto_join': True}).region_auto_join)
            with self.assertRaises(ValueError):
                DxfSettings.from_dict({'region_auto_join': 'yes'})

            with tempfile.TemporaryDirectory() as tmp:
                open_path = Path(tmp) / 'open.dxf'
                join_path = Path(tmp) / 'join.dxf'
                write_regions(nom, split, roles, open_path, settings=DxfSettings(region_auto_join=False))
                write_regions(nom, split, roles, join_path, settings=DxfSettings(region_auto_join=True))

                open_doc = ezdxf.readfile(open_path)
                join_doc = ezdxf.readfile(join_path)

                open_blocks = [e for e in open_doc.modelspace() if e.dxftype() == 'INSERT']
                join_blocks = [e for e in join_doc.modelspace() if e.dxftype() == 'INSERT']
                self.assertEqual(len(open_blocks), len(join_blocks))
                self.assertGreater(len(open_blocks), 0)

                # Req 1: every child entity inside every block definition has its block's ABC layer
                for doc in (open_doc, join_doc):
                    for insert in (e for e in doc.modelspace() if e.dxftype() == 'INSERT'):
                        blk = doc.blocks[insert.dxf.name]
                        children = list(blk)
                        self.assertGreater(len(children), 0)
                        for child in children:
                            self.assertEqual(child.dxftype(), 'LWPOLYLINE')
                            self.assertEqual(child.dxf.layer, insert.dxf.layer)
                            self.assertNotEqual(child.dxf.layer, '0')

                # Req 4: auto-JOIN merges connected segments in blocks (fewer or closed polylines, identical geometry)
                open_count = sum(len(list(open_doc.blocks[e.dxf.name])) for e in open_blocks)
                join_count = sum(len(list(join_doc.blocks[e.dxf.name])) for e in join_blocks)
                self.assertLessEqual(join_count, open_count)

                # Pure static polygon (single-kind ring) joins into 1 closed LWPOLYLINE when auto-JOIN is enabled
                data2 = replace(data, layers={**data.layers, '5/0': [*data.layers['5/0'], np.array([[200., 100.], [320., 100.], [320., 150.], [200., 150.]])]})
                cache2 = GeometryCache(data2)
                nom2 = snapshot(data2, cache2, dict(states=states), reconstruction=False)
                open_path2 = Path(tmp) / 'open2.dxf'
                join_path2 = Path(tmp) / 'join2.dxf'
                write_regions(nom2, split, roles, open_path2, settings=DxfSettings(region_auto_join=False))
                write_regions(nom2, split, roles, join_path2, settings=DxfSettings(region_auto_join=True))
                d_open2 = ezdxf.readfile(open_path2)
                d_join2 = ezdxf.readfile(join_path2)
                c_open2 = sum(len(list(d_open2.blocks[e.dxf.name])) for e in d_open2.modelspace() if e.dxftype() == 'INSERT')
                c_join2 = sum(len(list(d_join2.blocks[e.dxf.name])) for e in d_join2.modelspace() if e.dxftype() == 'INSERT')
                self.assertLess(c_join2, c_open2)
                joined_closed = [
                    child.closed
                    for e in d_join2.modelspace() if e.dxftype() == 'INSERT'
                    for child in d_join2.blocks[e.dxf.name]
                ]
                self.assertTrue(any(joined_closed))
        finally:
            engine.close()

    def test_defaults_process_sections_and_apd_interval(self):
        # Req 7: default space export interval is 100 um
        self.assertEqual(ViewSettings().space_export_interval_um, 100.)
        # Req 5: default expanded_process includes all 5 process sections
        norm = normalize_project({'schema_version': 5, 'roles': {}, 'parameters': {}})
        self.assertEqual(set(norm['expanded_process']), {'线宽线距', '过孔包围量', '图形过滤', '芯片距离', '其他'})
        # Explicit saved expanded_process is preserved
        norm_explicit = normalize_project({'schema_version': 5, 'roles': {}, 'parameters': {}, 'expanded_process': ['线宽线距']})
        self.assertEqual(norm_explicit['expanded_process'], ['线宽线距'])

    def test_measured_model_reuse_and_zero_die_exact_equivalence(self):
        pp = ROOT / 'Jobs' / 'SH22' / 'SH22.dshift.json'
        proj = normalize_project(json.loads(pp.read_text(encoding='utf-8')))
        raw = read_gds(str(pp.parent / proj['gds_path']), proj['top_cell'])
        data, proj = restore_project_geometry(raw, proj)
        p = Parameters(**proj['parameters'])
        cfg = ViewSettings.from_dict(proj['settings'])
        cache = GeometryCache(data)
        case = build_case(data, proj['roles'], layer_order=proj['layer_order'])
        sess = Session(data, case, proj['roles'], proj['motions'], p, cfg, cache)
        try:
            sess.prepare()
            centers = [np.asarray(d.centroid.coords[0]) for d in case['dies']]
            # Req 2: varying rotation_center_um reuses ContactModel & Reconstructor (no thrashing)
            _, p_a = __import__('die_shift_studio.measured_core', fromlist=['pose_parameters']).pose_parameters(
                case, sess.p, [dict(die=i + 1, dx_um=1., dy_um=0., angle_deg=0., rotation_center_um=centers[i].tolist()) for i in range(len(centers))]
            )
            _, p_b = __import__('die_shift_studio.measured_core', fromlist=['pose_parameters']).pose_parameters(
                case, sess.p, [dict(die=i + 1, dx_um=1., dy_um=0., angle_deg=0., rotation_center_um=(centers[i] + np.array([2., -3.])).tolist()) for i in range(len(centers))]
            )
            m1, r1 = sess.model(p_a)
            m2, r2 = sess.model(p_b)
            self.assertIs(m1, m2)
            self.assertIs(r1, r2)
            self.assertTrue(np.allclose(m2.bound.centers[0], centers[0] + np.array([2., -3.])))

            # Req 2: measured memory budget expands auto ceiling while honoring explicit user cap
            fake_profile = dict(available_memory_mb=10000, memory_budget_mb=3500)
            self.assertGreater(MeasuredOptions().memory_budget_mb(fake_profile, measured=True),
                               MeasuredOptions().memory_budget_mb(fake_profile, measured=False))
            self.assertEqual(MeasuredOptions(max_memory_mb=1024).memory_budget_mb(fake_profile, measured=True), 1024)

            # Req 3: zero-shift die fast path produces 100% identical geometry & DRC vs unskipped full run
            for rows in (
                [dict(die=1, dx_um=0., dy_um=0., angle_deg=0.), dict(die=2, dx_um=0., dy_um=0., angle_deg=0.)],
                [dict(die=1, dx_um=14., dy_um=-9., angle_deg=0.3), dict(die=2, dx_um=0., dy_um=0., angle_deg=0.)],
            ):
                with serial_scope():
                    _, fast_snap, fast_det = sess.run(rows, preview=False, output_policy='omit_failed_regions')
                    # Compare against preview=True (which runs full unskipped r.frame + standard snapshot)
                    _, full_snap, full_det = sess.run(rows, preview=True, output_policy='omit_failed_regions')
                self.assertEqual(set(fast_snap.layers), set(full_snap.layers))
                for k in sorted(fast_snap.layers):
                    ga = shapely.union_all([shapely.from_wkb(w) for w in fast_snap.layers[k]])
                    gb = shapely.union_all([shapely.from_wkb(w) for w in full_snap.layers[k]])
                    self.assertLessEqual(ga.symmetric_difference(gb).area, 1e-12, f'Layer {k} XOR mismatch')
                self.assertEqual(fast_det['connection_failure_count'], full_det['connection_failure_count'])
                self.assertEqual(fast_det['drc_count'], full_det['drc_count'])
                self.assertEqual(fast_det['drc_passed'], full_det['drc_passed'])
        finally:
            sess.close()

    def test_sh22_corner_c1_geometry_collection_and_joint_fallback(self):
        pp = ROOT / 'Jobs' / 'SH22' / 'SH22.dshift.json'
        proj = normalize_project(json.loads(pp.read_text(encoding='utf-8')))
        raw = read_gds(str(pp.parent / proj['gds_path']), proj['top_cell'])
        data, proj = restore_project_geometry(raw, proj)
        p = Parameters(**proj['parameters'])
        cfg = replace(ViewSettings.from_dict(proj['settings']), reconstruction_style='c1')
        cache = GeometryCache(data)
        case = build_case(data, proj['roles'], layer_order=proj['layer_order'])
        model = ContactModel(data, case, proj['roles'], case['connections'], cache, proj['motions'], p)
        r = Reconstructor(model, p, cfg)
        try:
            plans = r._plans_for_all_poses(None)
            pt = Point(-241542.0, 245414.0)
            entry = min([e for e in plans if e.get('plan') is not None], key=lambda e: e['plan'].source.distance(pt))
            plan = entry['plan']
            s_corner = float(plan.route.project(pt))
            cat = ExtremeCatalog(case, p)
            pose1 = cat.locals[entry['owner']][1]
            # Exact SH22 (-241542, 245414) corner drag poses that previously raised
            # AttributeError: 'NoneType' object has no attribute 'difference' in c1 mode
            for a, b, pose in (
                (663.8, 721.1, np.array([-15.0, 15.0, 0.017453292519943295])),
                (593.8, 652.1, np.array([15.0, 0.0, -0.017453292519943295])),
                (593.8, 644.5, np.array([15.0, -15.0, 0.017453292519943295])),
            ):
                part = plan.partition(a, b)
                self.assertIsNotNone(part)
                _, regs, err, _ = solve(r, plan, part, model.bound.centers[entry['owner']], pose, quick=False, native_check=False)
                self.assertIsNone(err)
                self.assertIn(regs['A'].geom_type, ('Polygon', 'MultiPolygon'))
                self.assertTrue(regs['A'].is_valid and not regs['A'].is_empty)
            # Direct GeometryCollection regression test for measure()
            part0 = plan.partition(plan.nominal_first, plan.nominal_second)
            gc_regions = dict(
                A=shapely.GeometryCollection([part0['A'], LineString(plan.route.coords[:2])]),
                B=part0['B'], C=part0['C'],
            )
            width, least, mean = measure(part0, plan, model.bound.centers[entry['owner']], pose1, gc_regions)
            self.assertTrue(np.isfinite(width) and np.isfinite(least) and np.isfinite(mean))

            # Direct regression test for reconstruction_joint.prepare when a ref is missing from replacements
            engine, fdata, fcase = fixture()
            try:
                fr = engine.reconstructor
                grid = ExtremeCatalog(fcase, engine.p)
                real_frame = fr.frame
                def dropped_frame(states, *a, **kw):
                    out = real_frame(states, *a, **kw)
                    out = dict(out, replacements={})
                    return out
                with patch.object(fr, 'frame', side_effect=dropped_frame):
                    bank = joint_prepare(fr, grid, indices=[0])
                self.assertIn(('5/0', 0), bank)
                self.assertFalse(bank[('5/0', 0)]['values'][next(iter(bank[('5/0', 0)]['values']))]['geometry'].is_empty)
            finally:
                engine.close()
        finally:
            r.close()

    def test_large_area_filter_and_multizone_live_serial(self):
        # Req 9: batch area_filter preserves exact polygon counts and filters tiny shapes
        polys = [np.array([[i * 20., j * 20.], [i * 20. + 10., j * 20.], [i * 20. + 10., j * 20. + 10.], [i * 20., j * 20. + 10.]])
                 for i in range(40) for j in range(40)]
        polys.append(np.array([[0., 0.], [0.2, 0.], [0.2, 0.2], [0., 0.2]]))  # 0.04 um^2 -> filtered
        raw = GDSData(path='big.gds', top_name='TOP', top_names=['TOP'], sha256='big', dbu_um=0.001,
                      layers={'5/0': polys}, auto_roles={'5/0': '固定RDL'})
        filtered, _ = area_apply(raw, {'parameters': {'minimum_area_um2': 0.5, 'minimum_edge_um': 0.01}})
        self.assertEqual(len(filtered.layers['5/0']), 1600)

        # Req 9: single metal polygon with 24 movable regions on GD55_1119 edits smoothly via live_serial
        pp = ROOT / 'Jobs' / 'GD55_1119' / 'GD55_1119.dshift.json'
        proj = normalize_project(json.loads(pp.read_text(encoding='utf-8')))
        raw2 = read_gds(str(pp.parent / proj['gds_path']), proj['top_cell'])
        data2, proj2 = restore_project_geometry(raw2, proj)
        p2 = Parameters(**proj2['parameters'])
        cfg2 = ViewSettings.from_dict(proj2['settings'])
        cache2 = GeometryCache(data2)
        case2 = build_case(data2, proj2['roles'], layer_order=proj2['layer_order'])
        model2 = ContactModel(data2, case2, proj2['roles'], case2['connections'], cache2, proj2['motions'], p2)
        r2 = Reconstructor(model2, p2, cfg2)
        try:
            ref = ('5/0', 24)
            states = np.zeros((len(case2['dies']), 3))
            res = r2.frame(states, diagnostics=False)
            piece0 = next(q for q in res['pieces'] if (q['layer'], q['index']) == ref and q.get('route_plan') is not None)
            plan0 = piece0['route_plan']
            new_cfg = replace(cfg2, reconstruction_intervals=tuple(cfg2.reconstruction_intervals) + (
                (*plan0.key, plan0.identity, piece0['interval'][0] + 5., piece0['interval'][1]),
            ))
            old_frame = dict(states=states, rotation_centers=model2.bound.centers.copy(), reconstruction=res)
            draft = live_serial(r2, new_cfg, old_frame, ref, draft=True)
            self.assertTrue(any(ref[1] in row['indices'] for row in draft['reconstruction']['replacements'][ref[0]]))
        finally:
            r2.close()

    def test_gui_req5_req6_req7_req8_end_to_end(self):
        from die_shift_studio.gui import MainWindow, W
        from qt_shutdown import close_window

        app = W.QApplication.instance() or W.QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
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
                # Req 5 & 7 on fresh MainWindow
                self.assertTrue(all(h.isChecked() for h in w.process_sections.values()))
                self.assertEqual(w.setting_fields['space_export_interval_um'].value(), 100.)

                engine, data, _ = fixture()
                p, roles = engine.p, engine.roles
                engine.close()
                lib = gdstk.Library(unit=1e-6, precision=1e-9)
                cell = lib.new_cell('TOP')
                for name, rows in data.layers.items():
                    layer, datatype = map(int, name.split('/'))
                    cell.add(*(gdstk.Polygon(xy, layer=layer, datatype=datatype) for xy in rows))
                cell.add(gdstk.rectangle((200, 100), (310, 140), layer=5))  # will edit nodes
                cell.add(gdstk.rectangle((400, 100), (480, 140), layer=5))  # will delete
                layout = folder / 'source.gds'
                lib.write_gds(layout)
                orig_sha = hashlib.sha256(layout.read_bytes()).hexdigest()

                job = folder / 'job.dshift.json'
                job.write_text(json.dumps(dict(
                    schema_version=5, gds_path=str(layout), top_cell='TOP', roles=roles,
                    layer_order=['6/0', '5/0', '4/0', '3/0', '128/0', '127/0'], parameters=asdict(p),
                    settings={'animation_enabled': False, 'reconstruction_auto': False},
                    overlays={'reconstruction': True},
                )))
                w.load_project(job)
                wait()
                self.assertEqual(len(w.data.layers['5/0']), 3)
                self.assertTrue(all(h.isChecked() for h in w.process_sections.values()))

                # Req 6: delete while reconstruction_auto=False defers with ZERO compute & keeps marker
                idx_del = next(i for i, xy in enumerate(w.data.layers['5/0']) if np.isclose(xy[:, 0].min(), 400, atol=1))
                w.selection.apply_hits([('5/0', idx_del)], 'replace')
                w.delete_selected_shapes()
                app.processEvents()
                self.assertFalse(w._tasks)
                self.assertFalse(w.coverage_timer.isActive())
                self.assertEqual(len(w.data.layers['5/0']), 3)
                self.assertTrue(w.reconstruction_pending and w.pending_deleted_shapes)
                self.assertTrue(w.reconstruction_overlay.staged.get_visible())

                # Apply in order on compute_reconstruction
                w.compute_reconstruction()
                wait()
                self.assertEqual(len(w.data.layers['5/0']), 2)
                self.assertFalse(w.pending_deleted_shapes)

                # Undo already-applied deletion while reconstruction_auto=False defers with restore marker
                w.undo_edit()
                app.processEvents()
                self.assertFalse(w._tasks)
                self.assertEqual(len(w.data.layers['5/0']), 2)
                self.assertTrue(w.reconstruction_pending and w.pending_restored_shapes)
                self.assertTrue(w.reconstruction_overlay.staged.get_visible())

                # Redo cancels pending restore (back to deleted), then compute_reconstruction clears
                w.redo_edit()
                app.processEvents()
                self.assertFalse(w._tasks)
                self.assertFalse(w.pending_restored_shapes)

                # Req 8: node edit + deletion reflected in GDS, OAS, DXF, ABC DXF, and project reopen
                idx_edit = next(i for i, xy in enumerate(w.data.layers['5/0']) if np.isclose(xy[:, 0].min(), 200, atol=1))
                base = getattr(w.data, '_source_snapshot', w.data)
                kind, orig_xy, _ = shape_nodes(base, '5/0', idx_edit)
                new_nodes = [[200., 100.], [350., 100.], [350., 155.], [200., 155.]]
                w.apply_node_edits([dict(layer='5/0', index=idx_edit, original=node_fingerprint(kind, orig_xy), nodes=new_nodes)])
                wait()
                w.compute_reconstruction()
                wait()

                snap = snapshot(w.data, w.geometry_cache, w.current_frame, reconstruction=True)
                gds_out = folder / 'out.gds'
                oas_out = folder / 'out.oas'
                dxf_out = folder / 'out.dxf'
                abc_out = folder / 'out_abc.dxf'
                write_layout(snap, gds_out, w.data.dbu_um, OutputOptions(merge_layers=False))
                write_layout(snap, oas_out, w.data.dbu_um, OutputOptions(merge_layers=False))
                write_dxf(snap, dxf_out, settings=DxfSettings(merge_layers=False))
                split = dict(w.current_frame['reconstruction'], pieces=[
                    dict(q, regions=q['nominal_regions']) for q in w.current_frame['reconstruction']['pieces']
                ])
                nom_snap = snapshot(w.data, w.geometry_cache, dict(states=np.zeros((len(w.case['dies']), 3))), reconstruction=False)
                write_regions(nom_snap, split, w.roles(), abc_out, settings=DxfSettings(region_auto_join=True))

                for exported in (gds_out, oas_out):
                    reader = gdstk.read_gds if exported.suffix == '.gds' else gdstk.read_oas
                    l5 = [Polygon(q.points) for q in reader(str(exported)).top_level()[0].get_polygons() if q.layer == 5]
                    self.assertEqual(len(l5), 2)
                    self.assertTrue(any(abs(g.bounds[2] - 350) < 0.1 and abs(g.bounds[3] - 155) < 0.1 for g in l5))
                    self.assertFalse(any(g.bounds[0] > 390 and g.bounds[1] > 90 for g in l5))

                dxf_doc = ezdxf.readfile(dxf_out)
                l5_dxf = [Polygon(list(e.get_points('xy'))) for e in dxf_doc.modelspace() if e.dxf.layer == 'L5_D0']
                self.assertEqual(len(l5_dxf), 2)
                self.assertTrue(any(abs(g.bounds[2] - 350) < 0.1 and abs(g.bounds[3] - 155) < 0.1 for g in l5_dxf))
                self.assertFalse(any(g.bounds[0] > 390 and g.bounds[1] > 90 for g in l5_dxf))

                abc_doc = ezdxf.readfile(abc_out)
                abc_l5 = [xy for layer, xy, _ in expanded_outlines(abc_doc.modelspace()) if layer.startswith('L5_D0_')]
                self.assertTrue(any(abs(xy[:, 0].max() - 350) < 0.1 and abs(xy[:, 1].max() - 155) < 0.1 for xy in abc_l5))
                self.assertFalse(any(xy[:, 0].min() > 390 and xy[:, 1].min() > 90 for xy in abc_l5))

                saved = folder / 'saved.dshift.json'
                w.write_project(saved)
                self.assertEqual(hashlib.sha256(layout.read_bytes()).hexdigest(), orig_sha)
                w.load_project(saved)
                wait()
                self.assertEqual(len(w.data.layers['5/0']), 2)
                reopened = [Polygon(xy) for xy in w.data.layers['5/0']]
                self.assertTrue(any(abs(g.bounds[2] - 350) < 0.1 and abs(g.bounds[3] - 155) < 0.1 for g in reopened))
                self.assertFalse(any(g.bounds[0] > 390 and g.bounds[1] > 90 for g in reopened))
                self.assertEqual(hashlib.sha256(layout.read_bytes()).hexdigest(), orig_sha)
            finally:
                close_window(app, w)


if __name__ == '__main__':
    unittest.main()
