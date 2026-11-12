"""Regression cases: whole-domain joint probability, imports, and state colours."""
import unittest,math
from types import SimpleNamespace
from dataclasses import replace
import numpy as np
from shapely.geometry import box
from scipy.special import ndtr
from die_shift_studio.model_yield import integrate_die,PhysicalMass,model_yield,DieEvent
from die_shift_studio.settings import ViewSettings
from die_shift_studio.core import Parameters
from die_shift_studio.extreme_validation import frame_status


def fixture(two=False):
    via=box(-1,-1,1,1);foreign=box(6,-100,9,100)
    records=[dict(owner=0,poly=via,foreign=[0])]
    if two:records.append(dict(owner=0,poly=box(-1,9,1,11),foreign=[0]))
    return SimpleNamespace(records=records,bound=SimpleNamespace(centers=np.array([[0.,0.]])),
        node_geometry=np.array([foreign],object),data=SimpleNamespace(dbu_um=.001),connections=SimpleNamespace(ownership_notes=[]),configure_parameters=lambda p:None)

class V16Tests(unittest.TestCase):
    def test_all_input_probability_matches_analytic_wall_and_shared_die_event(self):
        p=Parameters(single_bound_x_um=10,single_bound_y_um=0,bound_angle_deg=0,target_nsigma=2,annulus_um=1,line_space_um=1)
        expected=(ndtr(3/5)-ndtr(-2))/(ndtr(2)-ndtr(-2)) # touch starts at X=3
        cfg=ViewSettings(yield_box_budget=2048,yield_tolerance=.0001)
        result=integrate_die(fixture(),p,cfg,0);duplicated=integrate_die(fixture(True),p,cfg,0)
        self.assertLessEqual(result['lower'],expected);self.assertGreaterEqual(result['upper'],expected)
        self.assertLess(result['upper']-result['lower'],.0001)
        self.assertAlmostEqual(result['estimate'],duplicated['estimate'],places=10) # same state, not independent vias

    def test_correlation_and_degenerate_axes_normalize(self):
        for rho in (0,.8,-.8,1,-1):
            p=Parameters(single_bound_x_um=10,single_bound_y_um=20,bound_angle_deg=1,rho_xy=rho,target_nsigma=1)
            m=PhysicalMass(p,1);v,e=m(m.mu-m.b,m.mu+m.b)
            self.assertAlmostEqual(v,1.,places=10);self.assertGreaterEqual(e,0)
        p=Parameters(single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=0)
        m=PhysicalMass(p,1);self.assertAlmostEqual(m(m.mu,m.mu)[0],1.)

    def test_joint_dies_and_binary_model(self):
        m=fixture();m.bound.centers=np.zeros((2,2));m.records.append(dict(owner=1,poly=box(-1,-1,1,1),foreign=[0]))
        p=Parameters(multi_bound_x_um=10,multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=2,annulus_um=1,line_space_um=1)
        result=model_yield(m,p,ViewSettings(yield_method='partition',yield_tolerance=.001))
        self.assertAlmostEqual(result['lower'],result['rows'][0]['lower']**2)
        self.assertAlmostEqual(result['upper'],result['rows'][0]['upper']**2)
        binary=model_yield(m,replace(p,target_nsigma=math.inf),ViewSettings());self.assertEqual(binary['estimate'],1)

    def test_import_roles_are_explicit_even_with_tags(self):
        from die_shift_studio.recognition import recognize
        d=SimpleNamespace(layers={'127/0':[np.array(box(-1000,-1000,1000,1000).exterior.coords)],'128/0':[np.array(box(-500,-500,500,500).exterior.coords)]})
        roles,_=recognize(d,{'127/0':'封装边框','128/0':'芯片边框'})
        self.assertEqual(set(roles.values()),{'不参与'})
        p=Parameters.from_dict(dict(panel_layers=0,wafer_layers=0),require_rdl=False)
        with self.assertRaises(ValueError):p.validate()

    def test_monte_carlo_joint_event_interval_and_vectorized_geometry(self):
        p=Parameters(single_bound_x_um=10,single_bound_y_um=0,bound_angle_deg=0,target_nsigma=2,annulus_um=1,line_space_um=1)
        m=fixture(True);event=DieEvent(m,p,0)
        states=np.array([[-10,0,0],[2.9,0,0],[3,0,0],[7,0,.2]])
        np.testing.assert_array_equal(event.no_contact(states),[event.classify(q,q)[0]==1 for q in states])
        r=model_yield(m,p,ViewSettings(yield_sample_exponent=14,random_seed=510))
        expected=(ndtr(.6)-ndtr(-2))/(ndtr(2)-ndtr(-2))
        self.assertLess(r['lower'],expected);self.assertGreater(r['upper'],expected)
        self.assertLess(r['upper']-r['lower'],.03)
        self.assertEqual(r,model_yield(m,p,ViewSettings(yield_sample_exponent=14,random_seed=510)))

    def test_missing_interfaces_do_not_fabricate_yield(self):
        m=fixture();m.records=[]
        for method in ('monte_carlo','partition'):
            r=model_yield(m,Parameters(),ViewSettings(yield_method=method))
            self.assertIsNone(r['estimate']);self.assertFalse(r['available'])
            self.assertEqual((r['lower'],r['upper']),(0,1))


if __name__=='__main__':unittest.main()
