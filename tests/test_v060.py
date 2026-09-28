"""Regression contracts for traced owners, real interfaces, Space Map and GDS repair."""
import hashlib, math, os, unittest
from dataclasses import replace
from pathlib import Path
import gdstk
import numpy as np
from shapely.geometry import Polygon,box,Point
from shapely.ops import unary_union
from die_shift_studio.core import GDSData,Parameters,build_case,read_gds,evaluate
from die_shift_studio.stack import Connections
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.contact import ContactModel
from die_shift_studio.preview import default_motion,rigid_transform,layer_owners
from die_shift_studio.space_map import space_maps
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.geometry_errors import sanitize_data
from die_shift_studio.project import normalize_project

USER_GDS = next((p for p in [Path(os.environ.get('DIE_SHIFT_TEST_GDS', '')),
                           Path.home()/'桌面/GD55_9-21.gds',
                           Path.home()/'下载/GD55_9-21.gds'] if p.is_file()), None)

def traced_fixture():
    arrays=lambda *rects:[np.asarray(box(*r).exterior.coords)[:-1] for r in rects]
    layers={'1/0':arrays((-1600,-800,1600,800)),
            '2/0':arrays((-1300,-400,-500,400),(500,-400,1300,400)),
            '10/0':arrays((-905,-5,-895,5),(895,195,905,205)),
            '30/0':arrays((-910,-3,910,3),(890,197,910,203)),
            '40/0':arrays((895,-5,905,5),(895,195,905,205)),
            '45/0':arrays((-1600,-800,1600,800)),
            '50/0':arrays((875,-20,925,20),(950,-20,970,20),(875,180,925,220)),
            '60/0':arrays((895,-5,905,5))}
    roles={'1/0':'封装边框','2/0':'芯片边框','10/0':'运动过孔','30/0':'运动RDL',
           '40/0':'运动过孔','45/0':'非金属图形','50/0':'固定RDL','60/0':'固定过孔'}
    layers={k:layers[k] for k in ['60/0','50/0','45/0','40/0','30/0','10/0','1/0','2/0']}
    d=GDSData('traced.gds','hash','TOP',['TOP'],.001,layers,roles)
    c=build_case(d,roles);cache=GeometryCache(d);model=ContactModel(d,c,roles,c['connections'],cache)
    p=Parameters(multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1,annulus_um=10,line_space_um=5)
    return d,roles,c,cache,model,p

