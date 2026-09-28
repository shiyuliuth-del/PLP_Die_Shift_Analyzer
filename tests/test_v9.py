"""V9 hard input bounds, truncated occupancy, rare-event decisions, and timing."""
import unittest,math
from dataclasses import replace
import numpy as np
from shapely.geometry import Polygon,box
from test_v050 import fixture
from test_v060 import traced_fixture
from die_shift_studio.core import Parameters,evaluate
from die_shift_studio.distribution import bounded_poses,normal_interval,normalizer
from die_shift_studio.settings import ViewSettings
from die_shift_studio.contact_probability import contact_probability
from die_shift_studio.probability_overlay import probability_maps,input_envelope
from die_shift_studio.statistics import mean_and_bounds,statistical_state
from die_shift_studio.preview import rigid_transform
from die_shift_studio.animation import motion_timing

class V9Tests(unittest.TestCase):
    def test_truncated_correlated_sampling_and_determinism(self):
        for method in ('sobol','monte_carlo'):
            cfg=ViewSettings(probability_method=method,sample_exponent=12)
            for rho in (-1,-.8,0,.8,1):
                p=Parameters(target_nsigma=1,rho_xy=rho,mean_x_um=2)
                a=bounded_poses(p,3,cfg);mu,b=mean_and_bounds(p,3)
                self.assertTrue(np.all(abs(a-mu)<=b+1e-12));np.testing.assert_array_equal(a,bounded_poses(p,3,cfg))
                self.assertLess(np.max(abs(np.mean(a-mu,axis=0)[:2])),1.)
                if abs(rho)==1:np.testing.assert_allclose(a[:,1],rho*(a[:,0]-2),atol=1e-12)
    def test_only_target_controls_parent_sigma(self):
        p=Parameters(target_nsigma=3,input_nsigma=12)
        _,cov,_=statistical_state(p,1);self.assertAlmostEqual(cov[0,0],(25/3)**2)
        for bad in (0,5.5,13):
            with self.assertRaises(ValueError):replace(p,target_nsigma=bad).validate()
    def test_probability_bounds_contain_analytic_truncated_event(self):
        *_,m,p=fixture()
        for k in (1,3,6,12):
            for b in (25,35,70):
                q=replace(p,target_nsigma=k,single_bound_x_um=b)
                result=contact_probability(m,m.records[0],q)
                truth=float(normal_interval(30/(b/k),min(k,90/(b/k)))/normal_interval(-k,k)) if b>=30 else 0.
                self.assertLessEqual(result['lower'],truth+1e-14);self.assertGreaterEqual(result['upper']+1e-35,truth)
                self.assertEqual(result['verdict'],'窗口不足' if truth>result['threshold'] else '通过')
    def test_exhausted_integration_does_not_fabricate_failure_or_pass(self):
        *_,m,p=fixture();q=replace(p,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=10)
        value=contact_probability(m,m.records[0],q,budget=1)
        self.assertEqual(value['verdict'],'概率待细化');self.assertEqual(value['lower'],0);self.assertGreater(value['upper'],value['threshold'])
    def test_infinity_is_nominal_binary_and_preview_is_always_binary(self):
        *_,m,p=fixture();p=replace(p,target_nsigma=math.inf,single_bound_x_um=1000,mean_x_um=45)
        self.assertEqual(m.screen(p)['issues'],[]) # nominal unchanged despite mean and max input
        for k in (1,12,math.inf):
            self.assertEqual(m.current([[30,0,0]],replace(p,target_nsigma=k))['problem_percent'],100)
        # A nominally violating fixture must flag all three schemes.
        p=replace(p,annulus_um=46);self.assertEqual({q['scheme'] for q in m.screen(p)['issues']},{1,2,3})
    def test_truncated_area_probability_matches_analytic_and_all_methods(self):
        *_,m,p=fixture();p=replace(p,target_nsigma=1,single_bound_x_um=30,single_bound_y_um=30)
        expected=float((normal_interval(-5/30,5/30)/normal_interval(-1,1))**2)
        for method in ('quadrature','sobol','monte_carlo'):
            cfg=ViewSettings(probability_method=method,sample_exponent=16,probability_cell_um=.5)
            q=probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=cfg)['maps'][0]
            x0,_,y0,_=q['extent'];ix=round(-x0/.5-.5);iy=round(-y0/.5-.5)
            self.assertAlmostEqual(q['probability'][iy,ix],expected,delta=.002)
            self.assertAlmostEqual(q['integrated_area_um2'],100,delta=.2)
    def test_swept_envelope_contains_fanout_extreme_vertices_and_continuous_angles(self):
        *_,m,p=traced_fixture();rng=np.random.default_rng(25);mu,b=mean_and_bounds(p,len(m.bound.centers))
        for record in m.records:
            center=m.bound.centers[record['owner']];env=input_envelope(record['poly'],center,p,len(m.bound.centers))
            for theta in np.r_[np.linspace(mu[2]-b[2],mu[2]+b[2],51),mu[2]]:
                for x,y in ((-1,-1),(-1,1),(1,-1),(1,1)):
                    poly=Polygon(rigid_transform(np.asarray(record['poly'].exterior.coords),center,[mu[0]+x*b[0],mu[1]+y*b[1],theta]))
                    self.assertLess(poly.difference(env).area,1e-7)
    def test_removed_capability_inputs_and_manual_network_do_not_change_evaluation(self):
        d,r,_,_,_,_,p=fixture();base=evaluate(d,r,p)
        modified=evaluate(d,r,replace(p,transition_length_um=.1,max_route_angle_deg=1,residual_budget_um=1000,routing_edge_keepout_um=1000,interdie='present'))
        self.assertEqual(base['schemes'],modified['schemes']);self.assertEqual(modified['parameters']['interdie'],'none')
    def test_animation_distance_speed_fps_and_separate_camera_control(self):
        cfg=ViewSettings();self.assertEqual(cfg.animation_duration_ms,1000);self.assertEqual(cfg.animation_fps,30)
        a,na=motion_timing(cfg,10,10000);b,nb=motion_timing(cfg,1000,10000)
        self.assertLess(a,b);self.assertLessEqual(b,1000);self.assertEqual(nb,math.ceil(b/1000*30))
        self.assertLess(motion_timing(replace(cfg,animation_speed=5),100,10000)[0],motion_timing(cfg,100,10000)[0])
        self.assertEqual(motion_timing(cfg,100,10000),motion_timing(replace(cfg,camera_speed=1),100,10000))
        self.assertGreater(motion_timing(replace(cfg,camera_speed=1),1000,10000,True)[0],motion_timing(cfg,1000,10000,True)[0])

if __name__=='__main__':unittest.main()
