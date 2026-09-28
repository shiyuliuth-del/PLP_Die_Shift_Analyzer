import json,math,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from shapely.geometry import box
from die_shift_studio.core import GDSData,Parameters,build_case,evaluate,uncertainty_bound
from die_shift_studio.stack import Connections
from die_shift_studio.contact import ContactModel,support_contact
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.preview import default_motion
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.geometry_errors import sanitize_data
from die_shift_studio.report import report_document


def fixture(via_role='固定RDL'):
    arrays=lambda *bounds:[np.array(box(*b).exterior.coords)[:-1] for b in bounds]
    layers={'1/0':arrays((-1500,-1500,1500,1500)),'2/0':arrays((-1000,-1000,1000,1000)),
            '10/0':arrays((-5,-5,5,5)),'40/0':arrays((-20,-10,20,10),(50,-10,70,10))}
    d=GDSData('fixture.gds','hash','TOP',['TOP'],.001,layers)
    roles={'1/0':'封装边框','2/0':'芯片边框','10/0':'运动过孔','40/0':via_role}
    d.auto_roles=roles;case=build_case(d,roles);cache=GeometryCache(d);con=Connections(d,roles)
    modes={k:default_motion(r) for k,r in roles.items()};model=ContactModel(d,case,roles,con,cache,modes)
    p=Parameters(single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=0,annulus_um=10,line_space_um=5)
    return d,roles,case,cache,con,model,p

