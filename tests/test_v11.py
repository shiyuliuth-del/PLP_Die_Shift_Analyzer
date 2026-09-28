"""V11 regressions: support conservation, independent DIE amplitudes and convergence."""
import unittest,json,math,tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box,Polygon
from die_shift_studio.core import read_gds,build_case,Parameters,evaluate
from die_shift_studio.contact import ContactModel
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.deformation import DeformationPreview,width_ok,angle_regions,baseline_check
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps,_footprint,_deposit
from die_shift_studio.space_map import space_maps
from die_shift_studio.statistics import mean_and_bounds,ExtremeCatalog
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.acceleration import convolve
ROOT=Path(__file__).resolve().parents[1]

def fixture():
    q=json.loads((ROOT/'examples/示例F_逐芯片幅度与迭代窗口.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path'])
    c=build_case(d,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(q['parameters'])
    m=ContactModel(d,c,q['roles'],c['connections'],cache)
    return d,q,c,cache,p,m

class V11Tests(unittest.TestCase):
    def test_individual_bounds_drive_all_geometry_evaluation_and_extremes(self):
        d,q,c,cache,p,m=fixture();p=replace(p,die_bounds=[[7,2,.2],[80,30,1.5]])
        r=evaluate(d,q['roles'],p,connections=c['connections'])
        for i,row in enumerate(r['dies']):self.assertEqual(row['input_bounds'],p.die_bounds[i])
        self.assertLess(r['dies'][0]['corner_bound_um'],r['dies'][1]['corner_bound_um'])
        reps=RepresentativeExtremes(c,p)
        for state in reps:
            for i in range(2):self.assertTrue(np.all(abs(state['states'][i])<=mean_and_bounds(p.for_die(i),2)[1]+1e-12))
        full=ExtremeCatalog(c,p)
        for idx in (0,7,28,full.total-1):
            for i in range(2):self.assertTrue(np.all(abs(full[idx]['states'][i])<=mean_and_bounds(p.for_die(i),2)[1]+1e-12))
        maps=space_maps(m,p)['maps'];self.assertLess(maps[0]['extra'].max(),maps[1]['extra'].max())
    def test_per_die_contact_probabilities_equal_separate_parameter_resolution(self):
        _,_,_,_,p,m=fixture();p=replace(p,die_bounds=[[2,1,0],[100,8,0]],target_nsigma=2)
        both=m.screen(p)
        for q in both['probabilities']:
            separate=m.screen(p.for_die(q['die']-1))
            v=next(v for v in separate['probabilities'] if v['source_index']==q['source_index'])
            self.assertEqual((q['lower'],q['upper']),(v['lower'],v['upper']))
    def test_probability_support_is_k_independent_without_clipping(self):
        from test_v050 import fixture as simple
        d,roles,case,cache,con,m,p=simple();p=replace(p,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1)
        for method in ('sobol','monte_carlo','quadrature'):
            extents=[];cells=[]
            for k in (1,2,6,12):
                cfg=ViewSettings(probability_method=method,sample_exponent=10,quadrature_order=11,acceleration='cpu')
                q=probability_maps(cache,m.bound,roles,replace(p,target_nsigma=k),masks=m.interface_masks,settings=cfg)
                a=q['maps'][0];extents.append(a['extent']);cells.append(q['cell_um'])
                self.assertLess(a['edge_probability'],1e-10);self.assertLess(a['convolution_mass_error'],1e-6)
                self.assertLess(abs(a['integrated_area_um2']/a['via_area_um2']-1),.08)
                self.assertTrue(np.all((a['probability']>=0)&(a['probability']<=1)))
                hull=a['input_envelope'];e=a['extent'];self.assertLess(e[0],hull[:,0].min());self.assertGreater(e[1],hull[:,0].max())
                self.assertLess(e[2],hull[:,1].min());self.assertGreater(e[3],hull[:,1].max())
            np.testing.assert_allclose(cells,cells[0])
    def test_probability_per_die_maps_have_the_correct_individual_extent(self):
        d,q,c,cache,p,m=fixture();p=replace(p,die_bounds=[[3,1,0],[100,30,0]])
        out=probability_maps(cache,m.bound,q['roles'],p,masks=m.interface_masks,settings=ViewSettings(sample_exponent=8))
        maps=sorted(out['maps'],key=lambda v:v['owner']);self.assertEqual(len(maps),2)
        self.assertLess(np.ptp(maps[0]['extent'][:2]),np.ptp(maps[1]['extent'][:2]))
        self.assertEqual(len(out['per_die']),2)
    def test_holes_are_not_filled_in_entity_coverage(self):
        poly=box(-20,-20,20,20).difference(box(-10,-10,10,10));g=_footprint(poly,0,1,4)
        self.assertEqual(g[g.shape[0]//2,g.shape[1]//2],0);self.assertAlmostEqual(g.sum(),poly.area)
    def test_invalid_deposit_is_rejected_instead_of_silent_truncation(self):
        with self.assertRaisesRegex(ValueError,'质量未守恒'):_deposit((3,3),np.array([[8.,0]]),np.ones(1),np.zeros(2),1)
    def test_iterative_final_layout_releases_then_adapts_initially_blocked_via(self):
        d,q,c,cache,p,m=fixture();states=np.array([[80.,0,0],[80,0,0]])
        dp=DeformationPreview(m,p,{},ViewSettings());f=dp.frame(states,m.current(states,p))
        self.assertEqual(f['initial_blocked_count'],1);self.assertEqual(f['released_count'],1);self.assertEqual(f['blocked_count'],0)
        self.assertEqual(f['accepted_count'],2);self.assertEqual(len(f['iterations']),2);self.assertFalse(f['violations'])
        self.assertFalse(m.current(states,p,f['replacements'])['issues'])
        values=f['replacements']['5/0'];self.assertGreaterEqual(values[0]['geometry'].distance(values[1]['geometry']),p.line_space_um)
        for value in values:self.assertTrue(width_ok(value['geometry'],p.line_width_um,d.dbu_um))
    def test_minimum_angle_checked_and_actual_bad_corners_located(self):
        acute=Polygon([(0,0),(100,0),(0,10)]);v=angle_regions(acute,90,.001)
        self.assertFalse(v.is_empty);self.assertLess(v.area,acute.area)
        self.assertTrue(angle_regions(box(0,0,100,100),90,.001).is_empty)
        d,q,c,cache,p,m=fixture();z=np.array([[80.,0,0],[80,0,0]])
        dp=DeformationPreview(m,p,{},ViewSettings(deformation_min_angle_deg=170));f=dp.frame(z,m.current(z,p))
        for values in f['replacements'].values():
            for value in values:
                if value.get('verified',True):self.assertTrue(angle_regions(value['geometry'],170,d.dbu_um).is_empty)
                else:self.assertTrue(f['diagnostics'])
        self.assertTrue(f['violations']);self.assertTrue(any(v['kind']=='最小内角不足' for v in f['violations']))
    def test_native_rule_violation_regions_and_frame_state_match(self):
        from test_v10 import fixture as efixture
        d,q,c,cache,p,m,res=efixture();engine=PreviewEngine(cache,c,q['roles'],{},p,res,c['connections']);engine.configure_deformation(True,ViewSettings())
        z=np.tile([35.,35,.01],(3,1));frame=engine.frame(z)
        self.assertTrue(any(v['kind']=='线宽不足' and v['geometry'].area>0 for v in frame['deformation']['violations']))
        np.testing.assert_array_equal(frame['deformation']['states'],z)
    def test_global_summary_never_calls_finite_extremes_a_probability_certificate(self):
        d,q,c,cache,p,m=fixture();res=evaluate(d,q['roles'],p,connections=c['connections']);e=PreviewEngine(cache,c,q['roles'],{},p,res,c['connections']);settings=ViewSettings();e.configure_deformation(True,settings)
        nominal=dict(e.frame(np.zeros((2,3))),nominal_global=True);out=e.global_frame(nominal,settings)
        self.assertTrue(out['global_deformation']['states']);self.assertIn('待细化',out['global_deformation']['continuous_probability'])
        self.assertTrue(all(v.get('adaptive_unresolved') and v['probability_lower']==0 for v in out['contact']['issues']))
    def test_optional_gpu_falls_back_equivalently(self):
        a=np.arange(80).reshape(8,10);b=np.ones((3,3))
        with patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cpu'}):
            np.testing.assert_allclose(convolve(a,b,backend='cuda'),convolve(a,b,backend='cpu'))
    def test_spawn_native_width_worker(self):
        from die_shift_studio.resources import parallel_map,resource_profile
        q=resource_profile();q['process_workers']=2
        out=parallel_map(baseline_check,[(box(0,0,100,100).wkb,20,.001),(box(0,0,100,5).wkb,20,.001)],q,process=True)
        self.assertEqual(out,['','原图形线宽不足'])
    def test_parameter_snapshot_does_not_share_editable_rows(self):
        source=[[1,2,3],[4,5,6]];p=Parameters.from_dict(dict(per_die_enabled=True,die_bounds=source))
        source[0][0]=99;self.assertEqual(p.for_die(0).single_bound_x_um,1)
    def test_per_die_validation(self):
        p=Parameters(per_die_enabled=True,die_bounds=[[1,2,3]]);p.validate()
        with self.assertRaisesRegex(ValueError,'DIE 2'):p.for_die(1)
        for v in ([[1,2,181]],[[1,-1,0]],[[1,2]],[[1,2,math.nan]]):
            with self.assertRaises(ValueError):Parameters(per_die_enabled=True,die_bounds=v).validate()

if __name__=='__main__':unittest.main()
