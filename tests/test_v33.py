"""Independent event unions, continuous laws, exact geometry parity and saved ignores."""
from dataclasses import replace
import math,os,unittest
from pathlib import Path
import numpy as np
from scipy.stats import binomtest
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings
from die_shift_studio.project import normalize_project,restored_width_ignores
from die_shift_studio.width_yield import joint_batches,policy_yields,packed_events,measure_width_yield
from die_shift_studio.width_yield_geometry import WidthEvents
from die_shift_studio.width_analysis import WidthAnalyzer,ADEQUATE,BLOCKED
from die_shift_studio.edge_clearance import edge_violations
from test_v21 import example

class JointPolicyTests(unittest.TestCase):
    def test_shared_state_union_and_ignored_yellow_is_fatal(self):
        a,b=('5/0',0),('5/0',1)
        bank=dict(method='mc',total=4,
                  chunks=(packed_events([{a},set(),{a},set()],[{b},{b},set(),set()]),))
        np.testing.assert_array_equal(policy_yields(bank,[])['values'],[.25,.5,1.])
        q=policy_yields(bank,[b]);np.testing.assert_array_equal(q['values'],[.25,.25,.5])
        for s,interval in zip((1,1,2),q['intervals']):
            expected=binomtest(s,4).proportion_ci(confidence_level=1-.01/3)
            np.testing.assert_allclose(interval,[expected.low,expected.high],atol=1e-12)
        # Overlapping red and yellow events count once per package.
        bank['chunks']=(packed_events([{a,b},{a},set(),set()],[{a},set(),set(),set()]),)
        np.testing.assert_array_equal(policy_yields(bank,[])['values'],[.5,.5,1.])

    def test_continuous_law_independent_dies_and_correlated_xy(self):
        p=replace(Parameters(),multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=0,target_nsigma=2)
        for mode in ('monte_carlo','partition'):
            cfg=replace(ViewSettings(),yield_method=mode,yield_sample_exponent=13,yield_box_budget=8192)
            for rho in (0.,.7,1.,-1.):
                poses=[];weights=[]
                for z,w,_,_ in joint_batches(replace(p,rho_xy=rho),2,cfg):
                    poses.extend(z);weights.extend(np.ones(len(z)) if w is None else w)
                z=np.array(poses);weights=np.array(weights)
                self.assertTrue(np.all(abs(z[:,:,:2])<=35+1e-12));self.assertTrue(np.all(z[:,:,2]==0))
                proportion=np.average((z[:,0,0]>0)&(z[:,1,0]>0),weights=weights)
                self.assertAlmostEqual(proportion,.25,delta=.02 if mode=='monte_carlo' else .002)
                if abs(rho)==1:np.testing.assert_allclose(z[:,:,1],rho*z[:,:,0],atol=1e-9)
                if rho==.7:
                    from die_shift_studio.model_yield import PhysicalMass
                    law=PhysicalMass(replace(p,rho_xy=rho),2)
                    expected,_=law(np.array([0,0,0]),np.array([35,35,0]))
                    actual=np.average((z[:,0,0]>0)&(z[:,0,1]>0),weights=weights)
                    self.assertAlmostEqual(actual,expected,delta=.02 if mode=='monte_carlo' else .002)

    def test_partial_zero_axes_per_die_and_reference_scale(self):
        p=replace(Parameters(),per_die_enabled=True,die_bounds=[[0,0,0],[17,0,0]],mean_x_um=3,mean_y_um=7,
                  target_nsigma=0,reference_nsigma=3)
        for mode in ('monte_carlo','partition'):
            z=np.concatenate([a for a,*_ in joint_batches(p,2,replace(ViewSettings(),yield_method=mode,yield_sample_exponent=8,yield_box_budget=256))])
            self.assertTrue(np.all(z[:,0]==0));self.assertTrue(np.all(z[:,1,1:]==0))
            self.assertLessEqual(np.max(abs(z[:,1,0]-3)),17)
        zero=replace(p,die_bounds=[[0,0,0],[0,0,0]])
        batch=list(joint_batches(zero,2,ViewSettings()))
        self.assertEqual((len(batch),batch[0][2],batch[0][3]),(1,'exact',1))

    def test_scene_probability_tracks_ignored_lines_without_recalculation(self):
        _,_,_,p,engine=example('示例O_运动网络三线路通道')
        try:
            p=replace(p,target_nsigma=2,multi_bound_x_um=35,multi_bound_y_um=0,bound_angle_deg=0)
            cfg=replace(ViewSettings(),yield_method='partition',yield_box_budget=1024)
            bank=measure_width_yield(engine.contact,p,cfg,None,lambda *_:None)
            refs=set().union(*(set(x[3]) for x in bank['chunks']))
            first=policy_yields(bank,[]);last=policy_yields(bank,refs)
            self.assertLessEqual(first['values'][0],first['values'][1]);self.assertEqual(first['values'][2],1.)
            np.testing.assert_allclose(last['values'],[first['values'][0]]*3)
        finally:engine.close()