class Revision060Tests(unittest.TestCase):
    def test_owner_follows_lower_metal_not_die_under_its_xy(self):
        d,r,c,cache,m,p=traced_fixture()
        self.assertTrue(c['dies'][1].covers(Polygon(d.layers['40/0'][0])))
        np.testing.assert_array_equal(c['ownership']['40/0'],[0,1])
        np.testing.assert_array_equal(c['ownership']['30/0'],[0,1])
        states=np.array([[35,12,.01],[-20,18,-.02]])
        e=PreviewEngine(cache,c,r,{k:default_motion(v) for k,v in r.items()},p,None,c['connections'])
        frame=e.frame(states)
        np.testing.assert_allclose(frame['layers']['40/0']['polygons'][0],rigid_transform(d.layers['40/0'][0],[-900,0],states[0]))
        np.testing.assert_array_equal(frame['layers']['50/0']['polygons'][0],d.layers['50/0'][0])
        np.testing.assert_array_equal(layer_owners(d,c,{},['40/0'])['40/0'],[0,1])
    def test_every_overlay_has_only_actual_moving_via_fixed_rdl_interfaces(self):
        d,r,c,cache,m,p=traced_fixture()
        self.assertEqual({q['key'] for q in m.records},{'40/0'})
        self.assertEqual(len(m.records),2);self.assertFalse(m.interface_masks['10/0'].any())
        self.assertEqual(len(m.current(np.zeros((2,3)),p)['regions']),2)
        probability=probability_maps(cache,m.bound,r,p,masks=m.interface_masks)
        self.assertEqual({q['key'] for q in probability['maps']},{'40/0'})
        field=space_maps(m,p);self.assertEqual(len(field['maps']),2)
        self.assertTrue(all(q['layers']==['40/0'] for q in field['maps']))
        self.assertFalse(any(q['virtual'] for q in m.records))
    def test_via_via_carries_owner_but_is_not_an_evaluation_interface(self):
        d,r,*_=traced_fixture();r['50/0']='固定过孔';c=build_case(d,r)
        np.testing.assert_array_equal(c['ownership']['40/0'],[0,1])
        m=ContactModel(d,c,r,c['connections']);self.assertEqual(m.records,[])
        self.assertTrue(c['connections'].links)
    def test_multiple_roots_or_no_root_never_use_nearest_die(self):
        d,r,*_=traced_fixture();d.layers['30/0'].append(np.asarray(box(898,0,902,200).exterior.coords))
        c=build_case(d,r);self.assertTrue(np.all(c['ownership']['40/0']==-2))
        self.assertTrue(all(len(q['dies'])==2 for q in c['connections'].ownership_notes))
        d,r,*_=traced_fixture();d.layers['10/0']=[];c=build_case(d,r)
        self.assertTrue(np.all(c['ownership']['40/0']==-2))
        self.assertEqual(ContactModel(d,c,r,c['connections']).records,[])
    def test_space_map_full_die_uniform_translation_and_rotation_gradient(self):
        d,r,c,cache,m,p=traced_fixture()
        flat=space_maps(m,replace(p,bound_angle_deg=0))
        for q in flat['maps']:
            self.assertLess(np.ptp(q['spacing']),1e-10)
            np.testing.assert_allclose([q['x_edges'][0],q['y_edges'][0],q['x_edges'][-1],q['y_edges'][-1]],q['domain'].bounds)
            self.assertTrue(q['domain'].covers(c['dies'][q['owner']]))
            np.testing.assert_allclose(q['spacing']-q['extra'],p.line_space_um)
        q=space_maps(m,p)['maps'][0];v=q['spacing'];self.assertGreater(np.ptp(v),1)
        high=space_maps(m,replace(p,target_nsigma=12))['maps'][0]['spacing'];self.assertTrue(np.all(high<=v+1e-9)) # fixed maximum B: k also sets the parent sigma
        stationary=space_maps(m,replace(p,multi_bound_x_um=0,multi_bound_y_um=0,bound_angle_deg=0))
        self.assertTrue(all(np.all(q['spacing']==p.line_space_um) for q in stationary['maps']))
    def test_space_map_infinity_respects_probability_support(self):
        *_,m,p=traced_fixture()
        maps=space_maps(m,replace(p,target_nsigma=math.inf))
        self.assertTrue(all(np.all(q['spacing']==p.line_space_um) for q in maps['maps']))
        ring=space_maps(m,replace(p,target_nsigma=math.inf,multi_bound_x_um=0,multi_bound_y_um=0))
        self.assertTrue(all(np.isfinite(q['spacing']).all() for q in ring['maps']))
    def test_hole_preserving_repair_and_receiver_merge_remove_internal_seams(self):
        ring=gdstk.boolean(gdstk.rectangle((-50,-50),(50,50)),gdstk.rectangle((-20,-20),(20,20)),'not')[0]
        d=GDSData('','','TOP',['TOP'],.001,{'5/0':[ring.points]});sanitize_data(d)
        union=unary_union([Polygon(a) for a in d.layers['5/0']])
        self.assertAlmostEqual(union.area,8400);self.assertFalse(union.covers(Point(0,0)))
        self.assertTrue(all(Polygon(a).is_valid for a in d.layers['5/0']))
        con=Connections(d,{'5/0':'固定RDL'})
        merged=con.component_geometry('5/0',0);self.assertAlmostEqual(merged.area,8400)
        self.assertEqual(len(merged.interiors),1)
    def test_project_migration_and_new_role_round_trip(self):
        old=dict(schema_version=3,roles={'3/0':'晶圆段RDL','4/0':'晶圆段过孔','5/0':'板段RDL','6/0':'板段过孔'},layer_order=['3/0','4/0','5/0','6/0'],parameters={})
        new=normalize_project(old);self.assertEqual(new['layer_order'],old['layer_order'])
        self.assertEqual(list(new['roles'].values()),['运动RDL','运动过孔','固定RDL','固定过孔'])
        self.assertEqual(normalize_project(new),new)
    @unittest.skipUnless(USER_GDS is not None,'External user GDS is not bundled; set DIE_SHIFT_TEST_GDS')
    def test_user_gds_522_vertex_shape_restored_against_klayout_area(self):
        path=USER_GDS;before=hashlib.sha256(path.read_bytes()).hexdigest();d=read_gds(path)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest());self.assertEqual(len(d.ignored_geometry),0)
        repaired=next(q for q in d.repaired_geometry if q['layer']=='5/0')
        self.assertEqual(repaired['index'],45);self.assertEqual(repaired['holes'],4)
        geom=unary_union([Polygon(a) for a in d.layers['5/0']]);self.assertAlmostEqual(geom.area,7001062.03804,delta=.01)
        r={k:'不参与' for k in d.layers};r.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'})
        c=build_case(d,r);m=ContactModel(d,c,r,c['connections']);self.assertEqual(len(m.records),158)
        self.assertEqual(c['connections'].ownership_notes,[])
        self.assertEqual([int((c['ownership']['4/0']==i).sum()) for i in range(3)],[9,40,109])

if __name__=='__main__':unittest.main()
