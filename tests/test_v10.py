"""Explicit stack order, validated source deformation, and viewport completeness."""
import unittest,json,math,hashlib
from pathlib import Path
from dataclasses import replace
import numpy as np
import shapely
from shapely.geometry import box,Point
from die_shift_studio.core import read_gds,build_case,Parameters,evaluate
from die_shift_studio.stack import stack_order,Connections
from die_shift_studio.contact import ContactModel
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.geometry_rules import width_ok
from die_shift_studio.settings import ViewSettings
from die_shift_studio.viewport import ViewportIndex,expanded_bounds
from die_shift_studio.project import normalize_project
ROOT=Path(__file__).resolve().parents[1]

def fixture():
    q=json.loads((ROOT/'examples/示例E_连续型变与约束.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path'])
    c=build_case(d,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(q['parameters'])
    m=ContactModel(d,c,q['roles'],c['connections'],cache);res=evaluate(d,q['roles'],p,connections=c['connections'])
    return d,q,c,cache,p,m,res

class V10Tests(unittest.TestCase):
    def test_stack_keeps_every_user_row_and_role_change_never_sorts(self):
        d,q,*_=fixture();self.assertEqual(stack_order(d,q['roles']),list(d.layers))
        order=q['layer_order'];self.assertEqual(stack_order(d,q['roles'],order),order)
        roles=dict(q['roles']);roles['127/0']='固定RDL'
        self.assertEqual(stack_order(d,roles,order),order)
        for schema in (1,2,3,4,5):self.assertEqual(normalize_project(dict(schema_version=schema,roles=roles,layer_order=order))['layer_order'],order)
    def test_nonmetal_interleaving_does_not_break_physical_adjacent_planes(self):
        d,q,c,*_=fixture();self.assertEqual(c['connections'].order,q['layer_order'])
        self.assertEqual(c['connections'].groups,[['6/0'],['5/0'],['4/0'],['3/0'],['2/0']])
    def test_viewport_query_is_superset_of_every_visible_moved_feature(self):
        d,q,c,cache,p,m,res=fixture();index=ViewportIndex(cache,c,{});rng=np.random.default_rng(10)
        for _ in range(12):
            states=rng.uniform(-1,1,(3,3))*[35,35,.03];layers=index.bound.transform(states)
            center=rng.uniform(-900,900,2);bounds=expanded_bounds((center[0]-150,center[0]+150),(center[1]-100,center[1]+100));region=box(*bounds)
            for key,layer in layers.items():
                expected={i for i,a in enumerate(layer['polygons']) if shapely.Polygon(a).intersects(region)}
                self.assertTrue(expected.issubset(set(index.query(key,bounds,states))))
    def test_contact_overlay_uses_replacement_metal_not_hidden_original(self):
        from test_v050 import fixture as small
        d,roles,case,cache,con,m,p=small();z=np.array([[30.,0,0]])
        self.assertTrue(m.current(z,p)['issues'])
        replacement={'40/0':[dict(indices=(1,),geometry=box(100,-10,120,10))]}
        self.assertFalse(m.current(z,p,replacement)['issues'])
        # The removed source is still intact for later nominal evaluations.
        self.assertTrue(m.current(z,p)['issues'])
    def test_native_width_drc_handles_holes_and_narrow_necks(self):
        ring=box(-50,-50,50,50).difference(box(-25,-25,25,25))
        self.assertTrue(width_ok(ring,20,.001));self.assertFalse(width_ok(ring,30,.001))
        narrow=shapely.union_all([box(-50,-40,0,40),box(0,-4,80,4),box(80,-40,130,40)])
        self.assertFalse(width_ok(narrow,10,.001))

if __name__=='__main__':unittest.main()