class SceneParityTests(unittest.TestCase):
    def test_numeric_events_equal_all_preview_routes_and_edges(self):
        for name in ('示例O_运动网络三线路通道','示例P_单点PATH与相邻孔盘','示例I_旋转中心与全范围概率','示例M_不同芯片底层与Pad随动'):
            data,project,case,p,engine=example(name)
            analyzer=WidthAnalyzer(engine.contact,p,ViewSettings());analyzer.probability=None
            try:
                events=WidthEvents(analyzer,p,lambda *_:None);count=len(case['dies'])
                rng=np.random.default_rng(3305)
                from die_shift_studio.statistics import mean_and_bounds
                mu,bounds=np.array([mean_and_bounds(p.for_die(i),count) for i in range(count)]).transpose(1,0,2)
                z=mu+rng.uniform(-1,1,(21,count,3))*bounds
                z[0]=0;z[1::3,0]=0
                red,yellow=events.classify(z)
                for i,pose in enumerate(z):
                    frame=analyzer.frame({'states':pose},quick=True);expected=[set(),set()]
                    for row in frame['rows']:
                        if row['status']!=ADEQUATE:expected[row['status']!=BLOCKED].update(row['refs'])
                    for row in edge_violations(engine.contact,pose,p,ViewSettings()):
                        expected[0].update((row['layer'],j) for j in row['indices'])
                    self.assertEqual([red[i],yellow[i]],expected,(name,i))
            finally:analyzer.close();engine.close()

    @unittest.skipUnless(os.environ.get('DIE_SHIFT_TEST_GDS') and Path(os.environ['DIE_SHIFT_TEST_GDS']).is_file(),
                         'External GD55 is supplied by the local acceptance run')
    def test_gd55_three_die_joint_events_match_complete_frames(self):
        from die_shift_studio.core import read_gds,build_case
        from die_shift_studio.fast_preview import PreviewEngine,GeometryCache
        from die_shift_studio.stack import Connections
        data=read_gds(Path(os.environ['DIE_SHIFT_TEST_GDS']))
        used=dict(zip(('127/0','128/0','3/0','4/0','5/0','6/0'),
                      ('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
        roles={k:used.get(k,'不参与') for k in data.layers};order=['6/0','5/0','4/0','3/0']
        cache=GeometryCache(data);con=Connections(data,roles,order,cache.geometries)
        case=build_case(data,roles,connections=con);p=Parameters()
        engine=PreviewEngine(cache,case,roles,{},p,None,con)
        analyzer=WidthAnalyzer(engine.contact,p,ViewSettings());analyzer.probability=None
        try:
            events=WidthEvents(analyzer,p,lambda *_:None)
            n=len(case['dies']);self.assertEqual(n,3)
            z=np.random.default_rng(335).uniform(-1,1,(24,n,3))*[35,35,math.radians(1)]
            from itertools import product
            for j,signs in enumerate(product((-1,1),repeat=3)):z[j]=np.array(signs)*[35,35,math.radians(1)]
            z[8]=0;red,yellow=events.classify(z)
            for i,state in enumerate(z):
                expected=[set(),set()]
                for row in analyzer.frame({'states':state},quick=True)['rows']:
                    if row['status']!=ADEQUATE:expected[row['status']!=BLOCKED].update(row['refs'])
                for row in edge_violations(engine.contact,state,p,ViewSettings()):
                    expected[0].update((row['layer'],j) for j in row['indices'])
                self.assertEqual([red[i],yellow[i]],expected,i)
        finally:analyzer.close();engine.close()

    def test_ignored_refs_are_bound_to_the_source_and_old_jobs_default_empty(self):
        data,project,_,_,engine=example('示例O_运动网络三线路通道')
        try:
            self.assertFalse(restored_width_ignores(normalize_project(project),data))
            project['ignored_width_lines']=dict(sha256=data.sha256,top_cell=data.top_name,refs=[['5/0',0]])
            q=normalize_project(project)
            self.assertEqual(restored_width_ignores(q,data),{('5/0',0)})
            q['ignored_width_lines']['sha256']='different'
            self.assertFalse(restored_width_ignores(q,data))
            project['ignored_width_lines']['refs']=[['5/0',True]]
            with self.assertRaises(ValueError):normalize_project(project)
        finally:engine.close()

if __name__=='__main__':unittest.main()
