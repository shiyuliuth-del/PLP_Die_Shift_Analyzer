"""V12: enclosure output constraints, physical trace cuts and global edge DRC."""
import unittest,json,math
from pathlib import Path
from dataclasses import replace
import numpy as np
import shapely
from shapely.geometry import box,Polygon,Point
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.contact import ContactModel
from die_shift_studio.drc import rule_region,separation_region,GlobalDRC
from die_shift_studio.settings import ViewSettings
ROOT=Path(__file__).resolve().parents[1]

def fixture():
    q=json.loads((ROOT/'examples/示例G_小偏移与三段追溯.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path'])
    c=build_case(d,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(q['parameters'])
    m=ContactModel(d,c,q['roles'],c['connections'],cache)
    return d,q,c,cache,p,m

class V12Tests(unittest.TestCase):
    def test_drc_ignores_adjacent_corner_edges_but_finds_narrow_parallel_neck(self):
        needle=Polygon([(0,0),(100,0),(100,1)])
        self.assertTrue(rule_region(needle,10,.001).is_empty)
        neck=shapely.union_all([box(-40,-20,0,20),box(0,-2,100,2),box(100,-20,140,20)])
        self.assertFalse(rule_region(neck,10,.001).is_empty)
        self.assertTrue(rule_region(box(0,0,100,20),20,.001).is_empty)
        a=box(0,0,100,20);b=box(0,25,100,45)
        self.assertFalse(separation_region(a,b,10,.001).is_empty)
        # Near-perpendicular separated edges are not a parallel spacing rule.
        far=Polygon([(105,0),(115,10),(125,0),(115,-10)])
        self.assertTrue(separation_region(a,far,10,.001).is_empty)
        self.assertFalse(separation_region(a,box(90,10,110,30),10,.001).is_empty)
        from die_shift_studio.drc import separation_area
        touch=Polygon([(100,20),(110,25),(115,35)])
        self.assertGreater(separation_area(a,touch,10,.001),0)
        self.assertGreater(separation_region(a,touch,10,.001).area,0)
        ramp=Polygon([(0,1),(1000,41),(1000,61),(0,21)])
        marker=separation_region(box(0,-20,1000,0),ramp,10,.001)
        self.assertFalse(marker.is_empty);self.assertLess(marker.bounds[2],230)
        for angle in (.1,.5,1,2,4,4.99):
            triangle=Polygon([(0,0),(1000,0),(1000,1000*math.tan(math.radians(angle)))])
            self.assertTrue(rule_region(triangle,100,.001).is_empty)
    def test_global_drc_independent_of_assessment_and_deformation_and_reuses_rigid_rules(self):
        d,q,c,cache,p,m=fixture();p=replace(p,line_width_um=50)
        e=PreviewEngine(cache,c,q['roles'],{},p,None,c['connections']);e.configure_drc(True,ViewSettings())
        a=e.frame(np.zeros((2,3)));self.assertNotIn('deformation',a)
        errors=[v for v in a['drc'] if v['kind']=='线宽不足' and v['layer']=='5/0'];self.assertTrue(errors)
        misses=e.global_drc.misses;z=np.array([[11.,13,0],[11,13,0]])
        b=e.frame(z);self.assertEqual(e.global_drc.misses,misses);self.assertGreater(e.global_drc.hits,0)
        newer=[v for v in b['drc'] if v['kind']=='线宽不足' and v['layer']=='5/0']
        for first,last in zip(errors,newer):np.testing.assert_allclose(np.array(last['geometry'].centroid.coords)-first['geometry'].centroid.coords,[[0,0]],atol=1e-8)
        # Unknown ownership freezes geometry but must not switch its process rule.
        m.bound.owners['3/0'][:]=-2
        unknown=GlobalDRC(m,p).frame(z,settings=ViewSettings())
        self.assertTrue(any(v['kind']=='线宽不足' and v['layer']=='5/0' for v in unknown))
    def test_holes_and_multipolygons_survive_motion_for_global_drc(self):
        *_,m=fixture();g=box(-10,-10,10,10).difference(box(-5,-5,5,5)).union(box(20,0,30,10))
        moved=m.moved(g,0,np.array([[3.,7,.1],[0,0,0]]));self.assertAlmostEqual(moved.area,g.area)
        self.assertEqual(sum(len(q.interiors) for q in moved.geoms),1)

if __name__=='__main__':unittest.main()
