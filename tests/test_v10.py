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
from die_shift_studio.deformation import DeformationPreview,Field,smooth_weight,width_ok
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
    def test_weight_endpoint_derivatives(self):
        h=1e-4
        for continuity in ('C1','C2'):
            self.assertEqual(float(smooth_weight(-1,continuity)),1);self.assertEqual(float(smooth_weight(2,continuity)),0)
            self.assertLess(abs((smooth_weight(h,continuity)-1)/h),.001)
            self.assertLess(abs(smooth_weight(1-h,continuity)/h),.001)
        # C2 second derivative tends to zero at both compact-support joins.
        self.assertLess(abs((smooth_weight(2*h)-2*smooth_weight(h)+1)/h**2),.02)
        self.assertLess(abs((smooth_weight(1-2*h)-2*smooth_weight(1-h))/h**2),.02)
    def test_deforms_original_copper_and_keeps_anchors_width_spacing(self):
        d,q,c,cache,p,m,res=fixture();original=hashlib.sha256(Path(d.path).read_bytes()).hexdigest()
        for method in ('bezier','arc'):
            for continuity in ('C1','C2'):
                dp=DeformationPreview(m,p,res,ViewSettings(deformation_method=method,deformation_continuity=continuity))
                for dx,dy,angle in [(35,35,.01),(-35,-35,-.01),(0,20,0),(2,3,.001)]:
                    z=np.tile([dx,dy,angle],(3,1));out=dp.frame(z,m.current(z,p))
                    self.assertGreater(out['accepted_count'],0,(method,continuity,out['diagnostics']))
                    for layer,values in out['replacements'].items():
                        for value in values:
                            self.assertTrue(value['verified'])
                            g=value['geometry'];co=next(c for c in dp.components if c.key==layer and c.indices==value['indices'])
                            self.assertTrue(g.is_valid and g.geom_type=='Polygon');self.assertTrue(width_ok(g,p.line_width_um,d.dbu_um))
                            self.assertGreater(g.symmetric_difference(co.poly).area,.1);self.assertGreater(value['dark'].area,10)
                            for anchor in co.anchors:self.assertTrue(g.covers(anchor))
                            for r in co.records:
                                self.assertTrue(g.covers(m.moved(r['poly'],r['owner'],z)))
                                self.assertGreaterEqual(g.boundary.distance(m.moved(r['poly'],r['owner'],z)),p.annulus_um)
                                for foreign in r['foreign']:self.assertGreaterEqual(g.distance(m.nodes[foreign]['poly'])+.00001,p.line_space_um)
        self.assertEqual(hashlib.sha256(Path(d.path).read_bytes()).hexdigest(),original)
    def test_blocked_and_unresolved_vias_never_acquire_deformed_pads(self):
        *_,p,m,res=fixture();dp=DeformationPreview(m,p,res,ViewSettings());z=np.tile([0.,35,0],(3,1));out=dp.frame(z,m.current(z,p))
        self.assertGreater(out['blocked_count'],0)
        self.assertTrue(any('原图形线宽不足' in d['reason'] for d in out['diagnostics']))
        for key,values in out['replacements'].items():
            for v in values:
                self.assertTrue(v['verified'])
                c=next(c for c in dp.components if c.key==key and c.indices==v['indices'])
                self.assertTrue(all((r['key'],r['index']) not in dp.blocked for r in c.records))
        # An inconclusive probability interval is not promoted to a safe interface.
        res['contact_analysis']['unresolved']=[dict(source_layer=r['key'],source_index=r['index']) for r in m.records]
        dp=DeformationPreview(m,p,res,ViewSettings());self.assertGreater(dp.frame(z,m.current(z,p))['accepted_count'],0)  # Specific poses use actual geometry, not global probability freezes.
    def test_each_animation_pose_revalidated_and_cache_reused(self):
        d,q,c,cache,p,m,res=fixture();e=PreviewEngine(cache,c,q['roles'],{},p,res,c['connections']);e.configure_deformation(True,ViewSettings())
        for t in np.linspace(.05,1,15):
            f=e.frame(np.tile([35*t,35*t,.01*t],(3,1)));self.assertGreater(f['deformation']['accepted_count'],0)
            np.testing.assert_array_equal(f['deformation']['states'],f['states']);self.assertIs(e.frame(f['states']),f)
        e.configure_deformation(False,ViewSettings());self.assertNotIn('deformation',e.frame(np.zeros((3,3))))
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
    def test_warp_preserves_holes_and_circular_option_changes_translation_path(self):
        from die_shift_studio.deformation import warp_polygon,snapped
        source=box(-100,-120,100,120).difference(box(-20,-20,20,20))
        records=[dict(center=[-80,0],owner=0)];centers=np.array([[-80.,0]]);states=np.array([[0.,4,0.]])
        fields=[Field(records,centers,states,[15],40,ViewSettings(deformation_method=method)) for method in ('bezier','arc')]
        outputs=[snapped(warp_polygon(source,f,.01,5),.001)[0] for f in fields]
        for g in outputs:
            self.assertTrue(g.is_valid);self.assertEqual(len(g.interiors),1);self.assertFalse(g.covers(Point(0,0)))
            self.assertTrue(width_ok(g,20,.001))
        self.assertGreater(outputs[0].symmetric_difference(outputs[1]).area,.001)
    def test_native_width_drc_handles_holes_and_narrow_necks(self):
        ring=box(-50,-50,50,50).difference(box(-25,-25,25,25))
        self.assertTrue(width_ok(ring,20,.001));self.assertFalse(width_ok(ring,30,.001))
        narrow=shapely.union_all([box(-50,-40,0,40),box(0,-4,80,4),box(80,-40,130,40)])
        self.assertFalse(width_ok(narrow,10,.001))

if __name__=='__main__':unittest.main()
