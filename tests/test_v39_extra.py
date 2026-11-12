import json,tempfile,unittest
from pathlib import Path
import numpy as np
import gdstk,shapely
from shapely.geometry import Point,LineString,GeometryCollection,box
from die_shift_studio.core import read_gds
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_shapes import shape_nodes,node_fingerprint,edited_snapshot
from die_shift_studio.gds_merge import restore_project_geometry,winding_outline,filled_polygon
from die_shift_studio.region_export import line_parts,contours,write_regions
from die_shift_studio.dxf_export import Snapshot
from die_shift_studio.dxf_settings import DxfSettings
from test_v38 import fixture
from die_shift_studio.reconstruction_route import warp


class V39ExtraTests(unittest.TestCase):
    def test_touch_points_terminate_instead_of_recursive_get_parts(self):
        line=LineString([(0,0),(1,1)])
        g=GeometryCollection([Point(0,0),GeometryCollection([line,Point(2,2)])])
        self.assertEqual(len(line_parts(g)),1);self.assertEqual(line_parts(Point(0,0)),[])

    def test_abc_shared_endpoints_are_identical_open_coordinates(self):
        g=box(0,0,10,10);snap=Snapshot({'5/0':(g.wkb,)},'TOP',None,0)
        data={'pieces':[dict(layer='5/0',regions=dict(A=box(-1,-1,0,0),B=box(0,0,5,10),C=box(5,0,10,10)))]}
        cfg=DxfSettings(scale=.7)
        shapes,_,_=contours(snap,data,{'5/0':'固定RDL'},cfg)
        self.assertTrue(shapes)
        self.assertTrue(all(not np.array_equal(xy[0],xy[-1]) for _,xy in shapes))
        ends={name:[tuple(p) for n,xy in shapes if n==name for p in (xy[0],xy[-1])] for name,_ in shapes}
        self.assertEqual(set(ends['L5_D0B']) & set(ends['L5_D0C']),{(3.5,0.),(3.5,7.)})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'regions.dxf';write_regions(snap,data,{'5/0':'固定RDL'},path,settings=cfg)
            import ezdxf
            entities=list(ezdxf.readfile(path).modelspace())
            self.assertTrue(all(q.dxftype()=='LWPOLYLINE' and not q.closed for q in entities))

    def test_fractured_circle_is_one_whole_imported_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
            circle=gdstk.ellipse((0,0),100,tolerance=.001,layer=5)
            cell.add(*circle.fracture(max_points=30,precision=.001));path=Path(tmp)/'fractured.gds';lib.write_gds(path,max_points=30)
            d=read_gds(path);self.assertGreater(len(d._legacy_snapshot.layers['5/0']),1)
            self.assertEqual(len(d.layers['5/0']),1)
            g=GeometryCache(d).layers['5/0']['geometry'][0]
            self.assertTrue(g.is_valid);self.assertEqual(g.geom_type,'Polygon');self.assertGreater(len(d.layers['5/0'][0]),30)

    def test_holes_merge_and_whole_object_node_edits(self):
        p=box(0,0,100,100).difference(box(30,30,70,70))
        xy=winding_outline(p);self.assertLess(filled_polygon(xy,True).symmetric_difference(p).area,1e-9)
        with tempfile.TemporaryDirectory() as tmp:
            lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
            for rect in ((0,0,100,30),(0,70,100,100),(0,30,30,70),(70,30,100,70)):
                cell.add(gdstk.rectangle(rect[:2],rect[2:],layer=5))
            path=Path(tmp)/'hole.gds';lib.write_gds(path);d=read_gds(path)
            self.assertEqual(len(d.layers['5/0']),1)
            g=GeometryCache(d).layers['5/0']['geometry'][0];self.assertEqual(len(g.interiors),1)
            kind,nodes,_=shape_nodes(d,'5/0',0)
            edit=dict(layer='5/0',index=0,original=node_fingerprint(kind,nodes),nodes=(nodes+[5,0]).tolist())
            changed=edited_snapshot(d,[edit]);actual=GeometryCache(changed).layers['5/0']['geometry'][0]
            self.assertEqual(len(actual.interiors),1);self.assertAlmostEqual(actual.centroid.x,55)

    def test_legacy_fragment_edits_and_ignores_survive_merge_and_resave(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
            cell.add(gdstk.rectangle((0,0),(50,50),layer=5),gdstk.rectangle((50,0),(100,50),layer=5))
            path=Path(tmp)/'legacy.gds';lib.write_gds(path);d=read_gds(path)
            self.assertEqual(len(d.layers['5/0']),1)
            kind,xy,_=shape_nodes(d._legacy_snapshot,'5/0',0)
            old=dict(layer='5/0',index=0,original=node_fingerprint(kind,xy),nodes=(xy-[5,0]).tolist())
            project={'node_edits':[old],'settings':{},'ignored_width_lines':{'sha256':d.sha256,'top_cell':'TOP','refs':[['5/0',1]]}}
            a,new=restore_project_geometry(d,project)
            self.assertEqual(len(a.layers['5/0']),2);self.assertEqual(new['geometry_indexing'],'merged-v39');self.assertEqual(new['legacy_node_edits'],[old])
            b,_=restore_project_geometry(read_gds(path),json.loads(json.dumps(new)))
            self.assertEqual(a.geometry_id,b.geometry_id)
            self.assertTrue(all(x.equals(y) for x,y in zip(GeometryCache(a).layers['5/0']['geometry'],GeometryCache(b).layers['5/0']['geometry'])))

    def test_both_endpoint_whole_pad_modes_keep_both_landings(self):
        e,_,_=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan']
            part=plan.partition(0,plan.length)
            self.assertEqual(part['near'].geom_type,'Point');self.assertEqual(part['far'].geom_type,'Point')
            for style in ('linear','c1','c2','arc'):
                g,error=warp(part,plan,np.zeros(2),np.array([20.,10.,.01]),style)
                self.assertIsNone(error,(style,error));self.assertTrue(g['B'].is_empty and g['C'].is_empty)
                self.assertLess(Point(600,0).buffer(29).difference(g['A']).area,.001)
            back,_=plan.edit(0,plan.length,1,400);self.assertEqual(back['a'],0);self.assertLess(back['b'],plan.fixed_start)
        finally:e.close()

class V39ConnectionTests(unittest.TestCase):
    def test_return_bend_preserves_selected_style_and_straight_clamps(self):
        from die_shift_studio.connection_curves import linear_gap
        from die_shift_studio.reconstruction_route import failed_connection
        e,_,_=fixture()
        try:
            plan=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0]['route_plan']
            part=plan.partition(100,400);center=np.zeros(2);pose=np.array([600.,200.,0.])
            for style in ('c1','c2','arc'):
                regions,error=warp(part,plan,center,pose,style)
                self.assertIsNone(error,(style,error))
                self.assertTrue(shapely.union_all(list(regions.values())).is_valid)
                self.assertLess(regions['A'].intersection(regions['C']).area,.001)
            regions,error=warp(part,plan,center,pose,'linear');self.assertIsNone(regions);self.assertTrue(error)
            dropped=failed_connection(part,plan,center,pose)
            self.assertTrue(dropped['A'].is_empty)
            self.assertTrue(dropped['B'].equals(part['B']))
            clamped,stop=plan.edit_pose(100,500,0,450,center,np.array([200.,0.,0.]),'linear')
            self.assertTrue(stop);self.assertEqual(clamped['b'],500)
            self.assertGreater(linear_gap(clamped,plan,center,np.array([200.,0.,0.])),0.)
            self.assertLess(clamped['a'],301)
        finally:e.close()

    def test_disconnected_result_is_marked_and_never_outputs_missing_strip(self):
        from dataclasses import replace
        e,_,_=fixture()
        try:
            e.configure_reconstruction(True,replace(e.reconstructor.settings,reconstruction_style='linear'))
            f=e.reconstructor.frame(np.array([[800.,0.,0.]]));q=f['pieces'][0]
            self.assertTrue(q['connection_failed']);self.assertFalse(q['valid'])
            self.assertFalse(q['problem_region'].is_empty);self.assertTrue(q['problem_reasons'])
            self.assertTrue(q['regions']['A'].is_empty)
            copper=f['replacements']['5/0'][0]['geometry']
            self.assertTrue(copper.is_valid);self.assertEqual(copper.geom_type,'MultiPolygon')
            self.assertLess(Point(800,0).buffer(10).difference(copper).area,.001)
            self.assertLess(Point(600,0).buffer(10).difference(copper).area,.001)
        finally:e.close()

    def test_multibranch_land_has_independent_shared_intervals_and_point_handles(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from matplotlib.figure import Figure
        from die_shift_studio.core import GDSData,Parameters,build_case
        from die_shift_studio.fast_preview import PreviewEngine
        from die_shift_studio.settings import ViewSettings
        from die_shift_studio.reconstruction_overlay import ReconstructionOverlay
        source=shapely.union_all([LineString([(0,0),(200,0),(600,y)]).buffer(15) for y in (-200,200)] +
                                 [Point(x,y).buffer(30) for x,y in ((0,0),(600,200),(600,-200))])
        shapes={'127/0':[box(-300,-500,1000,600)],'128/0':[box(-80,-80,80,80)],'3/0':[box(-20,-20,20,20)],
                '4/0':[Point(0,0).buffer(10)],'5/0':[source],'6/0':[Point(600,y).buffer(10) for y in (-200,200)]}
        roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
        d=GDSData('branches.gds','v39-branches','TOP',['TOP'],.001,{k:[np.asarray(g.exterior.coords) for g in v] for k,v in shapes.items()},roles)
        c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters(single_bound_x_um=20,single_bound_y_um=20,annulus_um=10)
        e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings())
        try:
            first=e.reconstructor.frame(np.zeros((1,3)));qs=first['pieces']
            self.assertEqual(len(qs),2);self.assertTrue(all(q['route_plan'] for q in qs))
            self.assertEqual(len({q['route_identity'] for q in qs}),2)
            rows=[]
            for q in qs:
                plan=q['route_plan'];rows.append((*plan.key,plan.identity,0.,plan.length))
            cfg=replace(e.reconstructor.settings,reconstruction_intervals=tuple(rows));e.configure_reconstruction(True,cfg)
            pose=np.array([[15.,10.,.01]]);frame=e.reconstructor.frame(pose);qs=frame['pieces']
            self.assertTrue(all(q['valid'] for q in qs),[q['warning'] for q in qs])
            self.assertTrue(all(q['moved_cut'].geom_type==q['extra_cut'].geom_type=='Point' for q in qs))
            copper=frame['replacements']['5/0'][0]['geometry']
            self.assertEqual(copper.geom_type,'Polygon');self.assertTrue(copper.is_valid)
            painted=shapely.union_all([g for q in qs for g in q['regions'].values()])
            self.assertLess(copper.symmetric_difference(painted).area,.001)
            fig=Figure(figsize=(8,5));ax=fig.subplots();ax.set_xlim(-100,700);ax.set_ylim(-300,300)
            overlay=object.__new__(ReconstructionOverlay);overlay.w=SimpleNamespace(ax=ax,current_frame={'states':pose,'rotation_centers':e.bound.centers})
            handles=overlay.handle_geometries(qs);pts=[ax.transData.transform(handles[overlay._key(q),'moved'].coords[0]) for q in qs]
            self.assertGreater(np.linalg.norm(pts[0]-pts[1]),18)
            settings,part=e.reconstructor.edited(qs[0],1,qs[0]['route_plan'].route.interpolate(qs[0]['route_plan'].length-100).coords[0])
            self.assertEqual(len(settings.reconstruction_intervals),2)
            untouched=[row for row in settings.reconstruction_intervals if row[4]==qs[1]['route_identity']][0]
            self.assertEqual(untouched[-2:],qs[1]['interval'])
        finally:e.close()

if __name__=='__main__':unittest.main()
