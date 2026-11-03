"""V14: real pivots, full bounded probability and offline desktop execution."""
import json,math,os,shutil,tempfile,unittest
from pathlib import Path
from dataclasses import replace,asdict
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box,Polygon,Point
from test_v060 import traced_fixture
from test_v12 import fixture
from die_shift_studio.core import Parameters,evaluate,uncertainty_bound,rectangle,rectangle_info
from die_shift_studio.pivots import rotation_centers,rotation_center,PIVOT_CHOICES
from die_shift_studio.preview import rigid_transform,transformed_layers
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.statistics import statistical_state
from die_shift_studio.distribution import bounded_poses
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps,coverage_envelope
from die_shift_studio.contact_probability import contact_probability
from die_shift_studio.space_map import space_maps
from die_shift_studio.viewport import ViewportIndex
from die_shift_studio.deformation import DeformationPreview

class V14Tests(unittest.TestCase):
    def test_pivots_centers_corners_custom_and_legacy(self):
        *_,model,p=traced_fixture();case=model.case
        expected={'center':(-900,0),'top_left':(-1300,400),'top_right':(-500,400),'bottom_left':(-1300,-400),'bottom_right':(-500,-400),'custom':(-877,-17)}
        for _,mode in PIVOT_CHOICES:
            q=replace(p,rotation_center=mode,rotation_x_um=23,rotation_y_um=-17)
            np.testing.assert_allclose(rotation_center(case,0,q),expected[mode])
        d=rectangle(123,456,1500,500,23);rotated=dict(dies=[d],infos=[rectangle_info(d)])
        corners=[rotation_center(rotated,0,replace(p,rotation_center=mode)) for _,mode in PIVOT_CHOICES[1:5]]
        self.assertEqual(len({tuple(np.round(v,6)) for v in corners}),4)
        self.assertTrue(all(d.boundary.distance(Point(v))<1e-8 for v in corners))
        np.testing.assert_allclose(rotation_centers(case),rotation_centers(case,Parameters()))
        with self.assertRaises(ValueError):replace(p,die_pivots=[['custom',math.inf,0]]).validate()

    def test_per_die_pivots_follow_connectivity_even_off_die(self):
        d,r,c,cache,m,p=traced_fixture()
        p=replace(p,per_die_enabled=True,die_bounds=[[35,25,1],[10,20,2]],die_pivots=[['bottom_left',0,0],['custom',100,-50]])
        e=PreviewEngine(cache,c,r,{},p,{},c['connections']);z=np.array([[3,4,.13],[-1,2,-.08]])
        f=e.frame(z);origins=rotation_centers(c,p)
        for i,owner in enumerate(c['ownership']['40/0']):
            np.testing.assert_allclose(f['layers']['40/0']['polygons'][i],rigid_transform(d.layers['40/0'][i],origins[owner],z[owner]))
            rec=e.contact.by_feature['40/0',i]
            np.testing.assert_allclose(shapely.get_coordinates(e.contact.moved(rec['poly'],owner,z)),rigid_transform(shapely.get_coordinates(rec['poly']),origins[owner],z[owner]))
            view=ViewportIndex(cache,c,{},p);center=f['layers']['40/0']['centers'][i]
            self.assertIn(i,view.query('40/0',(*center-1,*center+1),z))
        reference=transformed_layers(d,c,{},z,parameters=p)
        for k in reference:
            if k in f['layers']:np.testing.assert_allclose(reference[k],f['layers'][k]['polygons'])
        result=evaluate(d,r,p,connections=c['connections'])
        np.testing.assert_allclose([v['rotation_origin_um'] for v in result['dies']],origins)
        self.assertEqual(result['dies'][0]['input_bounds'],[35,25,1]);e.close()

    def test_pivot_changes_probability_space_and_adaptation_consistently(self):
        d,r,c,cache,m,p=traced_fixture();p=replace(p,rotation_center='custom',rotation_x_um=800,multi_bound_x_um=0,multi_bound_y_um=0,bound_angle_deg=3)
        m.configure_parameters(p);maps=probability_maps(cache,m.bound,r,p,masks=m.interface_masks,settings=ViewSettings(probability_method='quadrature',quadrature_order=9))
        for q in maps['maps']:
            owner=q['owner'];np.testing.assert_allclose(q['rotation_center'],rotation_center(c,owner,p))
            self.assertLess(abs(q['integrated_area_um2']/q['via_area_um2']-1),.01)
            self.assertLess(q['edge_probability'],1e-10)
        sm=space_maps(m,p);self.assertTrue(all(np.isfinite(q['spacing']).all() for q in sm['maps']))
        origin=rotation_center(c,0,p)
        self.assertEqual(uncertainty_bound([origin],origin,p,2)[0],0)
        d,q,c,cache,p,m=fixture();p=replace(p,rotation_center='bottom_left');z=np.array([[0,0,.002],[0,1,.001]])
        dp=DeformationPreview(m,p,{},ViewSettings());np.testing.assert_allclose(m.bound.centers,rotation_centers(c,p));frame=dp.frame(z,m.current(z,p))
        self.assertEqual(len(frame['interfaces']),len(m.records));self.assertTrue(all(v['verified'] for values in frame['replacements'].values() for v in values));dp.cache.close()

    def test_zero_keeps_distribution_scale_and_uses_all_hard_support(self):
        p=Parameters(target_nsigma=3,rho_xy=.4);zero=replace(p,target_nsigma=0);zero.validate()
        self.assertEqual(zero.reference_nsigma,3);self.assertEqual(zero.failure_threshold,0)
        np.testing.assert_array_equal(statistical_state(p,2)[1],statistical_state(zero,2)[1])
        cfg=ViewSettings(sample_exponent=9)
        np.testing.assert_array_equal(bounded_poses(p,2,cfg),bounded_poses(zero,2,cfg))
        reloaded=Parameters.from_dict(json.loads(json.dumps(asdict(zero))))
        self.assertEqual(reloaded.scale_nsigma,3)
        *_,m,p=traced_fixture();p=replace(p,target_nsigma=0,multi_bound_x_um=70,multi_bound_y_um=0,bound_angle_deg=0)
        value=contact_probability(m,m.records[0],p,2048)
        self.assertEqual(value['threshold'],0);self.assertEqual(value['tail_bound'],0);self.assertGreater(value['lower'],0);self.assertEqual(value['verdict'],'窗口不足')
        far=contact_probability(m,m.records[1],p,2048);self.assertEqual(far['verdict'],'通过')

    def test_zero_never_turns_probability_underflow_into_a_pass(self):
        *_,m,p=traced_fixture();p=replace(p,target_nsigma=0,multi_bound_x_um=70,multi_bound_y_um=0,bound_angle_deg=0)
        # Deliberately erase numerical CDF masses. Geometric positive-volume
        # evidence must still distinguish positive risk from zero risk.
        with patch('die_shift_studio.contact_probability.interval_mass',side_effect=lambda lo,hi:np.zeros_like(lo)):
            result=contact_probability(m,m.records[0],p,2048)
        self.assertEqual(result['verdict'],'窗口不足');self.assertTrue(result['positive_probability_proven'])
        self.assertEqual(result['lower'],0.);self.assertGreater(result['upper'],0.)

    def test_range_envelopes_nested_same_distribution_and_not_clipped(self):
        d,r,c,cache,m,p=traced_fixture();p=replace(p,rotation_center='top_left');m.configure_parameters(p)
        cfg=ViewSettings(sample_exponent=9,probability_show_ranges=True,probability_ranges=((1,'#003344'),(3,'#335577')))
        maps=probability_maps(cache,m.bound,r,p,masks=m.interface_masks,settings=cfg)
        full=probability_maps(cache,m.bound,r,replace(p,target_nsigma=0),masks=m.interface_masks,settings=replace(cfg,probability_show_ranges=False))
        for a,b in zip(maps['maps'],full['maps']):
            np.testing.assert_array_equal(a['probability'],b['probability'])
            origins=a['rotation_center'];poly=a['nominal_polygon'];prev=None
            for level in (1,3,6,12,0):
                envelope=coverage_envelope(poly,origins,p,2,level)
                if prev is not None:self.assertTrue(envelope.buffer(.001).covers(prev))
                prev=envelope
            np.testing.assert_allclose(coverage_envelope(poly,origins,p,2,6).bounds,coverage_envelope(poly,origins,p,2,12).bounds)
            # Entire contour is inside the raster, including footprint at hard corners.
            x0,x1,y0,y1=a['extent'];self.assertTrue(box(x0,y0,x1,y1).covers(prev))

    def test_installed_desktop_backend_matches_module_without_dependency_install(self):
        from die_shift_studio import native_backend as nb
        exe=shutil.which('klayout') or next(iter(nb.executable_candidates()),None)
        if exe is None:self.skipTest('Installed KLayout unavailable on this target')
        shapes=[box(0,0,100,10),Polygon([(0,0),(100,0),(100,100),(0,100)],holes=[[(10,10),(90,10),(90,90),(10,90)]])]
        nb.close()
        with patch.dict(os.environ,{'DIE_SHIFT_NATIVE_BACKEND':'module'}):
            try:nb.backend()
            except Exception:self.skipTest('Python module unavailable for cross-backend comparison')
            expected={(i,op):nb.compute(op,g.wkb,.001,20,box(0,15,100,25).wkb if op=='separation' else None) for i,g in enumerate(shapes) for op in ('normalize','width','notch','space','separation','round')}
        nb.close()
        original=nb.importlib.import_module
        def no_module(name):
            if name=='klayout.db':raise ModuleNotFoundError('simulated offline Python without KLayout')
            return original(name)
        with patch.dict(os.environ,{'DIE_SHIFT_NATIVE_BACKEND':'module'}),patch.object(nb.importlib,'import_module',side_effect=no_module),patch.object(nb,'executable_candidates',return_value=[exe]):
            self.assertEqual(nb.backend().info['backend'],'installed desktop');pid=nb.backend().process.pid
            for (i,op),(g,area) in expected.items():
                actual,aa=nb.compute(op,shapes[i].wkb,.001,20,box(0,15,100,25).wkb if op=='separation' else None)
                self.assertLess(actual.symmetric_difference(g).area,1e-8);self.assertAlmostEqual(aa,area)
            self.assertEqual(nb.backend().process.pid,pid)
        nb.close()
        for name in ('RUN.bat',):
            text=(Path(__file__).resolve().parents[1]/name).read_text()
            self.assertNotIn('pip install',text);self.assertNotIn('-m venv',text)

    def test_executable_path_with_spaces_unicode_and_environment_discovery(self):
        from die_shift_studio.native_backend import executable_candidates
        with tempfile.TemporaryDirectory(prefix='芯片 layout ') as temp:
            p=Path(temp)/'klayout_app.exe';p.touch()
            with patch.dict(os.environ,{'DIE_SHIFT_KLAYOUT':temp}),patch('shutil.which',return_value=None):
                self.assertEqual(executable_candidates()[0],str(p))

if __name__=='__main__':unittest.main()
