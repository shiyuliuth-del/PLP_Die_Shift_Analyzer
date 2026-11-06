"""Export unions, stable mouths and weighted position/line probabilities."""
from dataclasses import replace
from itertools import product
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import math,tempfile,unittest,pickle
import numpy as np
import shapely,gdstk,ezdxf
from shapely.geometry import box,Polygon
from die_shift_studio.settings import ViewSettings
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.dxf_export import Snapshot,snapshot,write
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.core import read_gds
from die_shift_studio.width_analysis import WidthAnalyzer,ADEQUATE,Gate
from die_shift_studio.width_keepout import routing_violations
from die_shift_studio.width_event_ids import assign,edge_keys
from die_shift_studio.width_yield import measure_width_yield,packed_events,policy_yields
from die_shift_studio.width_yield_geometry import WidthEvents
from die_shift_studio.width_risk_probability import enrich,lookup,lookup_union,probability_text,probability_color
from test_v21 import example

def material(document):
    layers=defaultdict(list)
    for entity in document.modelspace():
        if entity.dxftype()=='LWPOLYLINE':
            assert entity.closed
            layers[entity.dxf.layer].append(Polygon(entity.get_points('xy')))
        else:
            assert entity.dxftype()=='HATCH'
            rings=[]
            for loop in entity.paths:
                assert all(edge.EDGE_TYPE=='LineEdge' for edge in loop.edges)
                rings.append([tuple(edge.start) for edge in loop.edges])
            layers[entity.dxf.layer].append(Polygon(rings[0],rings[1:]))
    return {key:shapely.union_all(values) for key,values in layers.items()}

class ExportTests(unittest.TestCase):
    def test_recursive_top_array_reflection_flatten_and_layer_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'hierarchy.gds';target=root/'current.dxf'
            lib=gdstk.Library(unit=1e-6,precision=1e-9)
            child=lib.new_cell('CHILD');child.add(gdstk.rectangle((0,0),(10,10),layer=5))
            middle=lib.new_cell('MIDDLE');middle.add(gdstk.Reference(child,origin=(3,4),rotation=math.pi/2,magnification=2,x_reflection=True))
            top=lib.new_cell('TOP');top.add(gdstk.Reference(middle,columns=2,rows=3,spacing=(16,17)))
            top.add(gdstk.rectangle((5,5),(20,20),layer=5),gdstk.rectangle((5,5),(20,20),layer=6))
            lib.write_gds(source);before=source.read_bytes()
            data=read_gds(source);cache=GeometryCache(data);frozen=snapshot(data,cache)
            result=write(frozen,target);document=ezdxf.readfile(target)
            self.assertFalse(document.audit().errors);self.assertEqual(document.units,13)
            self.assertFalse(list(document.modelspace().query('INSERT ARC CIRCLE')))
            shapes=material(document)
            for key in data.layers:
                expected=shapely.union_all(cache.layers[key]['geometry'])
                self.assertLess(expected.symmetric_difference(shapes['L'+key.replace('/','_D')]).area,1e-10)
            self.assertTrue(result['flattened'] and result['merge_layers'])
            self.assertLess(result['merged_polygons'],result['input_polygons'])
            self.assertEqual(before,source.read_bytes())

    def test_overlap_holes_scale_entities_and_fill_preferences(self):
        ring=box(0,0,20,20).difference(box(5,5,12,12));adjacent=box(15,0,30,15)
        frozen=Snapshot({'5/0':(ring.wkb,adjacent.wkb,adjacent.wkb)},'TOP',None,0)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'holes.dxf'
            for mode in ('outline','hatch'):
                for separate in (False,True):
                    options=DxfSettings(polygon_mode=mode,separate_holes=separate,show_fill=True,scale=2)
                    r=write(frozen,p,settings=options);doc=ezdxf.readfile(p);shapes=material(doc)
                    self.assertEqual(doc.header['$FILLMODE'],1)
                    actual=shapes['L5_D0'].difference(shapes.get('L5_D0_holes',Polygon()))
                    expected=shapely.transform(ring.union(adjacent),lambda xy:xy*2)
                    self.assertLess(actual.symmetric_difference(expected).area,1e-10)
                    self.assertEqual(r['merged_polygons'],1)
                    if mode=='hatch':self.assertTrue(all(e.dxftype()=='HATCH' for e in doc.modelspace()))
            r=write(frozen,p,settings=DxfSettings(merge_layers=False))
            self.assertEqual(r['polygons'],3);self.assertEqual(ezdxf.readfile(p).header['$FILLMODE'],0)
            with self.assertRaises(ValueError):DxfSettings.from_dict({'scale':0})

