"""V38 contracts: shared nominal regions, independent handles and open DXF joins."""
from dataclasses import replace,asdict
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import shapely
from shapely.geometry import box,Point,LineString
from die_shift_studio.core import Parameters,build_case,GDSData
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.reconstruction_route import RoutePlan,warp
from die_shift_studio.reconstruction import transform
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.dxf_export import snapshot,Snapshot
from die_shift_studio.region_export import contours,write_regions
from die_shift_studio.project import project_overlays,normalize_project


def fixture(bent=False):
    route=LineString([(0,0),(250,0),(250,300)]) if bent else LineString([(0,0),(600,0)])
    source=route.buffer(15,cap_style=1,join_style=1).union(Point(0,0).buffer(30)).union(Point(route.coords[-1]).buffer(30))
    end=route.coords[-1]
    shapes={'127/0':[box(-200,-250,850,550)],'128/0':[box(-100,-180,100,180)],
            '3/0':[box(-20,-20,20,20)],'4/0':[Point(0,0).buffer(10)],'5/0':[source],
            '6/0':[Point(end).buffer(10)]}
    roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
    data=GDSData('synthetic.gds','v38','TOP',['TOP'],.001,{k:[np.asarray(g.exterior.coords) for g in gs] for k,gs in shapes.items()},roles)
    case=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters(single_bound_x_um=20,single_bound_y_um=20,bound_angle_deg=1,annulus_um=10,moving_annulus_um=5)
    e=PreviewEngine(GeometryCache(data),case,roles,{},p,None,case['connections']);e.configure_reconstruction(True,ViewSettings())
    return e,data,case


