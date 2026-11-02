import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import gdstk
import numpy as np
from shapely.geometry import Polygon

from die_shift_studio.core import (Parameters,read_gds,build_case,evaluate,uncertainty_bound,
                                    rectangle,rectangle_info,spacing_violation,clean_json)

ROOT=Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def example(self,name):
        project=json.loads((ROOT/'examples'/f'{name}.json').read_text())
        data=read_gds(ROOT/'examples'/project['gds_path'])
        data.layers={k:data.layers[k] for k in project['layer_order']}
        return data,project['roles'],Parameters.from_dict(project['parameters'])
    def test_examples_respect_foreign_net_contact(self):
        for name,expected in [('示例A_小芯片大孔盘',None),('示例B_大芯片局部重构',1),('示例C_多芯片晶圆接口',None)]:
            data,roles,p=self.example(name)
            r=evaluate(data,roles,p)
            candidates=[s for s in r['schemes'] if s['status']=='初筛候选']
            if candidates:
                chosen=r['schemes'][r['recommendation']['scheme']-1]
                self.assertLessEqual(chosen['gamma'],min(s['gamma'] for s in candidates)+1e-9)
                self.assertIn('contact_status',chosen)
            else:self.assertIsNone(r['recommendation']['scheme'])
            self.assertTrue(r['recommendation']['provisional'])
            json.dumps(clean_json(r),allow_nan=False)
    def test_no_wafer_layers_no_scheme3(self):
        data,roles,p=self.example('示例C_多芯片晶圆接口')
        r=evaluate(data,roles,replace(p,wafer_layers=0))
        self.assertEqual(r['schemes'][2]['status'],'结构不适用')
    def test_missing_outputs_not_fabricated(self):
        data,roles,p=self.example('示例C_多芯片晶圆接口');roles=roles.copy();roles['30/0']='不参与'
        r=evaluate(data,roles,p)
        self.assertEqual(r['schemes'][2]['status'],'资料待补充')
        self.assertIsNone(r['recommendation']['scheme'])
    def test_multidie_topology_is_automatic(self):
        data,roles,p=self.example('示例C_多芯片晶圆接口')
        r=evaluate(data,roles,replace(p,interdie='unknown'))
        self.assertNotIn('尚未确认是否存在跨芯片互连',str(r['schemes']))
        self.assertIn(r['parameters']['interdie'],('none','present'))
    def test_source_not_modified(self):
        data,roles,p=self.example('示例A_小芯片大孔盘')
        before=Path(data.path).read_bytes();evaluate(data,roles,p)
        self.assertEqual(before,Path(data.path).read_bytes())
    def test_input_sigma_scaling(self):
        p=Parameters(bound_angle_deg=0,mean_angle_deg=0)
        a=uncertainty_bound([[1000,0]],[0,0],p,1)[0]
        b=uncertainty_bound([[1000,0]],[0,0],replace(p,input_nsigma=3),1)[0]
        self.assertAlmostEqual(b,a) # obsolete multiplier no longer changes calculations
    def test_rotation_and_mean_bound(self):
        p=Parameters(single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=0,mean_angle_deg=90)
        q=uncertainty_bound([[1000,0]],[0,0],p,1)[0]
        self.assertEqual(q,0.)  # A zero rotation amplitude locks the axis even with a system bias.
    def test_bad_parameters(self):
        for change in [dict(panel_layers=0,wafer_layers=0),dict(line_width_um=0),dict(rho_xy=1.1),dict(target_nsigma=-1),dict(wafer_layers=1.5),dict(mean_x_um=float('nan'))]:
            with self.assertRaises(ValueError):replace(Parameters(),**change).validate()
    def test_manual_die_validation(self):
        data,roles,p=self.example('示例A_小芯片大孔盘')
        with self.assertRaises(ValueError):build_case(data,roles,{'dies':[dict(cx=10000,cy=0,width=1000,height=1000)]})
        with self.assertRaises(ValueError):rectangle_info(Polygon([(0,0),(100,0),(0,100)]))
    def test_rotated_rectangle_is_not_bbox(self):
        info=rectangle_info(rectangle(20,30,600,1800,31))
        self.assertAlmostEqual(info['width'],1800);self.assertAlmostEqual(info['height'],600)
    def test_spatial_spacing_index(self):
        self.assertTrue(spacing_violation([rectangle(0,0,10,10),rectangle(12,0,10,10)],3))
        self.assertFalse(spacing_violation([rectangle(0,0,10,10),rectangle(20,0,10,10)],3))
    def test_units_hierarchy_array_rotation_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=gdstk.Library(unit=1e-9,precision=1e-10);leaf=lib.new_cell('LEAF');root=lib.new_cell('TOP')
            leaf.add(gdstk.rectangle((0,0),(500000,1000000),layer=2))
            root.add(gdstk.Reference(leaf,origin=(2000000,3000000),rotation=math.pi/2,x_reflection=True,columns=2,rows=1,spacing=(2000000,0)))
            path=Path(tmp)/'hier.gds';lib.write_gds(path);data=read_gds(path)
            self.assertEqual(len(data.layers['2/0']),2)
            self.assertAlmostEqual(data.dbu_um,.0001)
            poly=Polygon(data.layers['2/0'][0]);self.assertAlmostEqual(poly.area,500000,places=5)
            self.assertAlmostEqual(min(poly.bounds),2000,places=6)
            self.assertEqual(data.top_names,['TOP'])
    def test_multiple_top_requires_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=gdstk.Library();lib.new_cell('A').add(gdstk.rectangle((0,0),(1,1)));lib.new_cell('B').add(gdstk.rectangle((0,0),(2,2)))
            path=Path(tmp)/'two.gds';lib.write_gds(path)
            self.assertEqual(read_gds(path)['top_names'],['A','B'])
            self.assertEqual(read_gds(path,'B').top_name,'B')
    def test_conservative_bound_covers_random_samples(self):
        from die_shift_model import displacement
        p=Parameters(target_nsigma=3,rho_xy=.3,mean_angle_deg=.2,mean_x_um=2)
        points=np.array([[3000,1000],[-3000,1000],[0,-1000.]])
        bound=uncertainty_bound(points,[0,0],p,1)
        sx=25/6;theta=math.radians(1/6)
        cov=np.array([[sx*sx,.3*sx*sx,0],[.3*sx*sx,sx*sx,0],[0,0,theta*theta]])
        z=np.random.default_rng(89).multivariate_normal([2,0,math.radians(.2)],cov,80000)
        fail=(np.linalg.norm(displacement(points,z),axis=-1)>bound).any(axis=1).mean()
        self.assertLess(fail,.0027)


if __name__=='__main__':unittest.main()
