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





    def test_search_controls_validated_and_part_of_cache_keys(self):
        cfg=ViewSettings()
        for k,v in [('deformation_iterations',7),('deformation_route_candidates',40),('deformation_general_bands',False)]:
            changed=ViewSettings.from_dict({k:v});self.assertEqual(cfg.deformation_key(),changed.deformation_key())  # retired controls cannot invalidate current results
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