class Revision050Tests(unittest.TestCase):
    def test_foreign_network_touch_includes_boundary_and_excludes_own_network(self):
        d,r,c,cache,con,m,p=fixture()
        self.assertEqual(m.current([[29.999,0,0]],p)['issues'],[])
        issues=m.current([[30,0,0]],p)['issues'];self.assertEqual({q['scheme'] for q in issues},{1,2,3});self.assertEqual(len(issues),3)
        q=issues[0];self.assertEqual(q['scheme'],1);self.assertNotEqual(q['source_net'],q['foreign_net'])
        self.assertEqual(q['point'][0],50);self.assertEqual(q['origin'],[0,0])
        self.assertEqual(m.current([[0,0,0]],p)['issues'],[])
    def test_control_regions_use_real_contour_and_two_different_buffers(self):
        *_,m,p=fixture();q=m.current([[30,0,0]],p)['regions'][0]
        np.testing.assert_allclose(q['annulus'].bounds,[15,-15,45,15])
        np.testing.assert_allclose(q['spacing'].bounds,[10,-20,50,20])
    def test_transitive_same_network_is_never_foreign(self):
        d,r,c,cache,con,m,p=fixture();d.layers['40/0'].append(np.asarray(box(20,-2,50,2).exterior.coords)[:-1])
        con=Connections(d,r);m=ContactModel(d,c,r,con)
        self.assertEqual(m.current([[30,0,0]],p)['issues'],[])
    def test_fixed_via_does_not_fabricate_virtual_terminal(self):
        d,r,c,cache,con,m,p=fixture('固定过孔')
        self.assertEqual(m.records,[]);self.assertEqual(m.current([[100,0,0]],p)['regions'],[])
    def test_via_via_and_nonmetal_skip(self):
        d,r,c,cache,con,m,p=fixture('固定过孔')
        d.layers['22/0']=[np.asarray(box(-100,-100,100,100).exterior.coords)];r['22/0']='非金属图形'
        con=Connections(d,r,['10/0','22/0','40/0']);m=ContactModel(d,c,r,con)
        self.assertTrue(all(x['upper']=='10/0' and x['lower']=='40/0' for x in con.links))
        self.assertTrue(con.links);self.assertEqual(m.current([[30,0,0]],p)['issues'],[]) # via-via carries connectivity only
    def test_role_motion_cannot_be_overridden_by_old_manual_setting(self):
        d,r,c,cache,con,m,p=fixture();modes={k:default_motion(v) for k,v in r.items()};modes['40/0']='auto'
        m=ContactModel(d,c,r,con,cache,modes)
        self.assertTrue(np.all(m.bound.owners['40/0']==-1))
        self.assertTrue(m.current([[30,0,0]],p)['issues'])
        # Same-DIE relative motion remains invariant in the general support kernel.
        p=replace(p,target_nsigma=math.inf,single_bound_x_um=35,single_bound_y_um=35,bound_angle_deg=1)
        self.assertFalse(support_contact(box(-5,-5,5,5),0,box(50,-5,60,5),0,np.array([[0.,0.]]),p,1)[0])
    def test_infinity_normal_support_and_degenerate_line_support(self):
        d,r,c,cache,con,m,p=fixture();p=replace(p,target_nsigma=math.inf,single_bound_x_um=.00001,single_bound_y_um=.00001)
        self.assertFalse(m.screen(p)['issues']) # infinity means nominal binary geometry in V9
        self.assertFalse(m.screen(replace(p,single_bound_y_um=1e-20))['issues'])
        self.assertFalse(m.screen(replace(p,single_bound_x_um=0,single_bound_y_um=0))['issues'])
        upper=box(-5,-5,5,5);far_vertical=box(-5,100,5,110)
        linear=replace(p,single_bound_y_um=0)
        hit,_=support_contact(upper,0,far_vertical,-1,np.array([[0.,0.]]),linear,1);self.assertFalse(hit)
    def test_infinite_rotation_support_is_bounded_ring(self):
        p=replace(Parameters(),single_bound_x_um=0,single_bound_y_um=0,bound_angle_deg=.001,target_nsigma=6)
        centers=np.array([[0.,0.]]);via=box(95,-5,105,5)
        self.assertTrue(support_contact(via,0,box(-5,95,5,105),-1,centers,p,1)[0])
        self.assertFalse(support_contact(via,0,box(-5,-5,5,5),-1,centers,p,1)[0])
        self.assertFalse(support_contact(via,0,box(500,500,510,510),-1,centers,p,1)[0])
        self.assertTrue(np.isfinite(uncertainty_bound([[100,0]],[0,0],p,1)).all())
    def test_rank_one_tangency_with_positive_length_has_nonzero_probability(self):
        from shapely.geometry import Polygon
        p=replace(Parameters(),single_bound_x_um=1,single_bound_y_um=0,bound_angle_deg=0,target_nsigma=6)
        centers=np.array([[0.,0.]])
        self.assertTrue(support_contact(box(-5,-5,5,5),0,box(50,5,60,15),-1,centers,p,1)[0])
        diamond=Polygon([(0,-5),(5,0),(0,5),(-5,0)])
        other=Polygon([(50,5),(55,10),(50,15),(45,10)])
        self.assertFalse(support_contact(diamond,0,other,-1,centers,p,1)[0])
    def test_repairable_geometry_enters_connection_and_numeric_calculation(self):
        d,r,c,cache,con,m,p=fixture();invalid=np.array([[0,0],[10,10],[0,10],[10,0]])
        d.layers['40/0'].append(invalid);d.layers['41/0']=[invalid];r['41/0']='板段过孔'
        sanitize_data(d);self.assertEqual(len(d.ignored_geometry),0);self.assertEqual(len(d.repaired_geometry),2);self.assertEqual(d.repaired_geometry[0]['index'],3)
        self.assertIn('Self-intersection',d.repaired_geometry[0]['reason']);self.assertGreater(len(d.layers['41/0']),0)
        con=Connections(d,r);cache=GeometryCache(d);ContactModel(d,c,r,con,cache).current([[30,0,0]],p)
        result=evaluate(d,r,p);self.assertEqual(len(result['repaired_geometry']),2)
    def test_unconstructible_and_nonfinite_geometry_is_annotated_without_exception(self):
        d,*_=fixture();d.layers['99/0']=[np.array([[1.,2.],[3.,4.]]),np.array([[np.nan,0.],[2.,3.],[4.,5.]]),np.empty((0,2))]
        sanitize_data(d);self.assertEqual(d.layers['99/0'],[])
        self.assertEqual(len(d.ignored_geometry),3)
        self.assertTrue(all(np.isfinite(q['bounds']).all() for q in d.ignored_geometry))
        sanitize_data(d);self.assertEqual(len(d.ignored_geometry),3)
    def test_invalid_real_gds_read_keeps_source_and_layer_identity(self):
        import gdstk,hashlib
        from die_shift_studio.core import read_gds
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.gds';lib=gdstk.Library();cell=lib.new_cell('TOP')
            cell.add(gdstk.rectangle((0,0),(500,500),layer=1),gdstk.Polygon([(0,0),(10,10),(0,10),(10,0)],layer=77,datatype=9))
            lib.write_gds(path);before=hashlib.sha256(path.read_bytes()).hexdigest();d=read_gds(path)
            self.assertEqual(len(d.repaired_geometry),1);self.assertEqual(d.repaired_geometry[0]['layer'],'77/9')
            self.assertGreater(len(d.layers['77/9']),0);self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_probability_occupancy_area_and_zero_variance(self):
        d,r,c,cache,con,m,p=fixture();b=cache.bind(c,m.motions,list(d.layers))
        maps=probability_maps(cache,b,r,p,masks=m.interface_masks);self.assertEqual(len(maps['maps']),1)
        self.assertAlmostEqual(maps['maps'][0]['integrated_area_um2'],100,delta=2)
        self.assertEqual(float(maps['maps'][0]['probability'].max()),1)
        maps=probability_maps(cache,b,r,replace(p,single_bound_x_um=25,single_bound_y_um=25,bound_angle_deg=1),masks=m.interface_masks)
        self.assertAlmostEqual(maps['maps'][0]['integrated_area_um2'],100,delta=2)
        self.assertGreater(np.count_nonzero(maps['maps'][0]['probability']),20)
    def test_report_formula_resources_and_inf_round_trip(self):
        d,r,c,cache,con,m,p=fixture();p=replace(p,target_nsigma=math.inf,single_bound_x_um=1)
        result=evaluate(d,r,p);html,res=report_document(result)
        self.assertIn('其他网络',html);self.assertIn('无穷大',html);self.assertGreater(len(res),25)
        self.assertTrue(all(v.startswith(b'\x89PNG') for v in res.values()))
        self.assertEqual(Parameters.from_dict({'target_nsigma':'Infinity'}).target_nsigma,math.inf)

if __name__=='__main__':unittest.main()
