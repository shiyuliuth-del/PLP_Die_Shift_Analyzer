"""V28 full-grid filtering, risk certificates and the binary geometry path."""
import math
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from shapely.geometry import box
from die_shift_studio.core import Parameters,uncertainty_bound
from die_shift_studio.extreme_filter import ProblemIndex,PoseClause,FilteredCatalog
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.width_summary import summarize,strongest
from die_shift_studio.width_analysis import NARROW,BLOCKED,ADEQUATE,Wall
from die_shift_studio.width_probability import ChannelProbability
from die_shift_studio.project import normalize_project
from tests.test_v21 import example
from tests.test_v16 import fixture
from die_shift_studio.model_yield import model_yield


class V28(unittest.TestCase):
    def test_problem_index_matches_every_full_index_with_sparse_large_catalog(self):
        g=ExtremeCatalog({'dies':[0]*4},Parameters(per_die_enabled=True,die_bounds=[[1,1,0],[1,0,0],[0,0,0],[1,1,0]]))
        rules=[PoseClause(((0,(2,3)),(3,(4,6)))),((1,),[True,False,False])]
        ix=ProblemIndex(g,rules)
        expected=[i for i in range(g.total) if (lambda d:d[0] in (2,3) and d[3] in (4,6) or d[1]==0)(g.choices(i))]
        self.assertEqual([ix.at(j) for j in range(ix.total)],expected)
        focus=SimpleNamespace(indices=list(range(0,g.total,3)),names={i:str(i) for i in range(0,g.total,3)})
        self.assertEqual([i for i,q in FilteredCatalog(focus,ix).page(0,g.total)],[i for i in expected if i%3==0])
        many=ExtremeCatalog({'dies':[0]*16},Parameters())
        sparse=ProblemIndex(many,[PoseClause(((0,(1,)),(15,(2,))))])
        self.assertEqual(sparse.total,27**14)
        self.assertEqual(many.choices(sparse.at(sparse.total-1)),[1]+[26]*14+[2])

    def test_all_poses_against_full_channel_frames(self):
        data,job,case,p,old=example('示例O_运动网络三线路通道')
        old.close()
        for k in (0,2,math.inf):
            p=replace(p,target_nsigma=k,per_die_enabled=True,die_bounds=[[35,0,.7],[0,10,0]])
            e=PreviewEngine(old.contact.cache,case,job['roles'],{},p,None,case['connections'])
            try:
                e.configure_width(True,ViewSettings());grid=ExtremeCatalog(case,p)
                summary=summarize(e.width_analyzer,p);ix=ProblemIndex(grid,summary['red_factors'])
                expected={};hit=0
                for digits in product(*(range(n) for n in grid.radices)):
                    states=np.array([q[i] for q,i in zip(grid.locals,digits)])
                    w=e.frame(states)['width_analysis']
                    red=bool(w['counts'][BLOCKED] or w['edge_violations'])
                    self.assertEqual(ix.matches(digits),red,(digits,k));hit+=int(red)
                    for row in w['rows']:
                        if row['status']!=ADEQUATE:strongest(expected,row['refs'],row['status'],row.get('event_probability'),k in (0,math.inf))
                    for row in w['edge_violations']:strongest(expected,[(row['layer'],i) for i in row['indices']],BLOCKED,row.get('event_probability'),k in (0,math.inf))
                self.assertGreater(hit,0);self.assertEqual(summary['package_states'],27)
                if k!=2:self.assertEqual(summary['lines'],expected)
                else:
                    for ref,(severity,_) in expected.items():self.assertIn(ref,summary['lines'])
                    for ref,(severity,proof) in summary['lines'].items():
                        if ref not in expected:self.assertEqual(proof,'undetermined')
            finally:e.close()

    def test_coupled_contact_filter_rechecks_changed_geometry(self):
        from die_shift_studio.coupled_filter import scan
        class Contacts:
            def current(self,z,p,replacements=None):
                # The original copper contacts on +X. After adapting it,
                # only the -X case contacts the replacement.
                return {'issues':[1] if (z[0,0]<0 if replacements else z[0,0]>0) else []}
        model=SimpleNamespace(p=Parameters(single_bound_x_um=5,single_bound_y_um=0,bound_angle_deg=0),
            contact=Contacts(),deformer=SimpleNamespace(frame=lambda z,c,progress:{'replacements':{'5/0':True}}))
        grid=ExtremeCatalog({'dies':[0]},model.p)
        q=scan(model,grid,ViewSettings(),False,None,lambda *_:None)
        self.assertEqual([grid[i]['states'][0,0] for i in q['indices']],[-5])
        self.assertEqual(scan(model,grid,ViewSettings(),False,(1,),lambda *_:None)['indices'],[])

    def test_evidence_three_categories_worst_per_source(self):
        f={};r=[('5/0',4)]
        strongest(f,r,BLOCKED,dict(low=True,lower=0,threshold=.01),False);self.assertFalse(f)
        strongest(f,r,NARROW,dict(lower=.4,threshold=.01),False);self.assertEqual(f[r[0]],(NARROW,'exceeded'))
        strongest(f,r,BLOCKED,None,False);self.assertEqual(f[r[0]],(BLOCKED,'undetermined'))
        strongest(f,r,BLOCKED,dict(lower=.02,threshold=.01),False);self.assertEqual(f[r[0]],(BLOCKED,'exceeded'))
        p=Parameters(single_bound_x_um=1,single_bound_y_um=0,bound_angle_deg=0,target_nsigma=6)
        checker=ChannelProbability(fixture(),p,box(-20,-20,20,20),1e-8)
        a=Wall('a','pad',box(-1,-1,1,1),(),0);b=Wall('b','pad',box(2,-1,3,1),())
        row=dict(locked=0,split=False,unresolved=False,nominal_gap_um=1.,status=BLOCKED,minimum_um=3.,nominal_witness_um=[[1,0],[2,0]])
        evidence=checker.classify(row,[a,b]);self.assertGreater(evidence['lower'],evidence['threshold'])
        self.assertLessEqual(evidence['lower'],evidence['upper'])

    def test_infinity_no_distribution_even_with_range_editor_enabled(self):
        p=Parameters(target_nsigma=math.inf)
        nominal=box(-3,-1,3,1).difference(box(-.2,-.2,.2,.2))
        cache=SimpleNamespace(layers={'4/0':dict(geometry=[nominal,box(9,9,10,10)])})
        bound=SimpleNamespace(owners={'4/0':np.array([1,0])},centers=np.array([[2,3],[4,5]]))
        with patch('die_shift_studio.probability_overlay._probability_maps',side_effect=AssertionError),patch('die_shift_studio.model_yield.DieEvent',side_effect=AssertionError):
            q=probability_maps(cache,bound,{'4/0':'运动过孔'},p,masks={'4/0':[True,False]},settings=ViewSettings(probability_show_ranges=True))
            self.assertTrue(q['binary']);self.assertEqual(q['samples'],0);self.assertEqual(len(q['maps']),1)
            self.assertIs(q['maps'][0]['nominal_polygon'],nominal);self.assertEqual(q['maps'][0]['owner'],1)
            result=model_yield(fixture(),p,ViewSettings(),contact={'issues':[{'die':1}]})
            self.assertEqual(result['estimate'],0.);self.assertEqual(result['rows'][0]['samples'],0)
        with patch('die_shift_studio.distribution.normalizer',side_effect=AssertionError):
            np.testing.assert_array_equal(uncertainty_bound([[1,0],[6,2]],[0,0],p,1),[0.,0.])

    def test_closed_form_matches_eigenvalue(self):
        p=Parameters(target_nsigma=6);rng=np.random.default_rng(28);pts=rng.normal(size=(48,2))
        m=rng.normal(size=(3,3));cov=m@m.T
        rows=uncertainty_bound(pts,[.2,.3],p,1,details=True,state=(np.array([2.,-1.,.31]),cov,2.))
        for row in rows:
            j=np.array([[1,0,-row['by']],[0,1,row['bx']]])
            self.assertAlmostEqual(np.linalg.eigvalsh(j@cov@j.T)[-1],row['lambda_max'])

    def test_other_results_do_not_evict_zero_width_summary(self):
        from die_shift_studio.session_cache import ProjectCacheRegistry
        registry=ProjectCacheRegistry();group=registry.get('one',1)
        stub=SimpleNamespace(signature='one',width_key=(True,),cache_group=group,width_analyzer=None,p=None)
        try:
            with patch('die_shift_studio.width_summary.summarize',return_value={'lines':{}}) as solve:
                first=PreviewEngine.width_summary(stub,lambda *_:None)
                for i in range(5):group.values[('ordinary',i)]=i
                self.assertEqual(first,PreviewEngine.width_summary(stub,lambda *_:None));solve.assert_called_once()
                group.clear();PreviewEngine.width_summary(stub,lambda *_:None);self.assertEqual(solve.call_count,2)
        finally:registry.close()

    def test_saved_filter_and_legacy_default(self):
        for value in (True,False):
            old={'schema_version':5,'extreme_view':dict(mode='all',page=2,page_size=256,filter_problems=value)}
            self.assertEqual(normalize_project(old)['extreme_view'],old['extreme_view'])
        self.assertTrue(normalize_project({'schema_version':3})['extreme_view']['filter_problems'])
        with self.assertRaises(ValueError):normalize_project({'schema_version':5,'extreme_view':{'filter_problems':'false'}})
