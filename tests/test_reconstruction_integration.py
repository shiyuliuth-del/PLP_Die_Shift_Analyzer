"""Integration contracts for the frame-based two-cut engine, not legacy single cuts."""
from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
import shapely
from shapely.geometry import box, LineString

from die_shift_studio.reconstruction import Reconstructor, normalize_via_refs
from die_shift_studio.reconstruction_overlay import CutDrag, nudge_cut, _rotate_second
from die_shift_studio.settings import ViewSettings
try:
    from test_reconstruction import chain, example, BRIDGE
except ModuleNotFoundError:
    from tests.test_reconstruction import chain, example, BRIDGE


@dataclass(frozen=True)
class RegionSettings(ViewSettings):
    reconstruction_regions: tuple = ()


class ReconstructionIntegrationTests(unittest.TestCase):
    def test_frame_cut_pair_cache_and_quick_parity(self):
        engine, *_ = chain()
        try:
            r=engine.reconstructor; state=np.array([[35.,0.,0.]])
            quick=r.frame(state,quick=True)
            full=r.frame(state)
            self.assertIsNot(quick,full)
            self.assertIs(r.frame(state),full)
            self.assertIn((state.tobytes(),()),r.cache.sizes)
            self.assertNotIn(None,r.cache.sizes)
            p=full['pieces'][0]
            self.assertIsNotNone(p['cut']);self.assertIsNotNone(p['extra_cut'])
            # Known fixture frame boundary and far cut, independently bounded.
            self.assertAlmostEqual(p['cut'].centroid.x,-800.)
            self.assertLess(p['extra_cut'].centroid.x,-800.)
            self.assertGreater(p['bridge_area_um2'],0.)
            for layer,rows in full['replacements'].items():
                for row,other in zip(rows,quick['replacements'][layer]):
                    self.assertTrue(row['geometry'].equals(other['geometry']))
            r.block_cache.max_entries=1
            for offset in (36.,37.,38.):r.frame(np.array([[offset,0.,0.]]),quick=True)
            self.assertLessEqual(len(r.block_cache.memory),1)
            r.close();self.assertEqual(len(r.block_cache.memory),0)
        finally:engine.close()

    def test_first_and_second_edits_stay_independent(self):
        engine,*_=chain()
        try:
            r=engine.reconstructor;state=np.array([[35.,0.,0.]])
            p=r.frame(state)['pieces'][0]
            cfg=nudge_cut(ViewSettings(),p,5.,'cut')
            self.assertTrue(cfg.reconstruction_cuts)
            self.assertTrue(cfg.reconstruction_second_cuts)
            self.assertIsInstance(cfg.reconstruction_cuts[0][2][0],tuple)
            far=cfg.reconstruction_second_cuts
            cfg=nudge_cut(cfg,p,10.,'extra')
            self.assertNotEqual(cfg.reconstruction_second_cuts,far)
            self.assertEqual(len(cfg.reconstruction_cuts),1)
            rotated=_rotate_second(cfg,p,5.)
            self.assertNotEqual(rotated.reconstruction_second_cuts,cfg.reconstruction_second_cuts)
            updated=Reconstructor(r.model,r.p,cfg)
            try:
                q=updated.frame(state)['pieces'][0]
                self.assertTrue(q['manual'])
                self.assertIsNotNone(q['extra_cut'])
            finally:updated.close()
        finally:engine.close()

    def test_reset_second_clears_correct_field_and_preserves_first(self):
        piece=dict(layer='5/0',index=0,vias=[0],via_refs=(('4/0',0),),
                   cut=LineString([(0,0),(0,20)]),extra_cut=LineString([(30,0),(30,20)]))
        cfg=nudge_cut(ViewSettings(),piece,4.,'cut')
        cfg=nudge_cut(cfg,piece,8.,'extra')
        window=SimpleNamespace(view_settings=cfg,reconstruction_overlay=SimpleNamespace(),
                               request_preview=Mock(),statusBar=lambda:SimpleNamespace(showMessage=Mock()))
        drag=CutDrag.__new__(CutDrag);drag.w=window
        drag.clear(('5/0',0,piece['via_refs']),piece,'extra')
        self.assertEqual(window.view_settings.reconstruction_second_cuts,())
        self.assertEqual(window.view_settings.reconstruction_cuts,cfg.reconstruction_cuts)
        self.assertTrue(window.dirty)

    def test_fixed_via_stop_clearance_bounds_second_cut(self):
        engine,*_=chain()
        try:
            r=engine.reconstructor
            polygon=box(-200,-30,100,30)
            face=LineString([(0,-30),(0,30)])
            moving=box(0,-30,100,30)
            distances=[]
            for clearance in (10.,20.,40.):
                far=r._second_cut(polygon,face,moving,None,stop=(-100.,0.),stop_clear=clearance)
                self.assertIsNotNone(far)
                distance=far.distance(shapely.Point(-100.,0.))
                self.assertGreaterEqual(distance,clearance)
                self.assertLessEqual(distance,clearance+1.001)
                distances.append(distance)
            self.assertEqual(distances,sorted(distances))
        finally:engine.close()

    def test_invalid_second_width_follows_without_bridge(self):
        engine,*_=chain()
        try:
            r=engine.reconstructor
            configured=Reconstructor(r.model,r.p,replace(r.settings,reconstruction_second_min_width_um=100.))
            try:
                p=configured.frame(np.array([[35.,0.,0.]]))['pieces'][0]
                self.assertIsNone(p['extra_cut'])
                self.assertIsNone(p['cut'])
                self.assertIsNone(p['bridge'])
            finally:configured.close()
        finally:engine.close()

    def test_qualified_refs_never_guess_ambiguous_legacy_layer(self):
        r=Reconstructor.__new__(Reconstructor)
        r.vias_by_target={('5/0',0):[dict(key='4/0',index=0),dict(key='6/0',index=0)]}
        self.assertEqual(r._qualified_refs('5/0',0,(0,)),())
        self.assertEqual(r._qualified_refs('5/0',0,(('4/0',0),)),(('4/0',0),))
        rows=(('5/0',0,(('4/0',0),),0.,1.,20.,0.),)
        self.assertIsNone(r._pinned_second(rows,'5/0',0,(('6/0',0),)))
        self.assertIsNotNone(r._pinned_second(rows,'5/0',0,(('4/0',0),)))
        self.assertEqual(normalize_via_refs([['4/0',0]]),(('4/0',0),))

    def test_regions_stay_priority_landing_and_ambiguous_ownership(self):
        r=Reconstructor.__new__(Reconstructor);r.tol=.001
        polygon=box(0,0,100,20)
        def block(owner,x):
            return dict(owner=owner,movable=box(x,0,x+20,20),
                        cluster=[dict(land=box(x+5,5,x+10,10))])
        blocks=[block(0,0),block(1,80)]
        r.regions=[('5/0',box(30,0,65,20),'move')]
        self.assertEqual(r._apply_regions('5/0',polygon,blocks),(0,1))
        self.assertEqual(sum(q['movable'].area for q in blocks),800.)
        r.regions=[('5/0',box(0,0,40,20),'move'),('5/0',box(0,0,15,20),'stay')]
        r._apply_regions('5/0',polygon,blocks)
        self.assertTrue(blocks[0]['movable'].covers(blocks[0]['cluster'][0]['land']))
        self.assertFalse(blocks[0]['movable'].covers(shapely.Point(2,2)))
        self.assertTrue(blocks[0]['movable'].covers(shapely.Point(30,2)))
        self.assertEqual(blocks[0]['movable'].intersection(blocks[1]['movable']).area,0.)

    def test_region_override_reaches_final_copper(self):
        engine,*_=chain()
        try:
            r=engine.reconstructor
            settings=RegionSettings(reconstruction_regions=(('5/0',(-900.,-30.,-700.,30.),'move'),))
            changed=Reconstructor(r.model,r.p,settings)
            try:
                result=changed.frame(np.array([[35.,0.,0.]]))
                self.assertEqual(result['summary']['regions_used'],1)
                p=result['pieces'][0]
                self.assertTrue(p['region'])
                self.assertTrue(p['movable'].covers(shapely.Point(-850.,0.)))
                self.assertTrue(result['replacements']['5/0'][0]['geometry'].is_valid)
            finally:changed.close()
        finally:engine.close()

    def test_final_clearance_is_exact_even_far_beyond_conflict_radius(self):
        r=Reconstructor.__new__(Reconstructor)
        probe=box(0,0,10,10);other=box(1000,0,1010,10)
        which,distance=r._foreign_gap(probe,shapely.STRtree([other]),[(2,other)])
        self.assertEqual(which,0);self.assertEqual(distance,990.)

    def test_inside_frame_whole_follow_has_no_invented_cut(self):
        engine,*_=example(BRIDGE)
        try:
            r=engine.reconstructor;state=np.zeros((len(r.model.bound.centers),3));state[0,0]=35.
            result=r.frame(state)
            self.assertIn('自动划分',result['scope']);self.assertIn('不代表产线',result['scope'])
            for piece in result['pieces']:
                self.assertIsNone(piece['cut']);self.assertIsNone(piece['extra_cut'])
                self.assertTrue(piece['moved'].is_valid)
        finally:engine.close()


if __name__=='__main__':unittest.main()
