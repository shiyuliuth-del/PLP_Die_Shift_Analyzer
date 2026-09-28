"""V15 continuous certificates, hand-calculable metrics and shape search."""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import shapely
from shapely.geometry import box,Point,Polygon
from die_shift_studio.core import Parameters,rectangle_info
from die_shift_studio.settings import ViewSettings
from die_shift_studio.safe_ranges import BoxCertificate,solve_die
from die_shift_studio.screening import band_ratio,displacement_bound
from die_shift_studio.route_adaptation import section_candidates,SlabField
from die_shift_studio.deformation import Field
from die_shift_studio.preview import rigid_transform


def model(via,foreign,center=(0,0)):
    return SimpleNamespace(bound=SimpleNamespace(centers=np.array([center],float)),
        data=SimpleNamespace(dbu_um=.001),records=[dict(owner=0,poly=via,foreign=list(range(len(foreign))))],node_geometry=np.array(foreign,object))

class V15Tests(unittest.TestCase):
    def test_certified_asymmetric_box_matches_one_dimensional_wall(self):
        m=model(box(-1,-1,1,1),[box(12,-100,14,100)])
        p=Parameters(single_bound_x_um=20,single_bound_y_um=10,bound_angle_deg=0,annulus_um=1,line_space_um=1)
        r=solve_die(m,p,ViewSettings(safe_box_precision=.001,safe_box_starts=3),0)
        self.assertTrue(r['certified']);self.assertEqual(r['minimum_display'][0],-20)
        self.assertLess(r['maximum_display'][0],9);self.assertGreater(r['maximum_display'][0],8.95)
        self.assertEqual(r['minimum_display'][1],-10);self.assertEqual(r['maximum_display'][1],10)
        self.assertEqual(r['minimum_display'][2],0);self.assertEqual(r['maximum_display'][2],0)
        self.assertEqual(BoxCertificate(m,0,p).check(r['minimum'],r['maximum']),'safe')

    def test_rotational_interior_contact_not_just_corners(self):
        via=Point(100,0).buffer(1);m=model(via,[Point(0,100).buffer(2)])
        p=Parameters(single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=180,annulus_um=1,line_space_um=1)
        c=BoxCertificate(m,0,p)
        self.assertEqual(c.check([0,0,0],[0,0,0]),'safe')
        self.assertEqual(c.check([0,0,math.pi],[0,0,math.pi]),'safe')
        self.assertEqual(c.check([0,0,0],[0,0,math.pi]),'contact')

    def test_touching_mean_does_not_emit_a_fake_zero_box(self):
        m=model(box(-1,-1,1,1),[box(3,-10,4,10)]);p=Parameters(annulus_um=1,line_space_um=1)
        r=solve_die(m,p,ViewSettings(),0)
        self.assertFalse(r['certified']);self.assertIsNone(r['minimum'])

    def test_swept_envelope_contains_all_continuous_support_samples(self):
        m=model(box(90,-3,104,6),[]);p=Parameters(annulus_um=1,line_space_um=1)
        c=BoxCertificate(m,0,p);v=np.asarray(m.records[0]['poly'].exterior.coords);lo=np.array([-7,-4,-.3]);hi=np.array([11,6,.45]);lever=np.linalg.norm(v,axis=1).max()
        outer=c.swept(v,lever,lo,hi)
        for state in np.random.default_rng(15).uniform(lo,hi,(600,3)):
            self.assertTrue(outer.covers(Polygon(rigid_transform(v,[0,0],state))))

    def test_large_rotation_interval_envelope_still_contains_full_turns(self):
        m=model(box(90,-3,104,6),[]);c=BoxCertificate(m,0,Parameters())
        v=np.asarray(m.records[0]['poly'].exterior.coords);lo=np.array([-7,-4,0]);hi=np.array([11,6,600*math.pi])
        outer=c.swept(v,np.linalg.norm(v,axis=1).max(),lo,hi)
        for z in np.random.default_rng(17).uniform(lo,hi,(100,3)):
            self.assertTrue(outer.covers(Polygon(rigid_transform(v,[0,0],z))))

    def test_track_band_hand_calculation_and_fanout(self):
        info=rectangle_info(box(-500,-500,500,500))
        records=[dict(poly=Point(450,y).buffer(5),net=i) for i,y in enumerate(np.linspace(-400,400,10))]
        q=band_ratio(records,info,2,20,20,40)
        self.assertEqual(q['ratio'],.26) # 5*20+4*20+2*40 = 260 over 1000
        duplicate=records+[records[0]]
        self.assertEqual(band_ratio(duplicate,info,2,20,20,40)['ratio'],q['ratio'])
        outside=[dict(poly=Point(1000,y).buffer(5),net=i) for i,y in enumerate(np.linspace(-800,800,10))]
        self.assertLess(band_ratio(outside,info,2,20,20,40,True)['ratio'],q['ratio'])
        self.assertEqual(displacement_bound(box(0,0,300,400),[0,0],Parameters(single_bound_x_um=3,single_bound_y_um=4,bound_angle_deg=0),1),5.)

    def test_exact_cancellation_tightens_local_injectivity_bound(self):
        poly=Point(1000,0).buffer(5);record=dict(owner=0,poly=poly,center=[1000,0])
        angle=.01;state=np.array([[1000*(1-math.cos(angle)),-1000*math.sin(angle),angle]])
        f=Field([record],np.array([[0.,0.]]),state,[10],40,ViewSettings())
        self.assertLess(f.jacobian_bound(),.05)
        np.testing.assert_allclose(f(np.array([[1000.,0.]])),[[1000,0]],atol=1e-10)

    def test_branched_whole_sections_are_continuous_and_keep_fixed_ends(self):
        source=shapely.union_all([box(-20,-60,30,60),box(0,30,240,60),box(0,-60,240,-30)])
        records=[dict(owner=0,poly=Point(0,0).buffer(5),center=[0,0])]
        anchors=[box(225,35,235,45),box(225,-45,235,-35)]
        cfg=ViewSettings(deformation_general_bands=True)
        candidates=list(section_candidates(source,records,anchors,20,.001,cfg));self.assertTrue(candidates)
        field=SlabField(records,np.array([[0.,0.]]),np.array([[3.,4.,.002]]),cfg,*candidates[0])
        pts=shapely.get_coordinates(anchors);np.testing.assert_allclose(field(pts),pts,atol=1e-9)
        for t,w in [(0,1),(1,0)]:
            point=field.start+field.direction*(field.length*t)
            self.assertAlmostEqual(field.weights(point[None,:])[0,0],w)
        self.assertLess(field.jacobian_bound(),.98)

    def test_serial_thread_spawn_have_identical_verified_output_and_cached_repeat(self):
        from test_v12 import fixture
        from die_shift_studio.deformation import DeformationPreview
        from die_shift_studio.resources import resource_profile
        d,q,c,cache,p,m=fixture();states=np.array([[1.,1.,0.],[0.,35.,0.]])
        outputs=[]
        for mode in ('serial','thread','process'):
            dp=DeformationPreview(m,p,{},ViewSettings(deformation_execution=mode,deformation_workers=2))
            try:
                frame=dp.frame(states,m.current(states,p))
                self.assertEqual(frame['accepted_count'],2,frame['diagnostics'])
                self.assertIs(dp.frame(states,m.current(states,p)),frame)
                outputs.append([v['geometry'] for v in frame['replacements']['5/0']])
                if mode=='process' and resource_profile()['process_workers']>1:self.assertEqual(frame['execution'],'process')
            finally:dp.close()
            self.assertIsNone(dp.executor)
        for values in outputs[1:]:
            for a,b in zip(outputs[0],values):self.assertLess(a.symmetric_difference(b).area,1e-7)

    def test_tangent_branch_zero_area_clip_does_not_crash_band_warp(self):
        from shapely.geometry import GeometryCollection,LineString
        from die_shift_studio.deformation import warp_polygon
        copper=box(0,0,100,20);clip=GeometryCollection([copper,LineString([(200,0),(200,20)])])
        result=warp_polygon(clip,lambda xy:xy+np.array([3.,4.]),.05,5.)
        self.assertAlmostEqual(result.area,copper.area)
        self.assertLess(result.symmetric_difference(box(3,4,103,24)).area,1e-9)

    def test_search_controls_validated_and_part_of_cache_keys(self):
        cfg=ViewSettings()
        for k,v in [('deformation_iterations',7),('deformation_route_candidates',40),('deformation_general_bands',False)]:
            changed=ViewSettings.from_dict({k:v});self.assertNotEqual(cfg.deformation_key(),changed.deformation_key())
        self.assertNotEqual(cfg.numeric_key(),replace(cfg,safe_box_starts=6).numeric_key())
        with self.assertRaises(ValueError):ViewSettings.from_dict(dict(safe_box_budget=0))

    def test_combined_chart_uses_each_axis_own_bounds_and_hides_uncertified_ranges(self):
        from die_shift_studio.safe_range_plot import range_rows,draw_safe_ranges
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        row=dict(die=1,input_min=[10,-50,math.radians(5)],input_max=[30,150,math.radians(5)],
                 minimum_display=[15,-10,5],maximum_display=[25,110,5],certified=True,volume_fraction=.3)
        bad=dict(row,die=2,certified=False,status='均值位置已接触；不存在包含均值的安全盒')
        ranges=dict(rows=[row,bad]);items=range_rows(ranges)
        self.assertEqual(items[0]['normalized'],[-50,50]);self.assertEqual(items[1]['normalized'],[-60,60])
        self.assertEqual(items[2]['normalized'],[0,0]);self.assertTrue(items[2]['fixed'])
        self.assertEqual(items[2]['label'],'[5.00000000, 5.00000000]')
        self.assertIsNone(items[3]['minimum']);self.assertEqual(items[3]['label'],'—')
        fig=Figure(figsize=(10,4));FigureCanvasAgg(fig);draw_safe_ranges(fig,ranges)
        self.assertEqual(len(fig.axes),1)
        for line in fig.axes[0].lines:
            if line.get_color() in ('#659bb6','#79a68f','#b08eac'):
                self.assertLess(max(line.get_ydata()),3)

if __name__=='__main__':unittest.main()
