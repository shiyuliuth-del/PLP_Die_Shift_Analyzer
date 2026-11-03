"""Physical event certificates, project migration, and pure display controls."""
from dataclasses import replace
from types import SimpleNamespace
import math
import unittest
import numpy as np
from scipy.special import ndtr
from shapely.geometry import box,Polygon
from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.project import normalize_project
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from die_shift_studio.width_analysis import Wall,NARROW
from die_shift_studio.width_overlay import WidthOverlay
from die_shift_studio.width_probability import ChannelProbability
from test_v050 import fixture


def contact_fixture():
    data,roles,_,_,_,_,base=fixture()
    data.layers['40/0'].append(np.asarray(box(-34,-10,-26,10).exterior.coords))
    case=build_case(data,roles);model=ContactModel(data,case,roles,Connections(data,roles),GeometryCache(data))
    return model,replace(base,single_bound_x_um=35,target_nsigma=2)


def analytic_tail(x,k):
    return (ndtr(-x*k/35)-ndtr(-k))/(ndtr(k)-ndtr(-k))


class YieldDisplayTests(unittest.TestCase):
    def test_two_targets_on_one_interface_change_with_yield_without_masking_real_contact(self):
        model,p=contact_fixture();direct=model.current([[34.,0,0]],p)
        self.assertTrue(any(r['target_index']==1 for r in direct['issues']))
        for k,rare in ((2,True),(6,False)):
            result=model.screen(replace(p,target_nsigma=k));locations={r['target_index']:r for r in result['locations']}
            far=locations[1];expected=analytic_tail(30,k)
            self.assertLessEqual(far['lower'],expected+1e-12);self.assertGreaterEqual(far['upper'],expected-1e-12)
            self.assertEqual(far['low'],rare);self.assertFalse(locations[2]['low'])
            self.assertEqual(bool(result['low_issues']),rare)
            if rare:
                witness=far['witness'];hits=model.current([witness['state']],p)['issues']
                self.assertTrue(any(q['target_index']==1 for q in hits))
            self.assertTrue(result['issues']);self.assertEqual(len(direct['issues']),3)
        for level in (0,math.inf):
            out=model.screen(replace(p,target_nsigma=level));self.assertFalse(out.get('low_issues'))

    def test_foreign_copper_inside_a_via_hole_is_not_contact(self):
        from die_shift_studio.contact_probability import contact_probability
        model,p=contact_fixture();source=dict(model.records[0]);source['poly']=box(20,-45,100,45).difference(box(35,-30,85,30))
        foreign=model.ids['40/0',1];p=replace(p,single_bound_x_um=.2,annulus_um=.5,line_space_um=.5)
        outcome=contact_probability(model,source,p,candidates=[foreign]);self.assertEqual(outcome['verdict'],'通过')
        self.assertIsNone(outcome['witness']);self.assertLess(outcome['upper'],outcome['threshold'])

    def test_edge_cull_matches_actual_rigid_geometry_even_after_large_rotations(self):
        from die_shift_studio.edge_clearance import edge_violations
        from die_shift_studio.preview import rigid_transform
        import shapely
        board=box(-100,-70,100,70).difference(box(4,-8,12,7))
        copper=np.array([box(-85,-25,-79,-19),box(18,27,24,34),box(-9,-8,-1,7),box(48,32,55,41)],object)
        center=np.array([[25.,30.]])
        model=SimpleNamespace(case={'board':board},data=SimpleNamespace(dbu_um=.001),bound=SimpleNamespace(centers=center),
          roles={'3/0':'运动RDL'},cache=SimpleNamespace(layers={'3/0':dict(geometry=copper)}),
          nodes=[dict(net=i,owner=0) for i in range(len(copper))],ids={('3/0',i):i for i in range(len(copper))})
        model.moved=lambda poly,owner,states:shapely.set_coordinates(poly,rigid_transform(shapely.get_coordinates(poly),center[owner],states[owner]))
        p=Parameters(wafer_line_space_um=10)
        for z in ((0.,0.,0.),(63.,-22.,.4),(3.,5.,3.7),(0.,0.,-2.1)):
            state=np.array([z]);actual=edge_violations(model,state,p,ViewSettings());expected=set()
            for i,poly in enumerate(copper):
                g=model.moved(poly,0,state)
                if not board.covers(g) or board.boundary.distance(g)<10-1e-9:expected.add(i)
            self.assertEqual({q['indices'][0] for q in actual},expected)

    def test_width_rare_bound_contains_closed_form_and_updates_at_six_sigma(self):
        model,p=contact_fixture();board=box(-1500,-1500,1500,1500)
        left=Wall('L','运动过孔孔盘',box(-5,-5,5,5),(),0);right=Wall('R','固定过孔孔盘',box(65,-5,75,5),())
        row=dict(status=NARROW,locked=0,split=False,unresolved=False,nominal_witness_um=[[5,0],[65,0]],nominal_gap_um=60,minimum_um=25,keep_um=29)
        for k,low in ((2,True),(6,False)):
            risk=ChannelProbability(model,replace(p,target_nsigma=k),board,1.e-8)
            q=risk.classify(row,[left,right]);expected=analytic_tail(31,k)
            self.assertGreaterEqual(q['upper'],expected-1e-12);self.assertEqual(q['low'],low)
            self.assertIs(risk.classify(row,[left,right]),q)
            self.assertIsNone(risk.classify(dict(row,split=True),[left,right]))
            outside=Wall('O','固定障碍',box(1400,0,1600,5),());self.assertIsNone(risk.classify(row,[left,outside]))

    def test_separate_rotating_dies_and_local_package_edge(self):
        p=Parameters(target_nsigma=2,bound_angle_deg=10,multi_bound_x_um=35,multi_bound_y_um=22,rho_xy=-1.)
        model=SimpleNamespace(bound=SimpleNamespace(centers=np.array([[0.,0.],[100.,0.]])))
        risk=ChannelProbability(model,p,box(-1000,-1000,1000,1000),1e-8)
        a=Wall('A','Pad',box(-8,-3,8,3),(),0);b=Wall('B','Pad',box(95,-5,105,5),(),1)
        # Independent samples of the bounded correlated input, using actual box polygons.
        from die_shift_studio.distribution import bounded_poses
        from die_shift_studio.preview import rigid_transform
        import shapely
        zs=[bounded_poses(p,2,ViewSettings(sample_exponent=13,random_seed=seed,probability_method='monte_carlo')) for seed in (27,28)]
        geos=[]
        for wall,states in zip((a,b),zs):
            xy=shapely.get_coordinates(wall.geometry)
            geos.append(shapely.polygons([rigid_transform(xy,model.bound.centers[wall.owner],z) for z in states]))
        event=risk.bound(a,b,np.array([1.,0]),55.,8)
        self.assertLessEqual(np.mean(shapely.distance(*geos)<55),event+.02)
        _,base=contact_fixture();edge=ChannelProbability(SimpleNamespace(bound=SimpleNamespace(centers=np.zeros((1,2)))),base,box(-100,-100,100,100),1.e-8)
        e=edge.edge('3/0',1,box(45,-2,54,2),0,15.)
        self.assertGreaterEqual(e['upper'],analytic_tail(31,2)-1.e-12);self.assertTrue(e['low'])
        self.assertFalse(edge.edge('5/0',0,box(84,0,90,3),-1,15.)['low'])
        concave=ChannelProbability(model,p,Polygon([(0,0),(10,0),(10,10),(7,4),(0,10)]),1.e-8)
        self.assertIsNone(concave.edge('3/0',0,a.geometry,0,15))

    def test_old_pages_and_label_precision_are_display_only(self):
        p={'schema_version':5,'parameters':{},'roles':{}}
        defaults=normalize_project(p);self.assertEqual(defaults['extreme_view'],dict(mode='focus',page=1,page_size=256))
        migrated=normalize_project(dict(p,extreme_view=dict(mode='all',page=9)))
        self.assertEqual(migrated['extreme_view'],dict(mode='all',page=3,page_size=256))
        self.assertEqual(normalize_project(migrated)['extreme_view'],migrated['extreme_view'])
        base=ViewSettings();changed=replace(base,width_label_decimals=0,show_low_width=False,show_low_contact=False)
        self.assertEqual(changed.width_key(),base.width_key());self.assertEqual(changed.numeric_key(),base.numeric_key())
        r=dict(narrowing_um=2.359,original=5,current=3,required=4)
        self.assertTrue(WidthOverlay.label_text(r).endswith('2.36微米'));self.assertTrue(WidthOverlay.label_text(r,0).endswith('2微米'))
        self.assertTrue(WidthOverlay.label_text(dict(r,narrowing_um=2),4).endswith('2.0000微米'))
        for v in (-1,1.5,7):
            with self.assertRaises(ValueError):ViewSettings.from_dict({'width_label_decimals':v})
