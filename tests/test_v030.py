"""Regression evidence for statistical domains, location diagnostics and state flags."""
import json,math,unittest
from dataclasses import replace
from pathlib import Path
import numpy as np
from scipy.stats import norm
from shapely.geometry import Polygon
from die_shift_studio.core import Parameters,read_gds,build_case,evaluate,uncertainty_bound
from die_shift_studio.statistics import ExtremeCatalog,statistical_state
from die_shift_studio.preview import default_motion,transformed_layers
from die_shift_studio.state_check import check_state
from die_shift_studio.formula_render import MathDocument

ROOT=Path(__file__).resolve().parents[1]
class RevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f=ROOT/'examples/示例C_多芯片晶圆接口.json';cls.project=json.loads(f.read_text())
        cls.data=read_gds(f.parent/cls.project['gds_path']);cls.roles=cls.data.auto_roles;cls.data.layers={k:cls.data.layers[k] for k in cls.project['layer_order']}
        cls.case=build_case(cls.data,cls.roles);cls.params=Parameters.from_dict(cls.project['parameters'])
    def test_integer_sigma_and_twelve_no_tail_cancellation(self):
        for target in (1,3,6,12):
            p=replace(self.params,target_nsigma=target);p.validate();mu,cov,k=statistical_state(p,256)
            self.assertTrue(math.isfinite(k));self.assertGreater(k,0)
        for value in (-1,.1,5.5,12.001):
            with self.assertRaises(ValueError):replace(self.params,target_nsigma=value).validate()
        _,c6,k6=statistical_state(replace(self.params,target_nsigma=6),2)
        _,c12,k12=statistical_state(replace(self.params,target_nsigma=12),2)
        np.testing.assert_array_equal(c6,4*c12);self.assertGreater(k12,k6)
        self.assertGreater(2*norm.sf(12),0)
    def test_all_three_die_combinations_and_mixed_extrema(self):
        cat=ExtremeCatalog({'dies':[None]*3},self.params)
        self.assertEqual(cat.total,19682)
        seen=set()
        for i in range(cat.total):
            e=cat[i];seen.add(tuple(e['choices']))
        self.assertEqual(len(seen),27**3-1)
        self.assertNotIn((0,0,0),seen)
        for a in (1,13,26):
            for b in (1,13,26):
                for c in (1,13,26):self.assertIn((a,b,c),seen)
        self.assertTrue(np.all(cat[cat.total-1]['states']!=0))
    def test_256_dies_last_page_without_enumeration(self):
        cat=ExtremeCatalog({'dies':[None]*256},self.params)
        self.assertEqual(cat.total,27**256-1)
        last=cat.page((cat.total-1)//64)
        self.assertLessEqual(len(last),64);self.assertEqual(last[-1][0],cat.total-1)
        np.testing.assert_allclose(last[-1][1]['states'],np.tile(cat.local_states[-1],(256,1)))
    def test_input_box_mixed_states_are_directional_maximum(self):
        p=replace(self.params,rho_xy=-.6);cat=ExtremeCatalog(self.case,p)
        from die_shift_studio.statistics import mean_and_bounds
        mu,bounds=mean_and_bounds(p,2);rng=np.random.default_rng(45)
        sampled=mu+rng.uniform(-1,1,size=(5000,3))*bounds
        for signs in [np.array([1,1,1]),np.array([-1,0,1])]:
            c=signs/bounds;theoretical=float(c@mu+np.abs(c)@bounds)
            self.assertAlmostEqual(float(np.max(cat.local_states@c)),theoretical)
            self.assertTrue(np.all(sampled@c<=theoretical+1e-9))
    def test_capture_shortage_is_separate_from_foreign_network_contact(self):
        p=replace(self.params,transition_length_um=.1)
        result=evaluate(self.data,self.roles,p)
        baseline=evaluate(self.data,self.roles,self.params)
        self.assertEqual(result['schemes'][0]['engineering_gamma'],baseline['schemes'][0]['engineering_gamma'])
        self.assertFalse(any(q['kind']=='动态补偿窗口' for q in result['issues']))
        self.assertTrue(all(q['source_net']!=q['foreign_net'] for q in result['issues']))
        self.assertEqual(result['schemes'][0]['status'],'窗口不足') # this fixture also has actual foreign-net risk
    def state(self,p,states,scheme=1):
        r=evaluate(self.data,self.roles,p)
        modes={k:default_motion(v) for k,v in self.roles.items()}
        layers=transformed_layers(self.data,self.case,modes,states)
        return check_state(self.data,self.case,self.roles,p,r,states,layers,scheme)
    def test_binary_is_closed_boundary_boolean_not_probability(self):
        from test_v050 import fixture
        *_,model,p=fixture()
        self.assertEqual(model.current([[29.999,0,0]],p)['problem_percent'],0)
        self.assertEqual(model.current([[30.,0,0]],p)['problem_percent'],100)
        self.assertEqual(model.current([[30.,0,0]],replace(p,target_nsigma=12))['problem_percent'],100)
    def test_touching_die_gap_is_a_problem(self):
        p=replace(self.params,transition_length_um=5000)
        nominal_gap=self.case['dies'][0].distance(self.case['dies'][1]);shift=nominal_gap-p.min_die_gap_um
        result=self.state(p,np.array([[shift,0,0],[0,0,0]]))
        self.assertEqual(result['problem_percent'],100)
        self.assertTrue(any('芯片净距' in q['kind'] for q in result['failures']))
    def test_cost_fields_only_ignored_for_old_project_migration(self):
        p=Parameters.from_dict(dict(cost_s1=12,yield_s2=.9));self.assertFalse(hasattr(p,'cost_s1'))
        self.assertFalse(any('cost' in k or 'yield' in k for k in evaluate(self.data,self.roles,p)['schemes'][0]))
    def test_distinct_fraction_images(self):
        m=MathDocument();m.formula(r'\sigma_x=\frac{B_x}{k_{\mathrm{in}}}');m.formula(r'\Gamma_1=\frac{Q}{h}')
        self.assertEqual(len(m.resources),2)
        self.assertEqual(len(set(m.resources.values())),2)
        self.assertTrue(all(k.startswith('math:///') for k in m.resources))
if __name__=='__main__':unittest.main()