class ProbabilityTests(unittest.TestCase):
    def test_per_line_union_and_site_masks_are_not_added_or_conditioned_on_preview(self):
        a,b=('5/0',0),('5/0',1);c,d=('channel','A'),('channel','B')
        weights=np.array([.1,.2,.3,.4])
        bank=enrich(dict(method='cubature',total=4,
            chunks=(packed_events([set(),{a},{a,b},set()],[set(),set(),set(),{b}],weights),),
            site_chunks=({c:6,d:12},)))
        self.assertAlmostEqual(lookup(bank,'lines',a)['p'],.5)
        self.assertAlmostEqual(lookup(bank,'lines',b)['p'],.7)
        self.assertAlmostEqual(lookup(bank,'sites',c)['p'],.5)
        self.assertAlmostEqual(lookup_union(bank,(c,d))['p'],.9)
        self.assertAlmostEqual(policy_yields(bank,[])['failure_probability'][1],.9)
        self.assertEqual(lookup(pickle.loads(pickle.dumps(bank)),'lines',a),lookup(bank,'lines',a))
        q=enrich(dict(method='importance',total=2,weight_bound=2.,
          chunks=(packed_events([set(),{a}],[set(),set()],np.array([1.,1e-20])),),site_chunks=({c:2},)))
        self.assertIn('e-',probability_text(lookup(q,'lines',a),6))
        self.assertIn('样本未命中',probability_text(lookup(q,'sites',('missing',)),3))

    def test_grid_site_and_line_probabilities_equal_every_full_frame(self):
        _,_,case,p,e=example('示例O_运动网络三线路通道')
        p=replace(p,multi_bound_x_um=35,multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=2)
        cfg=replace(ViewSettings(),width_grid_nodes=5)
        a=WidthAnalyzer(e.contact,p,cfg);a.probability=None
        try:
            bank=measure_width_yield(e.contact,p,cfg,None,lambda *_:None);grid=bank['grid']
            lines=defaultdict(float);sites=defaultdict(float)
            for digits in product(*(range(v) for v in grid.radices)):
                mass=math.prod(w[d] for w,d in zip(grid.weights,digits))
                if not mass:continue
                states=np.array([v[d] for v,d in zip(grid.locals,digits)])
                frame=a.frame({'states':states});lr=set();sr=set()
                for row in frame['rows']:
                    if row['status']!=ADEQUATE:lr.update(row['refs']);sr.add(row['probability_key'])
                for row in routing_violations(e.contact,states,p,cfg):
                    lr.update((row['layer'],i) for i in row['indices']);sr.update(edge_keys(row))
                for key in lr:lines[key]+=mass
                for key in sr:sites[key]+=mass
            self.assertTrue(lines and sites)
            self.assertEqual(set(lines),set(bank['probability_lines']))
            self.assertEqual(set(sites),set(bank['probability_sites']))
            for kind,expected in (('lines',lines),('sites',sites)):
                for key,mass in expected.items():self.assertAlmostEqual(lookup(bank,kind,key)['p'],mass,places=13)
        finally:a.close();e.close()

    def test_sample_site_masks_match_full_geometry_with_keepout(self):
        from test_v34 import fixture
        e=fixture(True);cfg=ViewSettings();a=WidthAnalyzer(e.contact,e.p,cfg);a.probability=None
        try:
            events=WidthEvents(a,e.p,lambda *_:None)
            states=np.array([[[x,0,0]] for x in (-50,-40,-2,0,20,40,41,46,50)])
            red,yellow,masks=events.classify(states,with_sites=True)
            for i,z in enumerate(states):
                frame=a.frame({'states':z});expected={r['probability_key'] for r in frame['rows'] if r['status']!=ADEQUATE}
                for row in routing_violations(e.contact,z,e.p,cfg):expected.update(edge_keys(row))
                actual={key for key,mask in masks.items() if mask>>i&1}
                self.assertEqual(actual,expected)
        finally:a.close();e.close()

    def test_nearby_stations_share_a_mouth_but_disconnected_openings_do_not(self):
        def gate(y,band):
            return Gate((0,1),(0,1),np.array([[0.,y],[100.,y]]),False,(15.,),(50.,),((('5/0',1),),),100.,band)
        gates=[gate(0,(0,0,0)),gate(10,(0,0,1)),gate(1000,(0,0,8))]
        plane=dict(gates=gates,walls=[SimpleNamespace(name='A'),SimpleNamespace(name='B')])
        assign('5',plane,20,1e-9)
        self.assertEqual(gates[0].event_key,gates[1].event_key)
        self.assertNotEqual(gates[0].event_key,gates[2].event_key)
        self.assertEqual(replace(gates[0],pair=(3,4)).event_key,gates[0].event_key)

    def test_defaults_migration_precision_and_fixed_alpha_rgb_rule(self):
        from die_shift_studio.project import normalize_project
        self.assertEqual(ViewSettings().width_yield_method,'grid')
        self.assertTrue(ViewSettings().width_show_probability)
        self.assertEqual(ViewSettings.from_dict({'width_yield_method':'importance'}).width_yield_method,'importance')
        p=normalize_project(dict(schema_version=5,expanded_settings=['宽度分析 · 良率计算','宽度分析 · 元素显示']))
        self.assertEqual(p['expanded_settings'],['宽度分析'])
        self.assertTrue(p['dxf_options']['merge_layers']);self.assertEqual(p['dxf_options']['scale'],1)
        p['settings']['width_show_probability']=False;p['dxf_options']['scale']=2
        self.assertFalse(normalize_project(p)['settings']['width_show_probability'])
        self.assertEqual(normalize_project(p)['dxf_options']['scale'],2)
        rgb=np.array([probability_color('#c44956',dict(p=v)) for v in (0,1e-15,1e-9,1e-3,1)])
        self.assertTrue(np.all(np.diff(rgb@np.array([.2126,.7152,.0722]))<0))
        self.assertEqual(probability_text(dict(p=.123456789,success=.876543211,method='grid'),3),'≈12.346%')

if __name__=='__main__':unittest.main()
