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
from die_shift_studio.drc import GlobalDRC,separation_region
from die_shift_studio.native_backend import geometry

class V17Tests(unittest.TestCase):
    def test_defaults_and_tolerance_validation(self):
        p=Parameters();cfg=ViewSettings()
        self.assertEqual((p.line_width_um,p.line_space_um,p.wafer_line_width_um,p.wafer_line_space_um,p.annulus_um),(15,)*5)
        self.assertTrue(cfg.animation_enabled);self.assertEqual(effective_rules(p,cfg),(15,15))
        for tolerance,rule in ((1,14),(-1,16)):
            q=replace(cfg,drc_tolerance_um=tolerance);self.assertEqual(effective_rules(p,q),(rule,rule));self.assertNotEqual(cfg.reconstruction_key(),q.reconstruction_key())
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




    def test_local_foreign_clipping_preserves_actual_drc(self):
        a=box(0,0,100,20);b=shapely.segmentize(box(-10000,30,10000,50),5)
        before=geometry('separation',a,.001,15,b);after=separation_region(a,b,15,.001)
        self.assertLess(before.symmetric_difference(after).area,1e-7)



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
