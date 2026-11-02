import json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from die_shift_studio.core import read_gds,build_case,evaluate,Parameters,GDSData
from die_shift_studio.stack import Connections,stack_order,resolve_stages
from die_shift_studio.jobs import job_path,list_jobs,atomic_save
from die_shift_studio.fast_preview import GeometryCache,check_frame
from die_shift_studio.preview import default_motion,transformed_layers,layer_owners
from die_shift_studio.state_check import check_state
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.statistics import statistical_state
ROOT=Path(__file__).resolve().parents[1]
class Revision040Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f=ROOT/'examples/示例C_多芯片晶圆接口.json';project=json.loads(f.read_text())
        cls.data=read_gds(f.parent/project['gds_path']);cls.roles=project['roles'];cls.data.layers={k:cls.data.layers[k] for k in project['layer_order']};cls.case=build_case(cls.data,cls.roles)
        cls.p=Parameters.from_dict(project['parameters']);cls.cache=GeometryCache(cls.data);cls.contacts=Connections(cls.data,cls.roles,geometries=cls.cache.geometries)
    def test_atomic_jobs_and_name_safety(self):
        with tempfile.TemporaryDirectory() as folder:
            path=job_path(folder,'工艺A');atomic_save(path,dict(gds_path='a.gds',parameters={},schema_version=3))
            before=path.read_bytes()
            with patch('die_shift_studio.jobs.os.replace',side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):atomic_save(path,{'other':1})
            self.assertEqual(path.read_bytes(),before);self.assertEqual(len(list_jobs(folder)),1)
            for name in ('../test','a/b','CON','', 'x'*81):
                with self.assertRaises(ValueError):job_path(folder,name)
    def test_auto_stage_uses_stack_without_column(self):
        data=GDSData('','','',[],.001,{'10/0':[], '11/0':[], '31/0':[], '40/0':[]})
        basic={'10/0':'运动过孔','11/0':'运动过孔','31/0':'晶圆段RDL','40/0':'板段RDL'}
        r=resolve_stages(data,basic,['10/0','31/0','11/0','40/0'])
        self.assertEqual(r['10/0'],'运动过孔');self.assertEqual(r['11/0'],'运动过孔');self.assertEqual(r['31/0'],'运动RDL');self.assertEqual(r['40/0'],'固定RDL')
        r=resolve_stages(data,basic,['10/0','31/0','11/0','40/0'],{'11/0':'运动过孔'})
        self.assertEqual(r['11/0'],'运动过孔')
    def test_adjacency_never_jumps_an_unconnected_physical_layer(self):
        square=np.array([[0,0],[10,0],[10,10],[0,10.]])
        layers={'1/0':[square*100],'10/0':[square,square+[30,0]],'40/0':[square], '41/0':[square+[30,0]]}
        data=GDSData('','','',[],.001,layers);roles={'1/0':'封装边框','10/0':'运动过孔','40/0':'板段RDL','41/0':'固定过孔'}
        con=Connections(data,roles,['10/0','1/0','40/0','41/0'])
        np.testing.assert_array_equal(con.eligible('10/0'),[True,False]);self.assertFalse(con.eligible('41/0')[0])
        self.assertTrue(all(q['upper']=='10/0' and q['lower']=='40/0' for q in con.links))
    def test_touching_is_not_nominal_area_overlap(self):
        a=np.array([[0,0],[10,0],[10,10],[0,10.]])
        d=GDSData('','','',[],.001,{'10/0':[a],'40/0':[a+[10,0]]})
        self.assertFalse(Connections(d,{'10/0':'运动过孔','40/0':'板段RDL'}).eligible('10/0')[0])
    def test_datatypes_share_plane_even_after_independent_row_drag(self):
        a=np.array([[0,0],[10,0],[10,10],[0,10.]])
        d=GDSData('','','',[],.001,{'10/0':[a],'10/1':[a+[20,0]],'40/0':[a+[20,0]]})
        roles={'10/0':'运动过孔','10/1':'运动过孔','40/0':'板段RDL'}
        c=Connections(d,roles,['10/0','40/0','10/1'])
        self.assertFalse(c.eligible('10/0')[0]);self.assertTrue(c.eligible('10/1')[0])
        self.assertEqual([(x['upper'],x['lower']) for x in c.links],[('10/1','40/0')])
    def test_all_three_schemes_report_only_connected_windows(self):
        p=replace(self.p,target_nsigma=6);r=evaluate(self.data,self.roles,p)
        self.assertEqual({q['scheme'] for q in r['issues']},{1,2,3})
        self.assertTrue(all(q['source_net']!=q['foreign_net'] for q in r['issues']))
        self.assertTrue(all('接触' in q['kind'] for q in r['issues']))
    def test_orphan_raw_pins_are_excluded_from_numeric_and_location_checks(self):
        layers=dict(self.data.layers);layers['10/0']=[self.data.layers['10/0'][0]*.001+[-3100,1100]]
        data=GDSData('','','',[],.001,layers);r=evaluate(data,self.roles,self.p)
        case=build_case(data,self.roles)
        self.assertFalse(case['connections'].eligible('10/0')[0])
        self.assertFalse(any(q['source_layer']=='10/0' for q in r['issues']))
        # Other DIE-local terminal RDL can remain rooted when this low plane
        # is absent over those DIEs; only the actual orphan must be excluded.
        from shapely.geometry import Polygon
        orphan=Polygon(layers['10/0'][0])
        self.assertFalse(any(p.equals(orphan) for group in case['pins'] for p in group))
    def test_vectorized_transform_and_checks_match_reference(self):
        modes={k:default_motion(r) for k,r in self.roles.items()};modes['40/0']='auto'
        z=np.array([[35,-20,.01],[-12,28,-.01]])
        reference=transformed_layers(self.data,self.case,modes,z);bound=self.cache.bind(self.case,modes,list(self.data.layers));fast=bound.transform(z,workers=2)
        for key in reference:
            for a,b in zip(reference[key],fast[key]['polygons']):np.testing.assert_allclose(a,b,atol=1e-9)
        r=evaluate(self.data,self.roles,self.p)
        def normalized(q):return sorted((x['kind'],tuple(np.round(x['point'],7))) for x in q['failures'])
        for scheme in (1,2,3):
            old=check_state(self.data,self.case,self.roles,self.p,r,z,reference,scheme)
            new=check_frame(bound,self.roles,self.p,r,z,fast,scheme)
            self.assertEqual(old['problem_percent'],new['problem_percent']);self.assertEqual(normalized(old),normalized(new))
    def test_compact_extremes_cover_all_dies_with_valid_joint_states(self):
        p=replace(self.p,rho_xy=.4);items=RepresentativeExtremes(self.case,p);self.assertLessEqual(items.total,items.full.total);self.assertGreaterEqual(items.total,52)
        mu,cov,k=statistical_state(p,2)
        for e in items:
            from die_shift_studio.statistics import mean_and_bounds
            _,bounds=mean_and_bounds(p,2)
            self.assertTrue(np.all(np.abs(e['states']-mu)<=bounds+1e-12))
        joint=[e for e in items if '最不利' in e['name']]
        # Support directions coinciding with an existing corner are deduplicated.
        self.assertTrue(any(np.all(np.linalg.norm(e['states'],axis=1)>0) for e in items))
        self.assertTrue(all(len({tuple(e['states'][i]) for e in items})==27 for i in range(2)))
if __name__=='__main__':unittest.main()
