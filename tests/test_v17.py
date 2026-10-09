"""V17: signed rules, conservative mapping proofs, sparse warp and cache playback."""
import math,time,unittest
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import shapely
from shapely.geometry import box,Point
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings
from die_shift_studio.drc_rules import effective_rules
from die_shift_studio.deformation import Field,warp_polygon
from die_shift_studio.route_adaptation import SlabField
from die_shift_studio.drc import GlobalDRC,separation_region
from die_shift_studio.native_backend import geometry

class V17Tests(unittest.TestCase):
    def test_defaults_and_tolerance_validation(self):
        p=Parameters();cfg=ViewSettings()
        self.assertEqual((p.line_width_um,p.line_space_um,p.wafer_line_width_um,p.wafer_line_space_um,p.annulus_um),(15,)*5)
        self.assertTrue(cfg.animation_enabled);self.assertEqual(effective_rules(p,cfg),(15,15))
        for tolerance,rule in ((1,14),(-1,16)):
            q=replace(cfg,drc_tolerance_um=tolerance);self.assertEqual(effective_rules(p,q),(rule,rule));self.assertNotEqual(cfg.deformation_key(),q.deformation_key())
        with self.assertRaises(ValueError):effective_rules(p,replace(cfg,drc_tolerance_um=15))

    def test_signed_tolerance_changes_real_fixed_rdl_drc(self):
        # Two 14.5 um tracks separated by 14.5 um: nominal 15 fails; +1 passes.
        polys=np.array([box(0,0,100,14.5),box(0,29,100,43.5)],object)
        model=SimpleNamespace(data=SimpleNamespace(dbu_um=.001),roles={'5/0':'固定RDL'},records=[],
            cache=SimpleNamespace(layers={'5/0':{'geometry':polys}}),ids={('5/0',0):0,('5/0',1):1},nodes=[{'net':0},{'net':1}])
        d=GlobalDRC(model,Parameters());z=np.zeros((1,3))
        strict=d.frame(z,settings=ViewSettings());relaxed=d.frame(z,settings=ViewSettings(drc_tolerance_um=1))
        self.assertTrue(strict);self.assertFalse(relaxed)
        self.assertTrue(d.frame(z,settings=ViewSettings(drc_tolerance_um=-1)))
        self.assertEqual(effective_rules(Parameters(annulus_um=15),ViewSettings(drc_tolerance_um=1)),(14,14))

    def test_support_bound_does_not_sum_remote_pads(self):
        records=[dict(owner=0,center=[x,0]) for x in (0,1000,2000)]
        f=Field(records,np.zeros((1,2)),np.array([[10.,0,0]]),[10]*3,100,ViewSettings())
        self.assertAlmostEqual(f.jacobian_bound(),1.875*.1)
        # Numerical Jacobians are below the analytic bound at support crossings.
        for xy in np.array([[0.,0],[60,4],[1100,0],[2050,20]]):
            eps=1e-4;j=np.column_stack([(f((xy+eps*e)[None])[0]-f((xy-eps*e)[None])[0])/(2*eps) for e in np.eye(2)])
            self.assertLessEqual(np.linalg.norm(j-np.eye(2),2),f.jacobian_bound()+1e-7)

    def test_slab_transverse_shear_is_certified_but_fold_is_rejected(self):
        source=box(-30,-40,100,40);records=[dict(owner=0,poly=Point(-10,0).buffer(3),center=[-10,0])]
        def field(state):return SlabField(records,np.zeros((1,2)),np.array([state],float),ViewSettings(),box(-30,-40,0,40),box(0,-40,20,40),[0,0],[1,0],20,80)
        f=field([0,30,0]);self.assertGreater(f.jacobian_bound(),.98);self.assertTrue(f.injectivity_certificate(source))
        self.assertFalse(field([30,0,0]).injectivity_certificate(source))
        for x in np.linspace(0,20,25):
            xy=np.array([x,3]);eps=1e-5;j=np.column_stack([(f((xy+eps*e)[None])[0]-f((xy-eps*e)[None])[0])/(2*eps) for e in np.eye(2)])
            self.assertAlmostEqual(np.linalg.det(j),1.,places=6)
        self.assertTrue(warp_polygon(source,f,.02,2).is_valid)
        compressed=field([12,0,0]);self.assertFalse(compressed.injectivity_certificate(source))
        compressed.gentle_profile=True;self.assertTrue(compressed.injectivity_certificate(source))
        t=np.linspace(0,1,1001);w=compressed.weights(np.column_stack((20*t,np.zeros_like(t))))[:,0]
        self.assertLessEqual(float(np.max(abs(np.diff(w)/np.diff(t)))),1.250001)
        self.assertEqual((w[0],w[-1]),(1,0))
        self.assertTrue(np.all(np.diff(w)<=0))

    def test_sparse_refinement_preserves_far_contours_and_matches_dense(self):
        source=box(-500,-30,500,30);f=Field([dict(owner=0,center=[-480,0])],np.zeros((1,2)),np.array([[1,2,.001]]),[10],70,ViewSettings())
        sparse=warp_polygon(source,f,.005,3)
        dense=warp_polygon(source,lambda xy:f(xy),.005,3)
        self.assertLess(sparse.symmetric_difference(dense).area,.03)
        self.assertEqual(sparse.bounds[2],source.bounds[2])

    def test_local_foreign_clipping_preserves_actual_drc(self):
        a=box(0,0,100,20);b=shapely.segmentize(box(-10000,30,10000,50),5)
        before=geometry('separation',a,.001,15,b);after=separation_region(a,b,15,.001)
        self.assertLess(before.symmetric_difference(after).area,1e-7)

    def test_portable_short_pad_case_deforms_and_keeps_fixed_anchor(self):
        import json
        from pathlib import Path
        from die_shift_studio.core import read_gds,build_case
        from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
        root=Path(__file__).resolve().parents[1];q=json.loads((root/'examples/示例J_短宽孔盘与DRC容差.json').read_text(encoding='utf-8'))
        data=read_gds(root/'examples'/q['gds_path']);case=build_case(data,q['roles'],layer_order=q['layer_order'])
        engine=PreviewEngine(GeometryCache(data),case,q['roles'],{},Parameters.from_dict(q['parameters']),{},case['connections'])
        try:
            engine.configure_deformation(True,ViewSettings());f=engine.frame(np.array([[-25.,-25.,0]]))
            self.assertEqual(f['deformation']['accepted_count'],1)
            value=f['deformation']['replacements']['5/0'][0];self.assertIn(value['mode'],('directional_pad','pad_envelope'))
            self.assertTrue(value['verified'])
            for anchor in engine.deformer.components[0].anchors:self.assertTrue(value['geometry'].covers(anchor))
        finally:engine.close()

    def test_cancelled_parallel_search_never_restarts_in_serial(self):
        from unittest.mock import patch,MagicMock
        from concurrent.futures import Future
        from die_shift_studio.deformation import DeformationPreview
        # Only orchestration is mocked: one completed native job reaches the
        # real progress/cancellation path before any fallback is allowed.
        dp=object.__new__(DeformationPreview);dp.settings=ViewSettings(deformation_execution='process',deformation_workers=2)
        dp.model=SimpleNamespace(cache=SimpleNamespace(layers={}),data=SimpleNamespace(count=0));dp.p=Parameters();dp.executor=None;dp.frame_generation=1
        done=Future();done.set_result((None,''));pending=Future();pool=MagicMock();pool.submit.side_effect=[done,pending]
        c=SimpleNamespace(key='5/0',indices=(0,));tasks=[(c,[],[],(),()),(c,[],[],(),())];fn=MagicMock()
        profile=dict(cpu=8,workers=4,process_workers=2,available_memory_mb=8192)
        def cancel(*_):raise InterruptedError('changed pose')
        with patch('die_shift_studio.resources.resource_profile',return_value=profile),patch('concurrent.futures.ProcessPoolExecutor',return_value=pool),patch('concurrent.futures.as_completed',return_value=iter([done])):
            with self.assertRaises(InterruptedError):dp.proposals(fn,tasks,np.zeros((1,3)),{},0,False,cancel)
        fn.assert_not_called();self.assertTrue(pending.cancelled())

    def test_slow_frames_never_consume_playback_budget_and_cache_reuses(self):
        from die_shift_studio.prepared_motion import prepare_motion
        calls=[]
        def frame(z,*args,**kwargs):time.sleep(.04);calls.append(z.copy());return dict(states=z.copy(),deformation={'states':z.copy()})
        e=SimpleNamespace(signature='test',cache_key=lambda:(),bound=SimpleNamespace(owner_radii=np.array([500.])),frame=frame)
        cfg=ViewSettings(animation_duration_ms=100,animation_fps=30);cache={};start=np.zeros((1,3));end=np.array([[50.,0,0]])
        r=prepare_motion(e,start,end,cfg,1000,cache,lambda *_:None)
        self.assertEqual(r['count'],r['steps']-1);self.assertGreaterEqual(len(calls),2)
        e.frame=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('recomputed cached animation'))
        reused=prepare_motion(e,start,end,cfg,1000,cache,lambda *_:None)
        self.assertTrue(reused['cached']);self.assertEqual(reused['count'],r['count'])

if __name__=='__main__':unittest.main()
