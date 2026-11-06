"""Keepout-sided channels, actual copper intrusion and two joint yield policies."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box
from die_shift_studio.core import GDSData,Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import WidthAnalyzer,Wall,capacity,ADEQUATE,NARROW,BLOCKED
from die_shift_studio.width_keepout import violations,routing_violations,regions
from die_shift_studio.width_yield_geometry import WidthEvents
from die_shift_studio.width_yield import measure_width_yield,policy_yields,packed_events
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.extreme_filter import ProblemIndex


def fixture(extra_copper=False,extra_keepouts=()):
    shapes={'9/0':[box(-500,-500,500,500)],'8/0':[box(-400,-400,400,400)],
            '4/0':[box(-10,-10,10,10),box(55,165,65,175)],
            '5/0':[box(-25,-25,25,25),box(50,-100,70,180)],
            '10/0':[box(100,-80,120,80),*extra_keepouts]}
    roles={'9/0':'封装边框','8/0':'芯片边框','4/0':'运动过孔','5/0':'固定RDL','10/0':'禁布区'}
    if extra_copper:
        # Actual moving copper starts tangent to the opposite keepout boundary.
        shapes['3/0']=[box(120,-40,140,40)];roles['3/0']='运动RDL'
    data=GDSData('keepout.gds','v34keepout','TOP',['TOP'],.001,
                 {k:[np.array(g.exterior.coords) for g in gs] for k,gs in shapes.items()},roles)
    case=build_case(data,roles,layer_order=['5/0','10/0','4/0','3/0','8/0','9/0'])
    p=replace(Parameters(),single_bound_x_um=50,single_bound_y_um=0,bound_angle_deg=0,target_nsigma=2)
    e=PreviewEngine(GeometryCache(data),case,roles,{},p,None,case['connections'])
    return e


class KeepoutTests(unittest.TestCase):
    def test_one_or_two_zero_margin_walls_and_original_thickness(self):
        e=fixture();a=WidthAnalyzer(e.contact,e.p,ViewSettings())
        try:
            metal=Wall('pad','运动过孔孔盘',box(0,0,1,1),())
            keep=Wall('keep','禁布区',box(0,0,1,1),())
            self.assertEqual(capacity(30,15,15,(15,0)),1)
            self.assertEqual(capacity(15,15,15,(0,0)),1)
            self.assertEqual(capacity(44.99,15,15,(15,15)),0)
            for gap,status in ((35,ADEQUATE),(30,NARROW),(29.99,BLOCKED)):
                row=a._row('5',{'keys':('5/0',)},metal,keep,75,gap,np.zeros(2),np.ones(2),(20.,),(('5/0',1),),0,False,False)
                self.assertEqual(row['status'],status)
                self.assertEqual(row['wall_margins_um'],(15,0))
                self.assertEqual((row['keep_um'],row['minimum_um']),(35,30))
            row=a._row('5',{'keys':('5/0',)},keep,keep,30,20,np.zeros(2),np.ones(2),(20.,),(),0,False,False)
            self.assertEqual((row['status'],row['keep_um'],row['minimum_um']),(ADEQUATE,20,15))
        finally:a.close();e.close()

    def test_actual_pad_to_keepout_channel_three_classes_and_fixed_obstacle(self):
        e=fixture();e.configure_width(True,ViewSettings())
        try:
            before=None
            for dx,status in ((0,ADEQUATE),(40,ADEQUATE),(41,NARROW),(45,NARROW),(46,BLOCKED)):
                frame=e.frame(np.array([[dx,0.,0.]]))
                data=frame['width_analysis']
                rows=[r for r in data['rows'] if r['pair']==('运动过孔孔盘 4/0 #1','禁布区 10/0 #1')]
                self.assertTrue(rows,(dx,data['rows']))
                for row in rows:
                    self.assertEqual(row['status'],status,(dx,row))
                    self.assertAlmostEqual(row['gap_um'],75-dx)
                    self.assertEqual((row['required'],row['original'],row['refs']),(1,2,(('5/0',1),)))
                    self.assertEqual(row['wall_margins_um'],(15.,0.))
                wall=next(o for o in data['retained'] if o['kind']=='禁布区')
                if before is None:before=wall['geometry']
                self.assertTrue(before.equals(wall['geometry']))
            # Non-metal keepout never connects the nominal electrical graph.
            self.assertNotIn(('10/0',0),e.contact.ids)
        finally:e.close()

    def test_tangent_allowed_but_interior_and_replacements_fail(self):
        e=fixture(True)
        try:
            z=np.zeros((1,3))
            self.assertFalse(violations(e.contact,z))
            z[0,0]=-1
            row=next(q for q in violations(e.contact,z) if q['layer']=='3/0')
            self.assertAlmostEqual(row['intrusion_area_um2'],80)
            self.assertEqual(row['required_um'],0)
            # Source geometry can be safe while a displayed reconstructed piece enters.
            change={'5/0':[dict(indices=(1,),geometry=box(119,0,130,10))]}
            row=violations(e.contact,np.zeros((1,3)),change)[0]
            self.assertEqual((row['layer'],row['indices'],row['intrusion_area_um2']),('5/0',(1,),10.))
            change['5/0'][0]['geometry']=box(120,0,130,10)
            self.assertFalse(violations(e.contact,np.zeros((1,3)),change))
            # DRC tolerance must never expand/shrink a keepout.
            for tol in (-2,0,2):
                q=routing_violations(e.contact,z,e.p,replace(ViewSettings(),drc_tolerance_um=tol))
                self.assertEqual(next(v for v in q if v['kind']=='禁布区侵入')['intrusion_area_um2'],80.)
        finally:e.close()

    def test_tangent_is_consistent_in_geometry_and_deformation_checks(self):
        from types import SimpleNamespace
        from die_shift_studio.state_check import check_state
        from die_shift_studio.deformation import DeformationPreview
        e=fixture(True)
        try:
            for dx,expected in ((0,False),(-1,True)):
                z=np.array([[dx,0.,0.]])
                frame=e.frame(z)
                fast=frame['checks'][1]
                slow=check_state(e.contact.data,e.contact.case,e.roles,e.p,None,z,
                                 {k:v['polygons'] for k,v in frame['layers'].items()},1)
                for q in (fast,slow):
                    self.assertEqual(any('侵入禁布区' in r['kind'] for r in q['failures']),expected)
            subject=SimpleNamespace(model=e.contact,p=e.p,reference_components={})
            c=SimpleNamespace(key='5/0',indices=(1,),poly=box(120,0,130,10),net=-999)
            for x,expected in ((120,False),(119,True)):
                bad=DeformationPreview.candidate_violations(subject,c,box(x,0,130,10),[],[],
                                                            np.zeros((1,3)),[],None,cheap=True)
                self.assertEqual(any(q['kind']=='禁布区侵入' for q in bad),expected)
        finally:e.close()

    def test_merge_overlapping_regions_preserves_holes_and_source_refs(self):
        e=fixture(extra_keepouts=(box(110,-80,130,80),))
        try:
            r=regions(e.contact)
            self.assertEqual(len(r),1);self.assertAlmostEqual(r[0][0].area,30*160)
            self.assertEqual(set(r[0][1]),{('10/0',0),('10/0',1)})
        finally:e.close()

    def test_touching_routes_and_third_keepout_boundary_still_count(self):
        from die_shift_studio.width_analysis import _crossings
        from die_shift_studio.neighbor_channels import occupied
        from die_shift_studio.width_summary import OccupancyBank
        from types import SimpleNamespace
        route=box(0,-40,20,40)
        plane=dict(routes=np.array([route],object),tree=shapely.STRtree([route]),
                   boundaries=np.array([route.boundary],object),route_sources=[[ (('5/0',0),route)]],width=15.,space=15.)
        ends=np.array([[[0.,0.],[20.,0.]]])
        answer=_crossings(ends,plane,1e-9,[[True,True]])
        self.assertEqual(answer[0][0][1:],(20.,(('5/0',0),)))
        self.assertFalse(_crossings(ends,plane,1e-9)[0])
        # A third keepout sharing only the top edge of the test rectangle does
        # not consume its interior. Both scalar and all-extremes paths agree.
        for bottom,expected in ((10,False),(9,True)):
            wall=Wall('keep','禁布区',box(2,bottom,18,20),())
            walls=[Wall('left','固定障碍',box(-3,-10,0,10),()),Wall('right','固定障碍',box(20,-10,23,10),()),wall]
            shapes=np.array([w.geometry for w in walls],object)
            blocked=occupied(ends,np.array([[0,1]]),walls,shapes,10,1e-9)
            self.assertEqual(bool(blocked),expected)
            bank=OccupancyBank(None,SimpleNamespace(),-1,[wall])
            self.assertEqual(next(iter(bank.masks(ends,10,1e-9,(0,)))),int(expected))

    def test_keepout_hole_is_free_and_exact_common_edge_is_allowed(self):
        from types import SimpleNamespace
        ring=box(-40,-40,40,40).difference(box(-20,-20,20,20))
        def model(poly):
            return SimpleNamespace(roles={'10/0':'禁布区','5/0':'固定RDL'},
                cache=SimpleNamespace(layers={'10/0':{'geometry':[ring]},'5/0':{'geometry':[poly]}}),
                ids={('5/0',0):0},nodes=[{'owner':-1,'net':0}])
        free=model(box(-20,-20,20,20))
        self.assertEqual(len(regions(free)[0][0].interiors),1)
        self.assertFalse(violations(free,np.zeros((1,3))))
        bad=model(box(-20,-20,21,20))
        self.assertAlmostEqual(violations(bad,np.zeros((1,3)))[0]['intrusion_area_um2'],40.)

    def test_hidden_keepout_sources_do_not_leak_through_merged_overlays(self):
        from types import SimpleNamespace
        from die_shift_studio.width_overlay import visible_keepout_walls,visible_keepout_intrusion
        a,b=box(0,0,20,40),box(0,30,20,60)
        cache=SimpleNamespace(layers={'10/0':{'geometry':[a]},'11/0':{'geometry':[b]}})
        row=dict(kind='禁布区侵入',boundary_refs=(('10/0',0),('11/0',0)),geometry=box(5,5,15,55))
        shown=visible_keepout_intrusion(row,{'10/0'},cache)
        self.assertTrue(shown['geometry'].equals(box(5,5,15,40)))
        self.assertTrue(row['geometry'].equals(box(5,5,15,55)))  # no numerical mutation
        self.assertIsNone(visible_keepout_intrusion(row,set(),cache))
        gate=dict(ends=('固定障碍','禁布区'),boundary_refs=((),row['boundary_refs']),
                  witness_um=[[-10,50],[0,50]])
        self.assertFalse(visible_keepout_walls(gate,{'10/0'},cache,.001))
        self.assertTrue(visible_keepout_walls(gate,{'11/0'},cache,.001))

    def test_continuous_events_and_all_extreme_summaries_match_preview(self):
        e=fixture(True);cfg=ViewSettings();e.configure_width(True,cfg)
        a=e.width_analyzer;a.probability=None
        try:
            events=WidthEvents(a,e.p,lambda *_:None)
            states=np.array([[[dx,0.,0.]] for dx in (-50,-2,0,20,40,41,46,50)])
            red,yellow=events.classify(states)
            for i,z in enumerate(states):
                expected=[set(),set()]
                full=e.frame(z)['width_analysis']
                for row in full['rows']:
                    if row['status']!=ADEQUATE:expected[row['status']!=BLOCKED].update(row['refs'])
                for row in full['edge_violations']:expected[0].update((row['layer'],j) for j in row['indices'])
                self.assertEqual([red[i],yellow[i]],expected,(i,z,full))
            from die_shift_studio.width_summary import summarize
            p=replace(e.p,single_bound_x_um=35)
            summary=summarize(a,p);g=ExtremeCatalog(e.contact.case,p);index=ProblemIndex(g,summary['red_factors'])
            for j in range(-1,g.total):
                digits=g.choices(j) if j>=0 else [0]
                z=np.array([g.locals[0][digits[0]]]);data=e.frame(z)['width_analysis']
                self.assertEqual(index.matches(digits),bool(data['counts'][BLOCKED] or data['edge_violations']))
            self.assertIn(('3/0',0),summary['lines'])
        finally:e.close()

    def test_two_ordered_joint_yields_and_cached_ignored_reclassification(self):
        r,y=('5/0',1),('5/0',2)
        # Unequal masses: safe .1; red .2; yellow .3; shared red+yellow .4.
        bank={'method':'cubature','total':4,'chunks':(packed_events(
            [set(),{r},set(),{r}],[set(),set(),{y},{y}],np.array([.1,.2,.3,.4])),)}
        np.testing.assert_allclose(policy_yields(bank,[])['values'],[1.,.1])
        np.testing.assert_allclose(policy_yields(bank,[y])['values'],[.3,.1])
        np.testing.assert_allclose(policy_yields(bank,[r,y])['values'],[.1,.1])
        e=fixture(True)
        try:
            cfg=replace(ViewSettings(),width_yield_method='importance',yield_method='partition',yield_box_budget=1024)
            bank=measure_width_yield(e.contact,e.p,cfg,None,lambda *_:None)
            ignored={('3/0',0)}
            q=policy_yields(bank,ignored)
            # The 20 μm trace overlaps a 20 μm keepout iff -40 < DX < 0.
            from scipy.special import ndtr
            loss=(ndtr(0)-ndtr(-40/25))/(ndtr(2)-ndtr(-2))
            self.assertLessEqual(q['intervals'][0][0],1-loss)
            self.assertGreaterEqual(q['intervals'][0][1],1-loss)
            self.assertLessEqual(q['values'][1],q['values'][0])
            with patch.object(WidthEvents,'classify',side_effect=AssertionError('geometry rerun')):
                self.assertEqual(policy_yields(bank,[])['values'][0],1.)
        finally:e.close()


if __name__=='__main__':unittest.main()
