"""V8 input-box semantics, zero variance, exact contact and package fields."""
import unittest,math,itertools
from dataclasses import replace,asdict
import numpy as np
from shapely.geometry import Point,box
from test_v050 import fixture
from die_shift_studio.core import Parameters,uncertainty_bound
from die_shift_studio.statistics import mean_and_bounds,statistical_state,ExtremeCatalog
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.contact import support_shape
from die_shift_studio.space_map import space_maps,package_map
from die_shift_studio.settings import ViewSettings
from die_shift_studio.selection import inverse_geometry
from die_shift_studio.preview import rigid_transform

class V8Tests(unittest.TestCase):
    def test_preview_corners_use_input_not_confidence_radius(self):
        *_,m,p=fixture();p=replace(p,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1)
        catalog=RepresentativeExtremes(m.case,p);mu,bounds=mean_and_bounds(p,1)
        expected={tuple(bounds*np.array(signs)) for signs in itertools.product((-1,1),repeat=3)}
        self.assertTrue(expected.issubset({tuple(e['states'][0]) for e in catalog}))
        for target in (1,6,12,math.inf):
            for multiple in (1,6,12,math.inf):
                other=RepresentativeExtremes(m.case,replace(p,target_nsigma=target,input_nsigma=multiple))
                np.testing.assert_array_equal([q['states'] for q in catalog],[q['states'] for q in other])
                self.assertTrue(all(np.all(np.abs(q['states']-mu)<=bounds) for q in other))
        self.assertEqual(max(e['states'][0,1] for e in catalog),35)
    def test_mean_is_offset_and_multidie_uses_35_not_single_25(self):
        p=Parameters(mean_x_um=5,mean_y_um=-2)
        catalog=ExtremeCatalog({'dies':[None]*3},p);mu,bounds=mean_and_bounds(p,3)
        np.testing.assert_allclose(catalog.local_states.min(axis=0),mu-bounds)
        np.testing.assert_allclose(catalog.local_states.max(axis=0),mu+bounds)
        self.assertEqual(catalog.local_states[:,0].max(),40)
    def test_infinite_input_multiplier_is_deterministic_including_rotation_support(self):
        *_,m,p=fixture();p=replace(p,input_nsigma=math.inf,target_nsigma=math.inf,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1,mean_x_um=12)
        parsed=Parameters.from_dict(dict(asdict(p),input_nsigma='Infinity',target_nsigma='无穷大'))
        _,cov,_=statistical_state(parsed,1);np.testing.assert_array_equal(cov,np.zeros((3,3)))
        np.testing.assert_array_equal(uncertainty_bound([[0,0],[500,1000]],[0,0],parsed,1),[0,0])
        support=support_shape(box(-5,-5,5,5),0,np.zeros((1,2)),parsed,1)
        self.assertEqual(support['kind'],'poly');self.assertFalse(support['random'])
        for invalid in (13,-1,5.5):
            with self.assertRaises(ValueError):replace(p,target_nsigma=invalid).validate()
    def test_package_field_base_and_transformed_domain_follow_visibility(self):
        *_,m,p=fixture();p=replace(p,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1)
        field=space_maps(m,p);board=box(-2000,-2000,2000,2000);states=np.array([[200,0,.1]])
        q=package_map(field,board,m.bound.centers,states,{'10/0'},101)
        self.assertTrue(q['active']);self.assertAlmostEqual(q['spacing'][0,0],p.line_space_um)
        self.assertGreater(q['spacing'][50,50],p.line_space_um)
        off=package_map(field,board,m.bound.centers,states,set(),101)
        self.assertFalse(off['active']);self.assertTrue(np.all(off['spacing']==p.line_space_um))
        # Two overlapping moving domains use the max, never alpha-weighted numerical averaging.
        field['maps'].append(dict(field['maps'][0],owner=1,spacing=field['maps'][0]['spacing']+100))
        q=package_map(field,board,np.zeros((2,2)),np.zeros((2,3)),{'10/0'})
        self.assertGreater(q['spacing'][40,40],100)
    def test_package_display_preserves_subcell_fanout_and_empty_clips(self):
        from die_shift_studio.plot_geometry import geometry_path
        domain=box(10,-500,10.1,500)
        field=dict(base_spacing_um=8,maps=[dict(owner=0,layers=['4/0'],domain=domain,
            spacing=np.full((2,2),60.),x_edges=np.linspace(10,10.1,3),y_edges=np.linspace(-500,500,3))])
        q=package_map(field,box(-1000,-1000,1000,1000),np.zeros((1,2)),None,{'4/0'},31)
        self.assertTrue(np.all(q['spacing']==8)) # no grid centre hits this narrow strip
        self.assertTrue(np.any(q['display_spacing']>8));self.assertTrue(q['domain'].equals(domain))
        q=package_map(field,box(-1000,-1000,1000,1000),np.zeros((1,2)),np.array([[5000,0,0]]),{'4/0'},31)
        self.assertTrue(q['domain'].is_empty);self.assertEqual(len(geometry_path(q['domain']).vertices),0)
    def test_inverse_pick_transform_matches_forward_rigid_motion(self):
        point=np.array([1200,2000]);center=np.array([-800,500]);state=np.array([35,-25,.12])
        moved=rigid_transform(point,center,state)
        np.testing.assert_allclose(inverse_geometry(Point(moved),center,state).coords[0],point,atol=1e-10)
    def test_vectorized_contact_matches_direct_distance_and_exact_intersection(self):
        *_,m,p=fixture()
        for dx,dy,angle in [(37,0,0),(32,4,.12),(100,20,-.1),(-20,0,0)]:
            state=np.array([[dx,dy,angle]]);current=m.current(state,p)
            expected=[]
            for r in m.records:
                via=m.moved(r['poly'],r['owner'],state)
                for j in r['foreign']:
                    other=m.nodes[j]['poly']
                    if via.distance(other)<=p.annulus_um+p.line_space_um+1e-9:expected.append((r['key'],r['index'],j))
            self.assertEqual(len(current['issues']),3*len(expected))
            for q in current['intrusions']:
                r=m.by_feature[q['key'],q['index']];other=m.nodes[m.ids[q['target_layer'],q['target_index']]]['poly']
                expected=m.moved(r['poly'],r['owner'],state).buffer(p.annulus_um+p.line_space_um,quad_segs=24).intersection(other)
                self.assertLess(q['geometry'].symmetric_difference(expected).area,1e-8)
    def test_display_settings_do_not_invalidate_numeric_caches(self):
        p=ViewSettings();changed=replace(p,probability_cmap='BuPu',space_scope='package',animation_enabled=True,animation_duration_ms=180,probability_interpolation='bicubic')
        self.assertEqual(p.numeric_key(),changed.numeric_key());self.assertNotEqual(p.numeric_key(),replace(p,sample_exponent=14).numeric_key())
        self.assertEqual(ViewSettings.from_dict(asdict(changed)),changed)
        for values in ({'animation_enabled':1},{'animation_steps':61},{'space_scope':'unknown'},{'probability_cmap':'unknown'}):
            with self.assertRaises(ValueError):ViewSettings.from_dict(values)
if __name__=='__main__':unittest.main()
