"""Independent GDS, geometry edit, adjacent-pad and edge-clearance regressions."""
from pathlib import Path
import tempfile
import unittest
import json
import math
import gdstk
import numpy as np
import shapely
from shapely.geometry import box, Point, LineString
from die_shift_studio.core import read_gds,Parameters
from die_shift_studio.gds_shapes import edited_snapshot,shape_nodes,node_fingerprint,build_outline
from die_shift_studio.width_analysis import Wall,Gate,capacity,BLOCKED
from die_shift_studio.neighbor_channels import occupied,local_openings
from die_shift_studio.landing_geometry import embedded_pad
from die_shift_studio.settings import ViewSettings,DEFAULTS
from die_shift_studio.edge_clearance import edge_violations
from die_shift_studio.project import normalize_project
from tools.create_path_example import patch_paths
from tests.test_v21 import example


class V24Tests(unittest.TestCase):
    def test_real_single_xy_paths_arrays_absolute_width_and_zero_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'nested.gds'
            lib=gdstk.Library(unit=1.e-6,precision=1.e-9)
            top=lib.new_cell('TOP');cell=lib.new_cell('CHILD')
            for layer in (4,5,77,78):
                cell.add(gdstk.FlexPath([(5.,7.),(15.,7.)],10,simple_path=True,ends='extended',layer=layer,datatype=2))
            top.add(gdstk.Reference(cell,(100.,200.),magnification=2,rotation=math.pi/2,x_reflection=True,
                                    columns=2,rows=2,spacing=(50.,60.)))
            lib.write_gds(path);patch_paths(path,{4,5,78},{77,78},{5})
            original=path.read_bytes()
            data=read_gds(path)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(set(data.layers),{'4/2','5/2'})
            self.assertEqual(len(data.import_warnings),2)
            self.assertTrue(all('4 个零宽 PATH' in w for w in data.import_warnings))
            for key,diameter in [('4/2',20),('5/2',10)]:
                self.assertEqual(len(data.layers[key]),4)
                centres=[]
                for i,poly in enumerate(data.layers[key]):
                    kind,xy,meta=shape_nodes(data,key,i);self.assertEqual(kind,'单点 PATH 圆')
                    self.assertEqual(meta['width'],diameter);centres.append(xy[0])
                    np.testing.assert_allclose(np.ptp(poly,axis=0),(diameter,diameter),atol=1.e-8)
                    self.assertAlmostEqual(shapely.Polygon(poly).area,math.pi*(diameter/2)**2,delta=.002*math.pi*(diameter/2)**2)
                self.assertTrue(any(np.allclose(p,[114.,210.]) for p in centres))
                self.assertEqual(len(set(tuple(x) for x in np.round(centres,4))),4)

    def test_mixed_polygon_path_order_and_native_centreline_after_hierarchy(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'mixed.gds';lib=gdstk.Library()
            a=lib.new_cell('A');t=lib.new_cell('TOP')
            for c,x in [(a,0),(t,100)]:
                c.add(gdstk.rectangle((x,0),(x+5,5),layer=3),gdstk.FlexPath([(x,20),(x+10,20)],2,simple_path=True,layer=3))
            t.add(gdstk.Reference(a,(10,10),columns=2,rows=1,spacing=(30,0)))
            lib.write_gds(p)
            old=gdstk.read_gds(p,unit=1.e-6).top_level()[0].get_polygons()
            new=read_gds(p)
            for i,(g,xy) in enumerate(zip(old,new.layers['3/0'],strict=True)):
                np.testing.assert_allclose(g.points,xy)
                if shape_nodes(new,'3/0',i)[2]:
                    center=LineString(shape_nodes(new,'3/0',i)[1])
                    self.assertTrue(shapely.Polygon(xy).covers(center))

    def test_node_edits_are_transactional_bound_to_source_and_reversible(self):
        d,_,_,_,e=example('示例P_单点PATH与相邻孔盘');e.close()
        ref=('4/0',0);kind,xy,meta=shape_nodes(d,*ref);old=d.layers['4/0'][0].copy()
        edit=dict(layer=ref[0],index=0,original=node_fingerprint(kind,xy),nodes=[[-84.,1.]])
        changed=edited_snapshot(d,[edit]);self.assertNotEqual(changed.geometry_id,d.geometry_id)
        self.assertEqual(changed.sha256,d.sha256)
        np.testing.assert_allclose(changed.layers['4/0'][0],old+[1,1])
        self.assertIs(edited_snapshot(changed,[]),d)
        path=edited_snapshot(changed,[dict(edit,nodes=[[-84,1],[-80,8]])])
        self.assertEqual(shape_nodes(path,*ref)[0],'PATH')
        for invalid in [dict(edit,original='wrong'),dict(edit,nodes=[]),dict(edit,nodes=[[1,float('nan')]])]:
            with self.assertRaises(ValueError):edited_snapshot(changed,[invalid])
        with self.assertRaises(ValueError):build_outline([[0,0],[10,10],[0,10],[10,0]],None,.001)
        np.testing.assert_allclose(d.layers['4/0'][0],old)

    def test_full_rigid_projection_even_when_centreline_misses_pad(self):
        # 120 um opening. The circle enters the rectangle at y=10 but misses
        # its centreline. Reserve the full 30 um diameter, not a 20 um chord.
        pads=[box(-5,-10,0,10),box(120,-10,125,10),Point(65,25).buffer(15,quad_segs=32)]
        walls=[Wall(str(i),'运动过孔孔盘',g,(('4/0',i),),contacts=frozenset({('5/0',1)}) if i==2 else frozenset()) for i,g in enumerate(pads)]
        ends=np.array([[[0.,0.],[120.,0.]]]);spans=occupied(ends,np.array([[0,1]]),walls,np.array(pads,object),19.5,1.e-8)[0]
        self.assertEqual(spans,[(2,50.,80.)])
        gate=Gate((0,1),(0,1),ends[0],False,(15.,30.,15.),(20.,65.,102.),((('5/0',0),),(('5/0',1),),(('5/0',2),)),120.)
        rows=list(local_openings(gate,ends[0],spans,spans,walls,1.e-8))
        self.assertEqual([(r['pair'],r['indices'],r['gap'],r['locked']) for r in rows],[((0,2),[0],50.,1),((2,1),[2],40.,1)])
        self.assertEqual([capacity(r['gap'],15,15) for r in rows],[1,0])

    def test_embedded_round_and_rectangular_landings_keep_real_footprint(self):
        via=Point(0,0).buffer(8,quad_segs=32);rule=via.buffer(5)
        for pad in [Point(0,0).buffer(30,quad_segs=32),box(-30,-30,30,30)]:
            copper=pad.union(box(-4,0,4,95))
            real=embedded_pad(via,copper,rule,.001)
            np.testing.assert_allclose(real.bounds,(-30,-30,30,30),atol=.005)
            self.assertFalse(real.covers(Point(0,70)))
        strip=box(-8,-100,8,100)
        self.assertTrue(embedded_pad(via,strip,rule,.001).equals(rule))

    def test_fixed_and_moving_edges_equality_outside_holes_and_replacements(self):
        from types import SimpleNamespace
        board=box(0,0,200,200).difference(box(90,90,110,110))
        g=[box(15,40,25,54),box(5,60,30,70),box(80,93,85,100),box(-50,42,-40,48)]
        model=SimpleNamespace(case={'board':board},data=SimpleNamespace(dbu_um=.001),
                  roles={'5/0':'固定RDL','3/0':'运动RDL'},cache=SimpleNamespace(layers={'5/0':dict(geometry=g),'3/0':dict(geometry=[box(25,20,30,25)])}),
                  nodes=[dict(net=i,owner=-1 if i<4 else 0) for i in range(5)],
                  ids={**{('5/0',i):i for i in range(4)},('3/0',0):4})
        from shapely.affinity import translate
        model.moved=lambda poly,owner,states:translate(poly,*states[owner,:2])
        p=Parameters(line_space_um=15,wafer_line_space_um=20)
        result=edge_violations(model,np.zeros((1,3)),p,ViewSettings())
        self.assertEqual([q['indices'][0] for q in result],[1,2,3])
        self.assertEqual([q['gap_um'] for q in result],[5.,5.,0.])
        replaced={'5/0':[dict(indices=[0],geometry=box(10,40,25,54))]}
        result=edge_violations(model,np.array([[-9.,0,0]]),p,ViewSettings(),replaced)
        self.assertEqual(sorted((q['layer'],q['gap_um']) for q in result),[('3/0',16.),('5/0',0.),('5/0',5.),('5/0',5.),('5/0',10.)])

    def test_normal_preview_checks_copper_to_edge_without_a_display_toggle(self):
        d,_,_,_,engine=example('示例P_单点PATH与相邻孔盘')
        try:
            states=np.zeros((1,3));plain=engine.frame(states)
            self.assertEqual([(v['layer'],v['gap_um']) for v in plain['edge_clearance']],[('5/0',5.)])
            self.assertIn('封装边缘线距不足',[q['kind'] for q in plain['checks'][1]['failures']])
            engine.configure_width(True,ViewSettings());rich=engine.frame(states)
            self.assertEqual(plain['checks'],rich['checks'])
            self.assertEqual(rich['width_analysis']['edge_violations'][0]['gap_um'],5.)
            self.assertTrue(any(r['split'] and r['locked']==1 for r in rich['width_analysis']['rows']))
        finally:engine.close()

    def test_board_fragments_preserve_the_internal_package_edge(self):
        from die_shift_studio.core import GDSData,build_case
        pieces=[box(-200,-150,-30,150),box(30,-150,200,150),box(-30,-150,30,-25),box(-30,25,30,150)]
        dies=[box(-160,-60,-80,60)]
        layers={k:[np.asarray(p.exterior.coords) for p in v] for k,v in [('9/0',pieces),('8/0',dies),('4/0',[box(-130,-5,-120,5)]),('5/0',[box(-135,-10,-115,10)])]}
        roles={'9/0':'封装边框','8/0':'芯片边框','4/0':'运动过孔','5/0':'固定RDL'}
        case=build_case(GDSData('g','hash','top',['top'],.001,layers),roles)
        self.assertEqual(len(case['board'].interiors),1)
        self.assertFalse(case['board'].covers(Point(0,0)))

    def test_new_modes_have_defaults_and_do_not_enter_numeric_cache_keys(self):
        legacy=normalize_project(dict(schema_version=1))
        self.assertEqual(legacy['settings']['width_display'],DEFAULTS)
        self.assertTrue(legacy['settings']['animation_return_zero'])
        self.assertFalse(ViewSettings.from_dict({'animation_return_zero':False}).animation_return_zero)
        a=ViewSettings();b=ViewSettings.from_dict(dict(width_display={'固定RDL':'hide','边距':'dim'}))
        self.assertEqual(a.width_key(),b.width_key());self.assertEqual(a.numeric_key(),b.numeric_key())
        self.assertEqual(b.width_display['运动RDL'],'dim')
        with self.assertRaises(ValueError):ViewSettings.from_dict({'width_display':{'截面':'unknown'}})
