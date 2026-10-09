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
from die_shift_studio.deformation import DeformationPreview,warp_polygon
from die_shift_studio.route_adaptation import straight_candidates,RouteField,routed_fields
from die_shift_studio.drc import rule_region,separation_region,GlobalDRC
from die_shift_studio.settings import ViewSettings
ROOT=Path(__file__).resolve().parents[1]

def fixture():
    q=json.loads((ROOT/'examples/示例G_小偏移与三段追溯.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path'])
    c=build_case(d,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(q['parameters'])
    m=ContactModel(d,c,q['roles'],c['connections'],cache)
    return d,q,c,cache,p,m

class V12Tests(unittest.TestCase):
    def test_small_motion_inside_original_pad_still_adapts_for_enclosure_range(self):
        d,q,c,cache,p,m=fixture();z=np.array([[1.,1,0],[0,0,0]])
        r=next(r for r in m.records if r['owner']==0)
        for annulus in (0,2,8,12,25):
            pp=replace(p,annulus_um=annulus);dp=DeformationPreview(m,pp,{},ViewSettings())
            source=next(c for c in dp.components if any(r['owner']==0 for r in c.records)).poly
            moved=m.moved(r['poly'],0,z);self.assertTrue(source.covers(moved))
            f=dp.frame(z,m.current(z,pp));self.assertEqual(f['accepted_count'],1,(annulus,f['diagnostics']))
            v=f['replacements']['5/0'][0];self.assertTrue(v['verified']);self.assertGreater(v['geometry'].symmetric_difference(source).area,.01)
            self.assertGreaterEqual(v['geometry'].boundary.distance(moved)+.001,annulus)
            self.assertTrue(v['geometry'].covers(moved))
    def test_three_part_tracks_actual_folded_copper_and_keeps_fixed_anchor(self):
        d,q,c,cache,p,m=fixture();z=np.array([[1.,1,0],[0,35,0]])
        for method in ('bezier','arc'):
            for continuity in ('C1','C2'):
                dp=DeformationPreview(m,p,{},ViewSettings(deformation_method=method,deformation_continuity=continuity))
                # Exercise the traced fallback itself; the default V18 search
                # can now solve this case with a whole-section field earlier.
                from unittest.mock import patch
                with patch('die_shift_studio.axial_adaptation.axial_candidates',return_value=iter(())), patch('die_shift_studio.material_field.material_candidates',return_value=iter(())):
                    f=dp.frame(z,m.current(z,p))
                self.assertEqual(f['accepted_count'],2,f['diagnostics'])
                self.assertFalse(f['violations']);v=next(v for v in f['replacements']['5/0'] if v['mode']=='three_part')
                self.assertGreater(v['span_um'],500);self.assertGreater(v['adaptation'].area,20000)
                self.assertAlmostEqual(v['adaptation'].centroid.x,450,delta=8)
                component=next(co for co in dp.components if co.indices==v['indices'])
                for anchor in component.anchors:self.assertTrue(v['geometry'].covers(anchor.buffer(3)))
                # A bend near the moving end moves rigidly; the far fixed end does not.
                self.assertTrue(v['geometry'].covers(Point(0,985)))
                self.assertLess(v['geometry'].intersection(box(850,320,940,700)).symmetric_difference(component.poly.intersection(box(850,320,940,700))).area,.001)
    def test_straight_cut_join_endpoint_derivatives_and_branches(self):
        d,q,c,cache,p,m=fixture();dp=DeformationPreview(m,p,{},ViewSettings());co=dp.components[1]
        candidate=next(straight_candidates(co.poly,co.records,co.anchors,20,.001));moving,band,start,u,length,width=candidate
        z=np.array([[0.,0,0],[0,35,0]])
        for continuity in ('C1','C2'):
            f=RouteField(co.records,m.bound.centers,z,ViewSettings(deformation_continuity=continuity),*candidate)
            for t in (0.,1.):
                h=.01;xy=start+u*length*t
                points=np.array([xy-u*h,xy,xy+u*h]);shift=f(points)-points
                self.assertLess(np.linalg.norm(shift[2]-shift[0])/(2*h),1e-5)
                if continuity=='C2':self.assertLess(np.linalg.norm(shift[0]-2*shift[1]+shift[2])/h**2,1e-5)
        # Cuts cannot detach / drag a fixed anchor or secretly jump a gap.
        none=list(straight_candidates(co.poly,co.records,[*co.anchors,co.records[0]['poly']],20,.001))
        self.assertFalse(none)
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
    def test_unverified_best_shape_is_restored_and_all_final_markers_use_source(self):
        d,q,c,cache,p,m=fixture();cfg=ViewSettings(deformation_min_angle_deg=179)
        dp=DeformationPreview(m,p,{},cfg);z=np.array([[1.,1,0],[0,35,0]]);f=dp.frame(z,m.current(z,p))
        self.assertEqual(f['best_effort_count'],0);self.assertGreater(f['unresolved_count'],0);self.assertTrue(f['diagnostics']);self.assertTrue(f['violations'])
        self.assertFalse(f['replacements']);self.assertEqual(len(f['interfaces']),2)
        for vs in f['replacements'].values():
            for v in vs:self.assertTrue(v['geometry'].is_valid)
        actual=m.current(z,p,f['replacements']);self.assertEqual(f['blocked_count'],len({(v['source_layer'],v['source_index']) for v in actual['issues']}))

if __name__=='__main__':unittest.main()
