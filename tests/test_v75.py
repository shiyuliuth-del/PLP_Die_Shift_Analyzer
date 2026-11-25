"""Serial deformation, coupled box edits and SH22 material-section regressions."""
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

from die_shift_studio.between_motion import solve_graph_weights, solve_serial_weights
from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_zones import live_serial
from die_shift_studio.settings import ViewSettings
from test_v38 import fixture
from test_v72 import vertices

ROOT = Path(__file__).resolve().parents[1]


class V75Tests(unittest.TestCase):
    def test_serial_transitions_share_motion_between_policy_anchors(self):
        self.assertEqual(solve_serial_weights(['min', 'average', 'average', 'max']),
                         [1., .5, .5, .5, .5, 0.])
        self.assertEqual(solve_serial_weights(['min', 'max']), [1., .5, .5, 0.])
        self.assertEqual(solve_graph_weights(4, {0:1.,3:0.}, {(0,1):1.,(1,2):1.,(2,3):1.},
                                             {1:'min',2:'max'}), {0:1.,1:.5,2:.5,3:0.})
        self.assertEqual(solve_serial_weights(['min', 'average', 'max'], moving_is_larger=False),
                         [1., 1., .5, 0., 0.])
        np.testing.assert_allclose(np.diff(solve_serial_weights(['average']*199)), -.005, atol=1e-12)
        engine, _, _ = fixture()
        settings = replace(ViewSettings(), reconstruction_between_motion='average',
                           reconstruction_angle_boxes=tuple(('5/0', vertices(x, -45, x+30, 45))
                                                            for x in (100, 280, 455)),
                           reconstruction_box_order=('aligned',)*3)
        r = Reconstructor(engine.contact, engine.p, settings)
        try:
            posed = r.frame(np.array([[0., 15., 0.]]), diagnostics=False, _raw=True)
            pieces = sorted(posed['pieces'], key=lambda q:q['interval'][0])
            self.assertEqual(len(pieces), 3)
            weights = [q['endpoint_weights'] for q in pieces]
            np.testing.assert_allclose([a-b for a,b in weights], [1./3.]*3)
            for q in pieces:
                self.assertAlmostEqual(q['moved_cut'].centroid.y-q['extra_cut'].centroid.y, 5., places=6)
            self.assertAlmostEqual(sum(q['moved_cut'].centroid.y-q['extra_cut'].centroid.y for q in pieces),15.)
        finally:
            r.close()
            engine.close()

    def test_sh22_auto_section_and_between_owner_selection(self):
        job = ROOT/'Jobs'/'SH22'/'SH22.dshift.json'
        project = normalize_project(json.loads(job.read_text(encoding='utf-8')))
        raw = read_gds(job.parent/project['gds_path'], project['top_cell'])
        data, project = restore_project_geometry(raw, project)
        roles = project['roles']
        params = Parameters.from_dict(project['parameters'])
        case = build_case(data, roles, layer_order=project['layer_order'])
        cache = GeometryCache(data)
        model = ContactModel(data, case, roles, case['connections'], cache, parameters=params)
        cfg = ViewSettings.from_dict(project['settings'])
        layer = '2/0'
        zero = np.zeros((len(case['dies']),3))
        refs = {}
        for x,y in [(-241037,246512),(-242643,246126)]:
            pt = shapely.Point(x,y)
            i = next(i for i,g in enumerate(cache.geometries[layer]) if g.covers(pt))
            refs[x,y]=(layer,i)
        r = Reconstructor(model, params, cfg)
        try:
            r.vias_by_target={ref:r.vias_by_target[ref] for ref in refs.values()}
            raw = r.frame(zero, targets=set(refs.values()), diagnostics=False, _raw=True)
            blade, = [q['moved_cut'] for q in raw['pieces'] if (q['layer'],q['index'])==refs[-241037,246512]]
            self.assertAlmostEqual(blade.length,118.68,delta=.15)
            x,y=-242643,246126
            settings = replace(cfg, reconstruction_boxes=((layer, vertices(x-12.5,y-16,x+12.5,y+16)),),
                               reconstruction_box_order=('free',))
            draft = live_serial(r,settings,dict(states=zero,reconstruction=raw),refs[x,y],draft=True)
            cursor = (-242600.,246127.)
            block, = [b for b in draft['reconstruction']['between_blocks'] if b['geometry'].contains(shapely.Point(cursor))]
            self.assertEqual(block['ref'],refs[x,y])
            self.assertTrue(block['locked'])
            from types import SimpleNamespace
            from die_shift_studio.selection import GeometrySelection
            on = SimpleNamespace(isChecked=lambda:True)
            canvas = SimpleNamespace(data=data,reconstruction_toggle=on,visible_boxes={layer:on},
                                     reconstruction_overlay=SimpleNamespace(between_views={block['id']:block}))
            hit=GeometrySelection.find_between_block(SimpleNamespace(w=canvas),cursor)
            self.assertIs(hit,block)
        finally:r.close()

    def test_qt_linked_drag_merges_and_unlinks_while_auto_is_off(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from matplotlib.backend_bases import MouseEvent
        from die_shift_studio.gui import MainWindow, W
        from die_shift_studio.shape_menu import show_shape_menu
        from qt_shutdown import close_window

        app = W.QApplication.instance() or W.QApplication([])
        engine,data,_ = fixture()
        roles,params = engine.roles,engine.p
        engine.close()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = gdstk.Library(unit=1e-6,precision=1e-9)
            cell = lib.new_cell('TOP')
            for layer,points in data.layers.items():
                number,datatype=map(int,layer.split('/'))
                copies=points if number>=127 else [*points,*(xy+(0.,100.) for xy in points)]
                cell.add(*(gdstk.Polygon(xy,layer=number,datatype=datatype) for xy in copies))
            gds=root/'case.gds';lib.write_gds(gds)
            job=root/'case.dshift.json'
            job.write_text(json.dumps(dict(
                schema_version=5,gds_path=str(gds),top_cell='TOP',roles=roles,parameters=asdict(params),
                layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],
                settings=dict(animation_enabled=False,reconstruction_auto=False,reconstruction_box_mode='free',
                              reconstruction_node_snap_um=-1.,reconstruction_between_motion='average'),
                overlays=dict(reconstruction=True))),encoding='utf8')
            window = MainWindow(jobs_dir=root/'Jobs')
            window.reconstruction_yield.refresh=lambda *_:None
            errors=[];window.error=lambda message:errors.append(str(message))
            window.show()
            overlay=window.reconstruction_overlay

            def wait():
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    app.processEvents()
                    if not (window._tasks or window.preview_pending or window.coverage_timer.isActive()
                            or overlay._stage_worker or window.cut_drag.worker):
                        self.assertFalse(errors,errors)
                        return
                    time.sleep(.01)
                self.fail('Qt reconstruction did not finish')

            def box(a,b):
                selector=window.region_select
                selector.state=dict(anchor=np.array([a,-30.]),current=np.array([a,-30.]),angle=0.)
                selector._finish(np.array([b,130.]))
                wait()

            def mouse(name,point,button=1):
                px,py=window.ax.transData.transform(point)
                return MouseEvent(name,window.canvas,px,py,button=button)

            def by_ref():
                return {i:sorted([p for p in overlay.pieces() if p['index']==i],key=lambda p:p['interval'])
                        for i in range(2)}

            def drag(point,destination,expected):
                mouse('button_press_event',point)._process()
                self.assertIsNotNone(window.cut_drag.state)
                self.assertEqual(len(window.cut_drag.state['edited_handles']),expected)
                mouse('motion_notify_event',destination)._process()
                window.cut_drag._tick()
                self.assertEqual(sum(q.get('marker') is not None for q in window.cut_drag.state['edited_handles']),expected)
                mouse('button_release_event',destination)._process()
                wait()

            try:
                window.load_project(job);wait()
                applied=window.current_frame
                box(150.,190.);box(320.,360.)
                self.assertEqual([len(q) for q in by_ref().values()],[2,2])
                window.set_view((280.,50.),565.,270.)
                window.plot_layout(preserve=True);window.canvas.draw()
                first=by_ref()[0][0]
                start=np.asarray(first['extra_cut'].centroid.coords[0])
                drag(start,start+np.array([150.,0.]),2)
                self.assertIs(window.current_frame,applied)
                merged=by_ref()
                self.assertEqual([len(q) for q in merged.values()],[1,1])
                for rows in merged.values():
                    self.assertAlmostEqual(rows[0]['interval'][1],360.,delta=.2)
                # Scope comes from the original selection and persists after merging.
                piece=merged[0][0]
                point=piece['extra_cut'].centroid.coords[0]
                menus=[]
                with patch.object(W.QMenu,'popup',lambda menu,*_:menus.append(menu)):
                    show_shape_menu(window,mouse('button_press_event',point,3),point)
                action,=[a for a in menus[0].actions() if a.text()=='拆分绑定动区']
                action.trigger()
                wait()
                self.assertTrue(window.view_settings.reconstruction_unlinked_boxes)
                old=by_ref()[1][0]['interval']
                mouse('button_press_event',point)._process()
                wheel=mouse('scroll_event',point,'up');wheel.step=4
                wheel._process()
                mouse('button_release_event',point)._process()
                wait()
                moved=by_ref()[0][0]
                self.assertGreater(abs(moved['boundary_angles'][1]),.05)
                angle=moved['boundary_angles'][1]
                point=moved['extra_cut'].centroid.coords[0]
                drag(point,np.array(point)+[25.,0.],1)
                self.assertEqual(by_ref()[1][0]['interval'],old)
                self.assertGreater(by_ref()[0][0]['interval'][1],380.)
                self.assertAlmostEqual(by_ref()[0][0]['boundary_angles'][1],angle,places=5)
                window.compute_reconstruction();wait()
                self.assertFalse(window.reconstruction_pending)
                window.write_project(job)
                loaded=ViewSettings.from_dict(json.loads(job.read_text(encoding='utf8'))['settings'])
                self.assertEqual(loaded.reconstruction_unlinked_boxes,window.view_settings.reconstruction_unlinked_boxes)
                from die_shift_studio.dxf_export import snapshot
                from die_shift_studio.region_export import write_regions
                from die_shift_studio.region_import import metadata_for, read, recover_metadata
                dxf=root/'regions.dxf'
                layout=snapshot(window.data,window.geometry_cache)
                layout=replace(layout,layers={'5/0':layout.layers['5/0']})
                metadata=metadata_for(window.data,roles,window.ordered_layers(),window.params(),loaded,
                                      ('5/0',),window.dxf_settings,{})
                write_regions(layout,window.current_frame['reconstruction'],roles,dxf,
                              settings=window.dxf_settings,edit_metadata=metadata)
                contours,record=read(dxf,with_metadata=True)
                before_split=replace(loaded,reconstruction_unlinked_boxes=())
                restored=recover_metadata(record,contours,window.data,roles,window.ordered_layers(),window.params(),before_split)
                self.assertIsNotNone(restored)
                self.assertEqual(restored.reconstruction_unlinked_boxes,loaded.reconstruction_unlinked_boxes)
            finally:
                close_window(app,window)


if __name__=='__main__':
    unittest.main()