class V38Tests(unittest.TestCase):
    def test_longest_straight_and_nominal_partition_closes(self):
        e,d,c=fixture()
        try:
            r=e.reconstructor.frame(np.zeros((1,3)));q=r['pieces'][0]
            self.assertGreater(q['interval'][1]-q['interval'][0],530)
            regs=q['nominal_regions'];src=e.contact.cache.layers['5/0']['geometry'][0]
            self.assertLess(shapely.union_all(list(regs.values())).symmetric_difference(src).area,1e-6)
            for a,b in ('AB','AC','BC'):self.assertLess(regs[a].intersection(regs[b]).area,1e-7)
            self.assertTrue(regs['C'].covers(e.contact.cache.layers['4/0']['geometry'][0]))
            self.assertTrue(regs['B'].covers(e.contact.cache.layers['6/0']['geometry'][0]))
        finally:e.close()

    def test_every_extreme_uses_same_nominal_endpoints_and_zero_cache(self):
        from die_shift_studio.statistics import ExtremeCatalog
        e,d,c=fixture()
        try:
            first=e.reconstructor.frame(np.zeros((1,3)));key=first['pieces'][0]['interval']
            for pose in ExtremeCatalog(c,e.p).locals[0]:
                frame=e.reconstructor.frame(pose[None,:]);q=frame['pieces'][0]
                self.assertEqual(q['interval'],key);self.assertEqual(q['route_identity'],first['pieces'][0]['route_identity'])
                self.assertTrue(q['valid'],q['warning'])
                via=transform(e.contact.cache.layers['4/0']['geometry'][0],e.bound.centers[0],pose)
                self.assertLess(via.difference(frame['replacements']['5/0'][0]['geometry']).area,.001)
            self.assertIs(e.reconstructor.frame(np.zeros((1,3))),first)
        finally:e.close()

    def test_two_edits_are_independent_shared_and_persisted(self):
        e,d,c=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan'];a,b=q['interval']
            settings,part=e.reconstructor.edited(q,0,plan.route.interpolate(a+50).coords[0])
            self.assertEqual(part['b'],b)
            e.configure_reconstruction(True,settings)
            for pose in ([20,15,.01],[-20,-15,-.01],[0,0,0]):
                actual=e.reconstructor.frame(np.array([pose]))['pieces'][0]
                self.assertEqual(actual['interval'],(part['a'],b))
            updated,part2=e.reconstructor.edited(actual,1,plan.route.interpolate(b-30).coords[0])
            self.assertEqual(part2['a'],part['a'])
            self.assertEqual(ViewSettings.from_dict(json.loads(json.dumps(asdict(updated)))).reconstruction_intervals,updated.reconstruction_intervals)
            self.assertIs(e.reconstructor._plans[0]['plan'],plan)
        finally:e.close()

    def test_cross_corner_snap_and_no_endpoint_exchange(self):
        source=LineString([(0,0),(200,0),(200,250)]).buffer(15,cap_style=1,join_style=1)
        plan=RoutePlan('5/0',0,0,(('4/0',0),),source,LineString([(0,0),(200,0),(200,250)]),Point(0,0).buffer(10),(Point(200,250).buffer(10),),.001,3)
        first=plan.partition(50,400);self.assertIsNotNone(first)
        part,clamped=plan.edit(50,400,0,220)
        self.assertIsNotNone(part);self.assertGreater(part['a'],200);self.assertEqual(part['b'],400)
        snapped=plan.snap(201,0,400);self.assertGreaterEqual(abs(snapped-200),9.7)
        part,clamped=plan.edit(50,400,0,500)
        self.assertLess(part['a'],part['b']);self.assertEqual(part['b'],400);self.assertTrue(clamped)

    def test_whole_landing_point_is_reversible(self):
        e,d,c=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan']
            cfg,part=e.reconstructor.edited(q,0,plan.route.coords[0]);self.assertEqual(part['a'],0)
            e.configure_reconstruction(True,cfg);f=e.reconstructor.frame(np.array([[20,15,.01]]));q=f['pieces'][0]
            self.assertTrue(q['whole_landing']);self.assertEqual(q['moved_cut'].geom_type,'Point');self.assertTrue(q['regions']['C'].is_empty)
            self.assertTrue(q['valid'],q['warning'])
            cfg2,part2=e.reconstructor.edited(q,0,plan.route.interpolate(100).coords[0]);self.assertGreater(part2['a'],0);self.assertEqual(part2['b'],part['b'])
        finally:e.close()

    def test_pivot_does_not_select_chip_frame(self):
        e,d,c=fixture()
        try:
            e.reconstructor.model.bound.centers[0]=[5000,4000]
            self.assertTrue(e.reconstructor._own_frame(0).equals(c['dies'][0]))
        finally:e.close()

    def test_open_abc_dxf_contains_only_complete_physical_boundary(self):
        e,d,c=fixture()
        try:
            f=e.frame(np.array([[20,15,.01]]));snap=snapshot(d,e.bound.cache,f,reconstruction=True)
            shapes,_,_=contours(snap,f['reconstruction'],e.roles,DxfSettings())
            abc=[LineString(xy) for name,xy in shapes if name.startswith('L5_D0')]
            source=f['reconstruction']['replacements']['5/0'][0]['geometry']
            self.assertLess(shapely.union_all(abc).symmetric_difference(source.boundary).length,1e-6)
            self.assertEqual({n for n,_ in shapes if n.startswith('L5_D0')},{'L5_D0A','L5_D0B','L5_D0C'})
            self.assertFalse(any(g.is_ring for g in abc))
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'区.dxf';write_regions(snap,f['reconstruction'],e.roles,out)
                pairs=out.read_text().splitlines();self.assertNotIn('HATCH',pairs)
                for i in range(0,len(pairs)-1,2):
                    if pairs[i]=='70' and i>0:pass
                import ezdxf
                doc=ezdxf.readfile(out);entities=list(doc.modelspace())
                self.assertTrue(all(q.dxftype()=='LWPOLYLINE' and not q.closed for q in entities))
        finally:e.close()

    def test_holes_scale_merge_and_failed_export_preserves_file(self):
        poly=box(0,0,100,100).difference(box(20,20,40,40));snap=Snapshot({'5/0':(poly.wkb,)},'T',None,0)
        cfg=DxfSettings(scale=2,separate_holes=True,polygon_mode='hatch',show_fill=True)
        shapes,_,_=contours(snap,{'pieces':[]},{'5/0':'固定RDL'},cfg)
        self.assertEqual({k for k,_ in shapes},{'L5_D0B','L5_D0B_holes'})
        self.assertLess(shapely.union_all([LineString(xy) for _,xy in shapes]).symmetric_difference(shapely.transform(poly,lambda a:a*2).boundary).length,1e-8)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'kept.dxf';out.write_bytes(b'previous')
            bad=Snapshot({'5/0':(LineString([(0,0),(1,1)]).wkb,)},'T',None,0)
            with self.assertRaises(ValueError):write_regions(bad,{}, {},out)
            self.assertEqual(out.read_bytes(),b'previous')

    def test_two_enclosures_follow_receiving_rdl_and_not_visibility(self):
        from die_shift_studio.drc import GlobalDRC
        e,d,c=fixture()
        try:
            base=GlobalDRC(e.contact,e.p).frame(np.zeros((1,3)),settings=ViewSettings())
            self.assertFalse(any(q['kind']=='孔盘包围不足' for q in base))
            changed=replace(e.p,moving_annulus_um=30)
            marks=GlobalDRC(e.contact,changed).frame(np.zeros((1,3)),settings=ViewSettings())
            missing=[q for q in marks if q['kind']=='孔盘包围不足']
            self.assertTrue(missing)
            self.assertTrue(all(q['rdl_role']=='运动RDL' and q['required_um']==30 for q in missing))
            fixed=replace(e.p,annulus_um=40)
            marks=GlobalDRC(e.contact,fixed).frame(np.zeros((1,3)),settings=ViewSettings())
            self.assertTrue(any(q.get('rdl_role')=='固定RDL' and q['required_um']==40 for q in marks))
            old=e.contact.current(np.array([[20,0,0]]),e.p)
            new=e.contact.current(np.array([[20,0,0]]),changed)
            self.assertEqual(old['issues'],new['issues'])
        finally:e.close()

    def test_large_shift_never_outputs_invalid_or_disconnected_copper(self):
        e,d,c=fixture(bent=True)
        try:
            for pose in ((400,400,1.2),(-400,-400,-1.2),(20,20,.02)):
                frame=e.reconstructor.frame(np.asarray([pose]))
                for row in frame['replacements']['5/0']:
                    self.assertTrue(row['geometry'].is_valid)
                    self.assertEqual(row['geometry'].geom_type,'Polygon')
                for q in frame['pieces']:
                    if not q['valid']:
                        self.assertTrue(q['warning'])
                        self.assertTrue(q['regions']['B'].equals(q['route_plan'].source))
        finally:e.close()

    def test_legacy_defaults_and_retired_preview_cannot_reactivate(self):
        p=Parameters.from_dict({'annulus_um':21},require_rdl=False);self.assertEqual(p.moving_annulus_um,21)
        cfg=ViewSettings.from_dict({'deformation_iterations':64,'deformation_min_angle_deg':45})
        self.assertFalse(hasattr(cfg,'deformation_iterations'));self.assertEqual(cfg.drc_min_angle_deg,45)
        self.assertFalse(cfg.show_contact_markers or cfg.show_low_contact);self.assertTrue(cfg.reconstruction_live)
        saved=project_overlays({'overlays':{'probability':True,'footprint':False,'deformation':True}})
        self.assertTrue(saved['footprint']);self.assertNotIn('deformation',saved);self.assertNotIn('probability',saved)
        e,_,_=fixture()
        try:
            e.result={};e.configure_deformation(True,cfg);self.assertIsNone(e.deformer)
            self.assertNotIn('deformation',e.frame(np.zeros((1,3))))
        finally:e.close()

if __name__=='__main__':unittest.main()
