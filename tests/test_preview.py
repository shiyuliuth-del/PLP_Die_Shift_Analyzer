import json,math,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from die_shift_studio.core import read_gds,build_case,Parameters,evaluate
from die_shift_studio.preview import extreme_cases,statistical_state,layer_owners,transformed_layers,rigid_transform,default_motion
from die_shift_studio.recognition import automatic_parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.resources import resource_profile

ROOT=Path(__file__).resolve().parents[1]
class PreviewTests(unittest.TestCase):
    def setUp(self):
        path=ROOT/'examples/示例C_多芯片晶圆接口.json';self.project=json.loads(path.read_text());self.data=read_gds(path.parent/self.project['gds_path'])
        self.roles=self.project['roles'];self.case=build_case(self.data,self.roles);self.params=Parameters.from_dict(self.project['parameters'])
    def test_auto_semantics_and_parameters(self):
        self.assertEqual(self.roles['20/0'],'固定过孔');self.assertEqual(self.roles.get('90/0','不参与'),'不参与')
        values,_=automatic_parameters(self.data,self.roles)
        self.assertEqual(values['panel_layers'],2);self.assertEqual(values['wafer_layers'],1)
        self.assertGreater(values['via_diameter_um'],319);self.assertLess(values['via_diameter_um'],321) # all actual moving-via geometries, including migrated output vias
    def test_extremes_are_in_input_box_independent_of_correlation(self):
        p=replace(self.params,rho_xy=.7);mu,cov,k=statistical_state(p,2)
        items=extreme_cases(self.case,p);self.assertEqual(items.total,27**2-1)
        for item in items:
            from die_shift_studio.statistics import mean_and_bounds
            _,bounds=mean_and_bounds(p,2)
            self.assertTrue(np.all(np.abs(item['states']-mu)<=bounds+1e-12))
        self.assertEqual(len({tuple(i['choices']) for i in items}),728)
    def test_same_die_vertices_share_exact_transform(self):
        modes={k:default_motion(v) for k,v in self.roles.items()};modes['40/0']='auto'
        owners=layer_owners(self.data,self.case,modes);states=np.array([[35,-20,math.radians(1)],[-12,28,math.radians(-1)]])
        actual=transformed_layers(self.data,self.case,modes,states,owners=owners)
        for key in ('2/0','10/0','31/0','40/0'):
            for j,points in enumerate(self.data.layers[key]):
                owner=owners[key][j]
                if owner<0:
                    np.testing.assert_array_equal(actual[key][j],points);continue
                center=np.array([self.case['infos'][owner]['cx'],self.case['infos'][owner]['cy']])
                np.testing.assert_allclose(actual[key][j],rigid_transform(points,center,states[owner]))
                np.testing.assert_allclose(np.linalg.norm(np.diff(actual[key][j],axis=0),axis=1),np.linalg.norm(np.diff(points,axis=0),axis=1),atol=1e-9)
        for a,b in zip(actual['20/0'],self.data.layers['20/0']):np.testing.assert_array_equal(a,b)
    def test_auxiliary_geometry_cannot_be_assigned_by_xy_or_manual_override(self):
        for mode in ('auto','die:1'):
            owners=layer_owners(self.data,self.case,{'90/0':mode},['90/0'])
            self.assertTrue(np.all(owners['90/0']==-1))
    def test_parallel_and_serial_match(self):
        with patch('die_shift_studio.core.resource_profile',return_value={'workers':1}):a=evaluate(self.data,self.roles,self.params)
        with patch('die_shift_studio.core.resource_profile',return_value={'workers':2}):b=evaluate(self.data,self.roles,self.params)
        self.assertEqual(a['dies'],b['dies']);self.assertEqual(a['schemes'],b['schemes'])
    def test_resources_bounded(self):
        profile=resource_profile();self.assertGreaterEqual(profile['workers'],1);self.assertLessEqual(profile['workers'],12);self.assertLessEqual(profile['workers'],max(1,profile['cpu']-2))
        self.assertLessEqual(profile['workers'],max(1,profile['cpu']-2))
    def test_zero_variance_still_previews_nonzero_mean(self):
        p=replace(self.params,multi_bound_x_um=0,multi_bound_y_um=0,bound_angle_deg=0,mean_x_um=10)
        items=extreme_cases(self.case,p)
        self.assertEqual(len(items),1);self.assertEqual(items[0]['name'],'组合 1')
        np.testing.assert_array_equal(items[0]['states'][:,0],[10,10])
    def test_old_manual_project_is_rejected(self):
        p=dict(self.project,geometry={'dies':[{}]})
        with self.assertRaisesRegex(ValueError,'手动边框'):normalize_project(p)
    def test_legacy_names_migrate(self):
        p=dict(self.project,schema_version=1,roles={'10/0':'芯片原始焊盘','20/0':'BGA接收盘'})
        q=normalize_project(p);self.assertEqual(q['roles']['10/0'],'运动过孔');self.assertEqual(q['roles']['20/0'],'固定过孔')
if __name__=='__main__':unittest.main()
