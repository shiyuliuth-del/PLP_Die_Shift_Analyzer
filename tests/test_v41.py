"""V41 physical topology, persistent editing and exact DXF publication contracts."""
from dataclasses import replace,asdict
from collections import Counter
import json
import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
import shapely
from shapely.geometry import Point,box,LineString
from test_v38 import fixture
from die_shift_studio.settings import ViewSettings
from die_shift_studio.reconstruction_edits import rotate,apply_angles,key
from die_shift_studio.reconstruction import transform
from die_shift_studio.reconstruction_enclosure import reachable_enclosure,enclosure_certificate
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_contract import verify_written_contours
from die_shift_studio.dxf_export import Snapshot


class V41Tests(unittest.TestCase):
    def test_visibility_fallback_finds_an_interior_bent_route(self):
        from unittest.mock import patch
        from die_shift_studio.reconstruction_route import trace
        source=LineString([(0,0),(200,0),(200,200)]).buffer(20)
        with patch('die_shift_studio.reconstruction_route.medial_graph',return_value=(np.empty((0,2)),())):
            route=trace(source,(0,0),(200,200),15)
        self.assertIsNotNone(route);self.assertTrue(source.covers(route))
        self.assertEqual(tuple(route.coords[0]),(0.,0.));self.assertEqual(tuple(route.coords[-1]),(200.,200.))

    def test_new_zone_on_ignored_shape_keeps_its_clicked_source_position(self):
        e,_,_=fixture()
        try:
            cfg=replace(e.reconstructor.settings,reconstruction_forced=(('5/0',0),),
                        reconstruction_added=(('5/0',0,0.,0.,'seed-case','seed'),))
            cfg=ViewSettings.from_dict(json.loads(json.dumps(asdict(cfg))))
            e.configure_reconstruction(True,cfg);q=e.frame(np.zeros((1,3)))['reconstruction']['pieces'][0]
            self.assertTrue(q['nominal_regions']['A'].covers(Point(0,0)))
            self.assertTrue(q['route_identity'].endswith('~seed-case'))
        finally:e.close()

    def test_independent_die_supports_on_a_midspan_connection_do_not_overlap(self):
        from test_v40 import make_engine
        from die_shift_studio.core import Parameters
        source=LineString([(-500,300),(0,0),(0,-600)]).buffer(15)
        for point in ((-500,300),(0,0),(0,-600)):source=source.union(Point(point).buffer(30))
        shapes={'127/0':[box(-800,-800,300,600)],'128/0':[box(-550,250,-450,350),box(-50,-50,50,50)],
                '3/0':[Point(-500,300).buffer(20),Point(0,0).buffer(20)],
                '4/0':[Point(-500,300).buffer(10),Point(0,0).buffer(10)],'5/0':[source],'6/0':[Point(0,-600).buffer(10)]}
        e,_,_=make_engine(shapes,Parameters(multi_bound_x_um=15,multi_bound_y_um=15,annulus_um=5))
        try:
            for z in (np.zeros((2,3)),np.array([[-15,-15,-.01],[15,15,.01]])):
                frame=e.frame(z)['reconstruction'];pieces=frame['pieces']
                self.assertTrue(all(q['valid'] and not q['regions']['A'].is_empty for q in pieces),[(q['warning'],q['interval']) for q in pieces])
                by_owner=[shapely.union_all([q['nominal_regions'][k] for q in pieces if q['die']==owner for k in 'AC']) for owner in (0,1)]
                self.assertLess(by_owner[0].intersection(by_owner[1]).area,1e-8)
                self.assertEqual(frame['replacements']['5/0'][0]['geometry'].geom_type,'Polygon')
        finally:e.close()

    def test_route_terminal_bounds_use_intersections_not_remote_vertex_projection(self):
        from die_shift_studio.reconstruction_route import RoutePlan
        route=LineString([(0,0),(100,0),(100,100)]);source=box(-20,-20,120,120)
        land=box(-10,-10,10,10).union(box(70,70,80,80))
        anchors=(box(90,90,110,110),box(-10,50,10,60))
        plan=RoutePlan('5/0',0,0,(),source,route,land,anchors,.001,3.)
        self.assertAlmostEqual(plan.land_end,10.);self.assertAlmostEqual(plan.fixed_start,190.)

    def test_zone_colour_uses_only_pre_and_post_active_material(self):
        from die_shift_studio.reconstruction_events import associate,zone_key,color_for
        q=dict(layer='5/0',index=0,die=0,net=1,route_identity='zone',valid=True,
               regions=dict(A=box(10,10,20,20),B=box(20,10,40,20),C=box(-10,10,10,20)),
               nominal_regions=dict(A=box(10,0,20,10)),moving_terminals=box(-10,10,0,20))
        def violation(g,phase=None):return dict(kind='线距不足',geometry=g,net=('5',1),layers=('5/0',),phase=phase)
        outside=[violation(box(25,12,28,16)),violation(box(-8,12,-4,16)),violation(box(20.001,12,20.0015,14))]
        self.assertFalse(associate([q],outside,.001)[zone_key(q)])
        q['rule_witnesses']=associate([q],outside,.001)[zone_key(q)]
        bank=dict(zones={zone_key(q):dict(complete=True,has_failure=False)})
        self.assertEqual(color_for(q,bank,False,6),'green')
        before=violation(box(12,2,14,4),'original')
        q['rule_witnesses']=associate([q],[*outside,before],.001)[zone_key(q)]
        self.assertEqual(len(q['rule_witnesses']),1);self.assertEqual(color_for(q,None,False,6),'red')
        after=violation(box(12,12,14,14))
        self.assertEqual(len(associate([q],[after],.001)[zone_key(q)]),1)

    def test_fixed_via_can_cross_only_locally_empty_via_rows(self):
        from die_shift_studio.core import GDSData
        from die_shift_studio.stack import Connections
        def connected(extra=None,occupied=False):
            shapes={'5/0':[box(-60,-20,60,20)],'4/0':[Point(-50,0).buffer(5)],'6/0':[Point(50,0).buffer(5)]}
            roles={'5/0':'固定RDL','4/0':'运动过孔','6/0':'固定过孔'};order=['5/0','4/0','6/0']
            if occupied:shapes['4/0'].append(Point(50,0).buffer(5))
            if extra is not None:shapes['7/0']=[extra];roles['7/0']='运动RDL';order.insert(1,'7/0')
            d=GDSData('test.gds','test','TOP',['TOP'],.001,{k:[np.array(g.exterior.coords) for g in values] for k,values in shapes.items()},roles)
            return Connections(d,roles,order)
        linked=connected();self.assertEqual(linked.order,['5/0','4/0','6/0'])
        self.assertTrue(any({row['upper'],row['lower']}=={'5/0','6/0'} for row in linked.links))
        for other in (connected(occupied=True),connected(box(45,-10,55,10)),connected(box(200,0,210,10))):
            self.assertFalse(any({row['upper'],row['lower']}=={'5/0','6/0'} for row in other.links))

    def test_safe_original_pad_auto_ignores_but_manual_add_can_override(self):
        from die_shift_studio.core import build_case
        from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
        e,d,c=fixture();p=e.p;roles=e.roles;e.close()
        g=shapely.Polygon(d.layers['5/0'][0]).union(box(-150,-150,150,150))
        d.layers['5/0']=[np.array(g.exterior.coords)];d.sha256='large-pad-v41'
        c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0'])
        e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections'])
        try:
            e.configure_reconstruction(True,ViewSettings());f=e.frame(np.array([[20.,20.,.01]]))['reconstruction']
            self.assertFalse(f['pieces']);self.assertIn(('5/0',0),f['auto_ignored'])
            e.configure_reconstruction(True,ViewSettings(reconstruction_forced=(('5/0',0),)))
            self.assertTrue(e.frame(np.zeros((1,3)))['reconstruction']['pieces'])
        finally:e.close()

    def test_continuous_enclosure_circumscribes_rotations_and_xy_box(self):
        via=box(180,-8,200,12);center=(10.,20.);mean=(2.,-3.,.1);bounds=(15.,17.,.7)
        envelope=reachable_enclosure(via.wkb,center,mean,bounds,15.,.001)
        for angle in np.linspace(mean[2]-bounds[2],mean[2]+bounds[2],131):
            for x,y in ((-15,-17),(-15,17),(15,-17),(15,17)):
                moved=transform(via,center,np.array([mean[0]+x,mean[1]+y,angle])).buffer(15,quad_segs=64)
                self.assertTrue(envelope.covers(moved))
        self.assertLess(envelope.area,via.buffer(250).area)

    def test_automatic_ignore_requires_entire_source_to_enclose_each_via(self):
        e,_,_=fixture()
        try:
            r=e.reconstructor;via=r.vias[0];large=box(-200,-200,200,200)
            proof=enclosure_certificate(e.contact,e.p,large,[via]);self.assertTrue(proof['certified'])
            self.assertIsNone(enclosure_certificate(e.contact,e.p,via['poly'].buffer(10),[via]))
            holed=large.difference(box(18,-10,25,10))
            self.assertIsNone(enclosure_certificate(e.contact,e.p,holed,[via]))
        finally:e.close()

    def test_absolute_manual_angle_survives_drag_style_and_project(self):
        e,_,_=fixture()
        try:
            q=e.frame(np.zeros((1,3)))['reconstruction']['pieces'][0];plan=q['route_plan']
            q=dict(q,interval=(100.,500.))
            base=replace(e.reconstructor.settings,reconstruction_intervals=((*plan.key,plan.identity,100.,500.),))
            settings=rotate(base,q,0,.17)
            settings=ViewSettings.from_dict(json.loads(json.dumps(asdict(settings))))
            part=apply_angles(settings,plan,plan.partition(*q['interval']))
            self.assertAlmostEqual(part['angles'][0],.17)
            moved,_=plan.edit(part['a'],part['b'],0,part['a']+65)
            angled=apply_angles(settings,plan,moved)
            a=np.diff(np.asarray(part['near'].coords),axis=0)[0];b=np.diff(np.asarray(angled['near'].coords),axis=0)[0]
            self.assertAlmostEqual(abs(float(a@b))/(np.linalg.norm(a)*np.linalg.norm(b)),1.)
            for style in ('linear','c1','c2','arc'):
                e.configure_reconstruction(True,replace(settings,reconstruction_style=style))
                current=e.frame(np.array([[10.,10.,.01]]))['reconstruction']['pieces'][0]
                self.assertAlmostEqual(current['boundary_angles'][0],.17)
            self.assertFalse(rotate(settings,q,0,None).reconstruction_angles)
        finally:e.close()

    def test_serial_zones_share_motion_without_covering_each_other(self):
        e,_,_=fixture()
        try:
            cfg=replace(e.reconstructor.settings,reconstruction_added=(('5/0',0,300.,0.,'serial'),),reconstruction_forced=(('5/0',0),))
            for style in ('linear','c1','c2','arc'):
                e.configure_reconstruction(True,replace(cfg,reconstruction_style=style))
                f=e.frame(np.array([[20.,15.,.01]]))['reconstruction'];qs=f['pieces']
                self.assertEqual(len(qs),2);self.assertEqual(qs[0]['endpoint_weights'][1],qs[1]['endpoint_weights'][0])
                self.assertTrue(all(q['valid'] for q in qs),[(q['warning']) for q in qs])
                self.assertLess(qs[0]['regions']['A'].intersection(qs[1]['regions']['A']).area,.001)
                g=f['replacements']['5/0'][0]['geometry'];self.assertEqual(g.geom_type,'Polygon');self.assertTrue(g.is_valid)
                via=transform(e.reconstructor.vias[0]['poly'],e.bound.centers[0],f.get('states',np.array([[20.,15.,.01]]))[0])
                self.assertLess(via.difference(g).area,.001)
            deleted=(*key(qs[1]['route_plan']),)
            e.configure_reconstruction(True,replace(cfg,reconstruction_deleted=(deleted,)))
            f=e.frame(np.array([[10.,5.,0.]]))['reconstruction'];self.assertEqual(len(f['pieces']),1)
            self.assertEqual(f['pieces'][0]['endpoint_weights'],(1.,0.))
        finally:e.close()

    def test_curved_width_candidate_measured_by_native_edges(self):
        from die_shift_studio.reconstruction_route import warp
        from die_shift_studio.drc import rule_region
        e,_,_=fixture()
        try:
            q=e.frame(np.zeros((1,3)))['reconstruction']['pieces'][0];plan=q['route_plan'];part=plan.partition(100,400)
            for style in ('c1','c2','arc'):
                region,error=warp(part,plan,e.bound.centers[0],np.array([0.,90.,0.]),style,-1)
                self.assertIsNone(error,(style,error))
                copper=shapely.union_all(list(region.values()))
                # A native edge-pair check, not area/perimeter inference.
                self.assertTrue(rule_region(copper,29.99,.001).is_empty,style)
        finally:e.close()

    def test_export_only_borders_and_abc_public_names_exact_endpoints(self):
        import ezdxf
        source=box(0,0,100,30).difference(box(40,10,60,20))
        regions={'A':source.intersection(box(30,-1,70,31)),'B':source.intersection(box(70,-1,110,31)),'C':source.intersection(box(-1,-1,30,31))}
        snap=Snapshot({'5/0':(source.wkb,source.wkb),'4/0':(Point(5,5).buffer(3).wkb,),
                       '127/0':(box(-20,-20,120,50).wkb,),'128/0':(box(-5,-5,10,10).wkb,)},'TOP',None,0)
        roles={'5/0':'固定RDL','4/0':'运动过孔','127/0':'封装边框','128/0':'芯片边框'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'regions.dxf';result=write_regions(snap,{'pieces':[dict(layer='5/0',regions=regions)]},roles,path)
            model=ezdxf.readfile(path).modelspace();rows=list(model);names={e.dxf.layer for e in rows}
            self.assertEqual(names,{'L5_D0A','L5_D0B','L5_D0C','L127_D0','L128_D0'})
            for e in rows:
                self.assertFalse(e.closed)
                xy=np.array(e.get_points())[:,:2]
                if e.dxf.layer=='L5_D0B':self.assertLessEqual(xy[:,0].max(),30)
                if e.dxf.layer=='L5_D0C':self.assertGreaterEqual(xy[:,0].min(),70)
            lines=shapely.union_all([LineString(np.array(e.get_points())[:,:2]) for e in rows if e.dxf.layer.startswith('L5_')])
            self.assertLess(lines.hausdorff_distance(source.boundary),1e-12)
            self.assertTrue(result['topology']['L5_D0']['closed'])
            # Mutating one serialized node is rejected even if in-memory
            # contours had been closed and the DXF remains syntactically valid.
            text=path.read_text();text=text.replace('\n10\n30\n','\n10\n30.00001\n',1);path.write_text(text)
            with self.assertRaises(ValueError):verify_written_contours(path)

    def test_continuous_certificate_rejects_unbounded_manual_fields_and_changed_baselines(self):
        from test_v40 import make_engine
        from die_shift_studio.core import Parameters
        from die_shift_studio.reconstruction_probability import central_certificate
        shapes={'127/0':[box(-500,-400,1100,500)],'128/0':[box(-70,-70,70,70)],'3/0':[box(-20,-20,20,20)],
                '4/0':[Point(0,0).buffer(10)],'5/0':[box(-100,-50,700,50),box(-150,164.9,100,200)],'6/0':[Point(600,0).buffer(10)]}
        p=Parameters(single_bound_x_um=0,single_bound_y_um=100,bound_angle_deg=0,annulus_um=10,moving_annulus_um=5)
        e,_,_=make_engine(shapes,p)
        try:
            r=e.reconstructor;entries=r._plans_for_all_poses(lambda *_:None)
            self.assertTrue(central_certificate(r,entries[0],entries)['certified'])
            q=r.frame(np.zeros((1,3)))['pieces'][0]
            e.configure_reconstruction(True,rotate(r.settings,q,0,.05))
            r=e.reconstructor;entries=r._plans_for_all_poses(lambda *_:None)
            self.assertIsNone(central_certificate(r,entries[0],entries))
        finally:e.close()
        # A diagonal straight join removes rectangle corners even at zero.
        # The original rectangle's edge separation cannot certify this result.
        shapes={'127/0':[box(-500,-400,1100,500)],'128/0':[box(-100,-100,-60,-60)],
                '3/0':[Point(-80,-80).buffer(15)],'4/0':[Point(-80,-80).buffer(5)],
                '5/0':[box(-100,-100,100,100)],'6/0':[Point(80,80).buffer(5)]}
        cfg=ViewSettings(reconstruction_style='linear',reconstruction_forced=(('5/0',0),))
        e,_,_=make_engine(shapes,replace(p,single_bound_x_um=.01,single_bound_y_um=.01,annulus_um=2),cfg)
        try:
            r=e.reconstructor;entries=r._plans_for_all_poses(lambda *_:None);part,_=r.interval(entries[0])
            hull=shapely.MultiPoint(np.vstack([shapely.get_coordinates(part['near']),shapely.get_coordinates(part['far'])])).convex_hull
            self.assertGreater(hull.symmetric_difference(part['A']).area,10000)
            self.assertIsNone(central_certificate(r,entries[0],entries))
        finally:e.close()

    def test_new_project_fields_are_optional_and_roundtrip(self):
        cfg=ViewSettings.from_dict({})
        self.assertEqual(cfg.reconstruction_angles,());self.assertEqual(cfg.reconstruction_added,())
        cfg=replace(cfg,reconstruction_added=(('5/0',1,25.,30.,'id'),),reconstruction_forced=(('5/0',1),))
        self.assertEqual(ViewSettings.from_dict(json.loads(json.dumps(asdict(cfg)))),cfg)


if __name__=='__main__':unittest.main()
