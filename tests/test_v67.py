"""DRC waiver evidence, exact native output sizing, and contour registration."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import gdstk
import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon, box

from die_shift_studio import output_compensation as compensation
from die_shift_studio.drc_waivers import approved, pose_id, record, witness_id
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.dxf_export import Snapshot
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.layout_export import write
from die_shift_studio.measured_core import Session
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction_route import RoutePlan
from die_shift_studio.reconstruction_snap import node_snap
from die_shift_studio.region_export import contours
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from test_v38 import fixture


class V67Tests(TestCase):
    def test_new_defaults_and_saved_explicit_defaults(self):
        new = normalize_project({'schema_version': 5})
        self.assertTrue(new['overlays']['drc'])
        self.assertFalse(new['settings']['animation_enabled'])
        self.assertTrue(new['dxf_options']['region_auto_join'])
        self.assertEqual(new['settings']['reconstruction_boundary_mode'], 'perpendicular')
        self.assertEqual(new['settings']['reconstruction_node_snap_um'], 0)
        old = normalize_project({'schema_version': 5, 'settings': {'show_contact_markers': False,
                                'animation_enabled': True, 'reconstruction_boundary_mode': 'wide'},
                                'dxf_options': {'region_auto_join': False}, 'layer_compensation': {'5/0': -4.1234}})
        self.assertFalse(old['overlays']['drc'])
        self.assertTrue(old['settings']['animation_enabled'])
        self.assertEqual(old['settings']['reconstruction_boundary_mode'], 'wide')
        self.assertFalse(old['dxf_options']['region_auto_join'])
        self.assertEqual(old['layer_compensation'], {'5/0': -4.123})

    def test_seam_remains_at_matched_cut_on_both_sides_after_sizing(self):
        original = box(0, 0, 100, 20)
        regions = dict(A=box(30, 0, 72, 20), B=box(72, 0, 100, 20), C=box(0, 0, 30, 20))
        snapshot = Snapshot({'5/0': (original.wkb,), '127/0': (box(-40, -40, 150, 80).wkb,)}, 'TOP', None, 0)
        roles = {'5/0': '固定RDL', '127/0': '封装边框'}
        for delta in (5., -2.):
            rows = contours(snapshot, {'pieces':[dict(layer='5/0', regions=regions)]}, roles,
                            DxfSettings(), compensation={'5/0': delta, '127/0': delta})[0]
            layers = {name: shapely.union_all([LineString(xy) for key,xy in rows if key==name])
                      for name in ('L5_D0_A','L5_D0_B','L5_D0_C')}
            target = box(-delta,-delta,100+delta,20+delta).boundary
            combined = shapely.union_all(list(layers.values()))
            self.assertTrue(combined.equals(target))
            self.assertAlmostEqual(sum(g.length for g in layers.values()), target.length)
            for a,b,x in (('A','C',30),('B','C',72)):
                seam = layers['L5_D0_'+a].intersection(layers['L5_D0_'+b])
                self.assertEqual(set(map(tuple,shapely.get_coordinates(seam))),{(x,-delta),(x,20+delta)})
            reference,=[LineString(xy) for key,xy in rows if key=='PKG_REF']
            self.assertTrue(reference.equals(box(-40-delta,-40-delta,150+delta,80+delta).boundary))

    def test_native_layer_sizing_and_gds_output(self):
        subject = Polygon(box(0,0,30,30).exterior.coords,[box(10,10,20,20).exterior.coords])
        s = Snapshot({'5/0': (subject.wkb,), '6/0': (box(40,0,41,1).wkb,)}, 'TOP', None, 0)
        adjusted=compensation.compensate_snapshot(s,{'5/0':2, '6/0':-2},.001)
        self.assertEqual(adjusted.layers['6/0'],())
        expected=box(-2,-2,32,32).difference(box(12,12,18,18))
        self.assertTrue(shapely.from_wkb(adjusted.layers['5/0'][0]).equals(expected))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'biased.gds'
            write(adjusted,path,.001,progress=None)
            top=gdstk.read_gds(path).top_level()[0]
            self.assertEqual({p.layer for p in top.polygons},{5})
            self.assertAlmostEqual(sum(p.area() for p in top.polygons),expected.area)

    def test_measured_rigid_geometry_is_sized_once(self):
        e,data,case=fixture()
        session=Session(data,case,e.roles,{},e.p,ViewSettings(),GeometryCache(data),compensation={'4/0':3})
        try:
            with patch.object(compensation,'sized',wraps=compensation.sized) as native:
                session.prepare_output()
                baseline=shapely.from_wkb(session.nominal_snapshot().layers['4/0'][0])
                for x in (5,10):
                    states=np.array([[x,0.,0.]])
                    frame=dict(states=states,layers=session._full_bound(session.p).transform(states))
                    actual=shapely.from_wkb(session._fast_snapshot(frame).layers['4/0'][0])
                    self.assertLess(actual.symmetric_difference(translate(baseline,xoff=x)).area,1e-7)
                self.assertEqual(native.call_count,1)
        finally:
            session.close()
            e.close()

    def test_waiver_only_excludes_the_proved_pose(self):
        e,_,case=fixture()
        try:
            grid=ExtremeCatalog(case,e.p)
            states=grid[0]['states']
            frame={'states': states}
            a=dict(layer='5/0',kind='线宽不足',indices=(0,),geometry=box(0,0,2,2))
            b={**a,'geometry':box(8,0,10,2)}
            self.assertNotEqual(witness_id(a),witness_id(b))
            key=pose_id(states)
            context='a'*64
            first=record(frame,[a,b],{},a,context,0)
            self.assertFalse(approved({key:first},context,grid,lambda i:grid[i]['states']))
            second=record(frame,[a,b],first,b,context,0)
            excepted=approved({key:second},context,grid,lambda i:grid[i]['states'])
            self.assertEqual(excepted,{0})
            self.assertFalse(approved({key:second},'b'*64,grid,lambda i:grid[i]['states']))
            index=ProblemIndex(grid,[((),True)],excepted)
            self.assertEqual(index.total,grid.total-1)
            self.assertEqual(index.at(0),1)
            self.assertTrue(index.zero)
        finally:e.close()

    def test_snap_is_soft_and_ignores_remote_nodes(self):
        line=LineString([(0,0),(100,0)])
        g=Polygon([(0,-5),(51,-5),(53,-7),(100,-7),(100,7),(0,7)])
        plan=RoutePlan('5/0',0,0,(),g,line,box(-1,-1,1,1),(box(99,-1,100,1),),.001,0)
        self.assertEqual(node_snap(plan,48,-1,1,{}),48)
        self.assertEqual(node_snap(plan,48,5,0,{}),48)
        self.assertGreater(node_snap(plan,48,5,.8,{}),48)
        self.assertLess(node_snap(plan,48,5,.8,{}),51)
        self.assertEqual(node_snap(plan,40,5,.8,{}),40)
