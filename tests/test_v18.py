"""Geometry contracts: interior terminals, convex smooth pads, CPU/GPU parity."""
import math,json,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import Point,box,LineString
from die_shift_studio.settings import ViewSettings
from die_shift_studio.axial_adaptation import axial_candidates
from die_shift_studio.pad_envelope import pad_envelopes
from die_shift_studio.deformation import warp_polygon
from die_shift_studio.preview import rigid_transform
from die_shift_studio.drc import rule_region

class V18Tests(unittest.TestCase):
    def test_middle_terminal_keeps_both_fixed_ends_and_uniform_sections(self):
        source=box(-45,-300,45,300);r=dict(owner=0,poly=Point(0,0).buffer(35),center=[0,0])
        anchors=[Point(0,y).buffer(15) for y in (-240,240)];states=np.array([[45.,5.,0.]])
        f=next(axial_candidates(source,[r],anchors,np.zeros((1,2)),states,ViewSettings(),15,.001))
        self.assertTrue(f.injectivity_certificate(source))
        for anchor in anchors:np.testing.assert_allclose(f(shapely.get_coordinates(anchor)),shapely.get_coordinates(anchor),atol=1e-10)
        np.testing.assert_allclose(f(np.array([[0.,0]])),[[45,5]],atol=1e-10)
        # A full cross section receives one weight; no symmetric radial waist.
        xy=np.array([[-40.,70],[0,70],[40,70]]);delta=f(xy)-xy
        np.testing.assert_allclose(delta,np.repeat(delta[1:2],3,axis=0),atol=1e-10)
        g=warp_polygon(source,f,.02,5);self.assertTrue(g.is_valid)
        self.assertTrue(rule_region(g,15,.001).is_empty)
        self.assertTrue(all(g.covers(a) for a in anchors))

    def test_axial_certificate_rejects_fold(self):
        source=box(-30,-250,30,250);r=dict(owner=0,poly=Point(0,0).buffer(20),center=[0,0])
        anchors=[Point(0,y).buffer(15) for y in (-200,200)]
        fields=list(axial_candidates(source,[r],anchors,np.zeros((1,2)),np.array([[0.,500,0]]),ViewSettings(),15,.001))
        self.assertTrue(fields);self.assertFalse(any(f.injectivity_certificate(source) for f in fields))

    def test_overlapping_landing_disks_have_convex_unpinched_valid_outline(self):
        source=LineString([(0,0),(0,120)]).buffer(50,quad_segs=32)
        r=dict(owner=0,poly=Point(0,120).buffer(20,quad_segs=32),center=[0,120])
        anchors=[Point(0,0).buffer(20,quad_segs=32)];z=np.array([[-35.,-35.,-math.pi/180]])
        f=next(pad_envelopes(source,[r],anchors,np.zeros((1,2)),z,ViewSettings(),15,15,.001))
        g=f.candidate_geometry(.05,.001)
        moved=shapely.Polygon(rigid_transform(shapely.get_coordinates(r['poly']),np.zeros(2),z[0]))
        self.assertTrue(g.is_valid);self.assertGreater(g.area/g.convex_hull.area,.999999)
        self.assertTrue(g.covers(anchors[0]));self.assertGreaterEqual(g.boundary.distance(moved),15)
        self.assertTrue(rule_region(g,15,.001).is_empty)
        # Refuse to shortcut holes or a strongly nonconvex bent/branched net.
        holed=source.difference(Point(0,50).buffer(5))
        self.assertFalse(list(pad_envelopes(holed,[r],anchors,np.zeros((1,2)),z,ViewSettings(),15,15,.001)))

    def test_gpu_formula_and_missing_runtime_fallback_match_rigid_endpoints(self):
        from die_shift_studio.acceleration import blend_points
        rng=np.random.default_rng(18);xy=rng.normal(size=(500,2))*100;center=np.array([37.,-45]);z=np.array([-35.,-35.,-.017])
        for method in ('bezier','arc'):
            for w in (np.zeros(500),np.ones(500),rng.random(500)):
                with patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cpu'}):
                    fallback=blend_points(xy,w,center,z,method,30,'cuda')
                expected=blend_points(xy,w,center,z,method,30,'cpu')
                np.testing.assert_allclose(fallback,expected,atol=1e-10)
                if np.all(w==0):np.testing.assert_allclose(expected,xy,atol=1e-10)
                if np.all(w==1):np.testing.assert_allclose(expected,rigid_transform(xy,center,z),atol=1e-10)

    def test_gpu_dispatch_and_invalid_device_output_fail_back_to_cpu(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        from die_shift_studio.acceleration import blend_points
        class Device:
            cuda=SimpleNamespace(Device=lambda n:nullcontext(),runtime=SimpleNamespace(memGetInfo=lambda:(10**9,10**9)))
            asnumpy=staticmethod(np.asarray)
            def __getattr__(self,key):return getattr(np,key)
        device=Device();xy=np.array([[3.,7.],[100,200]]);w=np.array([.3,.8]);center=np.array([10.,-5]);z=np.array([-35.,-35.,-.017])
        with patch.dict('sys.modules',{'cupy':device}),patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cuda'}):
            for method in ('bezier','arc'):
                expected=blend_points(xy,w,center,z,method,30,'cpu')
                np.testing.assert_allclose(blend_points(xy,w,center,z,method,30,'cuda'),expected)
            device.asnumpy=lambda v:np.asarray(v)+1
            with self.assertLogs('die_shift_studio.acceleration',level='ERROR'):
                got=blend_points(xy,w,center,z,'arc',30,'cuda')
            np.testing.assert_allclose(got,expected)

    def test_portable_middle_case_accepted_and_revalidation_is_cached(self):
        from die_shift_studio.core import read_gds,build_case,Parameters
        from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
        root=Path(__file__).resolve().parents[1];q=json.loads((root/'examples/示例K_中段连接与平滑孔盘.json').read_text(encoding='utf-8'))
        data=read_gds(root/'examples'/q['gds_path']);case=build_case(data,q['roles'],layer_order=q['layer_order'])
        e=PreviewEngine(GeometryCache(data),case,q['roles'],{},Parameters.from_dict(q['parameters']),{},case['connections'])
        try:
            cfg=ViewSettings();e.configure_deformation(True,cfg);z=np.array([[-35.,-35.,-math.pi/180]])
            f=e.frame(z);self.assertEqual(f['deformation']['accepted_count'],2)
            self.assertFalse(f['deformation']['violations'])
            self.assertEqual({v['mode'] for v in f['deformation']['replacements']['5/0']},{'axial','pad_envelope'})
            with patch.object(e.deformer,'search',side_effect=AssertionError('Recomputed')):self.assertIs(e.frame(z),f)
            co=e.deformer.components[0];dp=e.deformer;dp.prepare_pose(z)
            # A changed neighbour context must rerun DRC, but can reuse contours.
            with patch('die_shift_studio.deformation.warp_polygon',side_effect=AssertionError('Repeated contour')):
                g,field,span=dp.search(co,co.records,co.anchors,z,[],dp._snapshot({}))
                self.assertTrue(field.verified)
        finally:e.close()

if __name__=='__main__':unittest.main()
