"""Area probabilities, exact intrusion geometry, and chip-external ownership."""
import json,unittest,math
from pathlib import Path
from dataclasses import replace,asdict
import numpy as np
from scipy.special import ndtr
from shapely.geometry import box,Point,Polygon
from test_v050 import fixture
from die_shift_studio.core import read_gds,build_case,Parameters,uncertainty_bound
from die_shift_studio.contact import ContactModel
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.preview import default_motion,rigid_transform
from die_shift_studio.probability_overlay import probability_maps,_translation_kernel
from die_shift_studio.space_map import space_maps
from die_shift_studio.settings import ViewSettings
from die_shift_studio.project import normalize_project
ROOT=Path(__file__).resolve().parents[1]

class Revision070Tests(unittest.TestCase):
    def test_intrusion_is_geometric_intersection_and_tangent_has_no_area(self):
        *_,m,p=fixture();tangent=m.current([[30,0,0]],p)
        self.assertTrue(tangent['issues']);self.assertEqual(tangent['intrusions'],[])
        q=m.current([[37,0,0]],p);self.assertEqual(len(q['intrusions']),1)
        expected=q['regions'][0]['spacing'].intersection(box(50,-10,70,10))
        self.assertTrue(q['intrusions'][0]['geometry'].equals(expected))
        self.assertAlmostEqual(q['intrusions'][0]['area_um2'],expected.area)
        self.assertEqual({a['scheme'] for a in q['issues']},{1,2,3})
    def test_area_occupancy_matches_analytic_rectangle_probability(self):
        *_,m,p=fixture();p=replace(p,single_bound_x_um=30,single_bound_y_um=30)
        cfg=ViewSettings(probability_method='quadrature',probability_cell_um=.5)
        q=probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=cfg)
        a=q['maps'][0];extent=a['extent'];ix=round(-extent[0]/.5-.5);iy=round(-extent[2]/.5-.5)
        expected=(ndtr(1)-ndtr(-1))**2
        self.assertAlmostEqual(float(a['probability'][iy,ix]),expected,delta=.003)
        self.assertAlmostEqual(a['integrated_area_um2'],100,delta=.05)
        self.assertEqual(q['samples'],0);self.assertLess(q['translation_tail_bound'],3e-15)
        again=probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=replace(cfg,random_seed=42))
        np.testing.assert_array_equal(again['maps'][0]['probability'],a['probability'])
    def test_correlated_and_singular_translation_probabilities(self):
        for rho in (-1,-.85,0,.85,1):
            cov=np.array([[25,25*rho,0],[25*rho,25,0],[0,0,0]])
            kernel=_translation_kernel(cov,1,8,25)
            self.assertAlmostEqual(kernel.sum(),1,places=12)
            n=kernel.shape[0]//2
            if abs(rho)==1:self.assertAlmostEqual(kernel[n,n],ndtr(.1)-ndtr(-.1),places=12)
            if rho==0:self.assertAlmostEqual(kernel[n,n],(ndtr(.1)-ndtr(-.1))**2,places=12)
    def test_sampling_and_deterministic_probability_consistency(self):
        *_,m,p=fixture();p=replace(p,single_bound_x_um=30,single_bound_y_um=30,bound_angle_deg=8)
        arrays=[]
        for mode in ('sobol','monte_carlo','quadrature'):
            maps=probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=ViewSettings(probability_method=mode,probability_cell_um=1,sample_exponent=14))
            q=maps['maps'][0];a=q['probability'];self.assertTrue(np.all((a>=0)&(a<=1)))
            self.assertAlmostEqual(q['integrated_area_um2'],100,delta=1)
            arrays.append(a.max())
        self.assertLess(np.ptp(arrays),.018)
    def test_zero_variance_still_draws_whole_via_and_shape_rotates(self):
        d,r,c,cache,con,m,p=fixture();cfg=ViewSettings(probability_method='quadrature',probability_cell_um=.25)
        q=probability_maps(cache,m.bound,r,p,masks=m.interface_masks,settings=cfg)['maps'][0]
        self.assertGreater((q['probability']>.99).sum(),1000);self.assertAlmostEqual(q['integrated_area_um2'],100,delta=.01)
        # Frozen axes ignore bias; an active rotation about 90 degrees exchanges extents.
        d.layers['10/0'][0]=np.asarray(box(-4,-1,4,1).exterior.coords);c=build_case(d,r);m=ContactModel(d,c,r,c['connections'])
        outputs=[]
        for angle,amplitude in ((0,0),(90,0),(90,.0001)):
            a=probability_maps(m.cache,m.bound,r,replace(p,mean_angle_deg=angle,bound_angle_deg=amplitude),masks=m.interface_masks,settings=cfg)['maps'][0]['probability']
            yy,xx=np.where(a>.5);outputs.append((np.ptp(xx),np.ptp(yy)))
        self.assertEqual(outputs[0],outputs[1]);np.testing.assert_allclose(outputs[0],outputs[2][::-1],atol=1);self.assertGreater(outputs[0][0],outputs[0][1]*3)
    def test_external_vias_every_feature_uses_linked_owner(self):
        project=normalize_project(json.loads((ROOT/'examples/示例D_芯片外扇出.json').read_text()))
        data=read_gds(ROOT/'examples'/project['gds_path']);roles=project['roles'];p=Parameters.from_dict(project['parameters'])
        c=build_case(data,roles,layer_order=project['layer_order']);m=ContactModel(data,c,roles,c['connections'])
        self.assertEqual(c['connections'].ownership_notes,[]);np.testing.assert_array_equal(c['ownership']['4/0'],[0,1])
        for record in m.records:self.assertTrue(all(not die.intersects(record['poly']) for die in c['dies']))
        field=space_maps(m,p)
        for record in m.records:
            q=next(v for v in field['maps'] if v['owner']==record['owner'])
            self.assertLess(record['poly'].difference(q['domain']).area,1e-8);self.assertTrue(q['domain'].covers(c['dies'][record['owner']]))
            self.assertGreater(uncertainty_bound([record['center']],m.bound.centers[record['owner']],p,2)[0],uncertainty_bound([m.bound.centers[record['owner']]],m.bound.centers[record['owner']],p,2)[0])
        states=np.array([[65,0,0],[0,30,.015]])
        engine=PreviewEngine(m.cache,c,roles,{k:default_motion(v) for k,v in roles.items()},p,None,c['connections']);frame=engine.frame(states)
        for i in (0,1):np.testing.assert_allclose(frame['layers']['4/0']['polygons'][i],rigid_transform(data.layers['4/0'][i],m.bound.centers[i],states[i]))
        self.assertTrue(frame['contact']['intrusions']);self.assertEqual(len(frame['contact']['regions']),2)
        probability=probability_maps(m.cache,m.bound,roles,p,masks=m.interface_masks)
        self.assertEqual([a['owner'] for a in probability['maps']],[0,1]);self.assertEqual({a['key'] for a in probability['maps']},{'4/0'})
    def test_settings_validation_and_old_project_defaults(self):
        p=normalize_project(dict(schema_version=4,roles={},parameters={}))
        self.assertEqual(p['settings'],asdict(ViewSettings()));self.assertEqual(p['schema_version'],5)
        for values in ({'sample_exponent':25},{'space_cmap':'unknown'},{'probability_cell_um':float('nan')},{'quadrature_order':5.5}):
            with self.assertRaises(ValueError):ViewSettings.from_dict(values)
    def test_explicit_too_fine_grid_fails_before_large_allocation(self):
        *_,m,p=fixture();p=replace(p,single_bound_x_um=1000,single_bound_y_um=1000)
        with self.assertRaisesRegex(ValueError,'400万格'):
            probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=ViewSettings(probability_method='quadrature',probability_cell_um=.001))
if __name__=='__main__':unittest.main()
