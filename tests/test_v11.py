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
from die_shift_studio.geometry_rules import width_ok,angle_regions
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
    def test_optional_gpu_falls_back_equivalently(self):
        a=np.arange(80).reshape(8,10);b=np.ones((3,3))
        with patch('die_shift_studio.acceleration.capabilities',return_value={'backend':'cpu'}):
            np.testing.assert_allclose(convolve(a,b,backend='cuda'),convolve(a,b,backend='cpu'))
    def test_parameter_snapshot_does_not_share_editable_rows(self):
        source=[[1,2,3],[4,5,6]];p=Parameters.from_dict(dict(per_die_enabled=True,die_bounds=source))
        source[0][0]=99;self.assertEqual(p.for_die(0).single_bound_x_um,1)
    def test_per_die_validation(self):
        p=Parameters(per_die_enabled=True,die_bounds=[[1,2,3]]);p.validate()
        with self.assertRaisesRegex(ValueError,'DIE 2'):p.for_die(1)
        for v in ([[1,2,181]],[[1,-1,0]],[[1,2]],[[1,2,math.nan]]):
            with self.assertRaises(ValueError):Parameters(per_die_enabled=True,die_bounds=v).validate()

if __name__=='__main__':unittest.main()
