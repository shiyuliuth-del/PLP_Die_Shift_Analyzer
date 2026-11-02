"""Independent geometry and project-contract regressions for fixed-RDL channels."""
from dataclasses import asdict,replace
from pathlib import Path
from unittest.mock import patch
import math
import unittest
import numpy as np
import shapely
from shapely.geometry import box
from shapely.strtree import STRtree
from die_shift_studio.core import Parameters,GDSData,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.project import normalize_project,OVERLAY_DEFAULTS
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import capacity,WidthAnalyzer,_crossings,holds_original,gate_rectangle,_intruders,ADEQUATE,NARROW,BLOCKED
from tests.test_v21 import example

CASE='示例O_运动网络三线路通道'


def frame_at(engine,dx=0,dy=0,angle=0):
    states=np.zeros((len(engine.contact.case['dies']),3))
    states[0]=[dx,dy,angle]
    return engine.frame(states)['width_analysis']


def central(data):
    return [r for r in data['rows'] if r['pair']==('运动过孔孔盘 4/0 #1','运动过孔孔盘 4/0 #2')]


class ChannelTests(unittest.TestCase):
    def test_capacity_equality_and_both_side_clearances(self):
        self.assertEqual([capacity(x,15,15) for x in (0,30,44.9999,45,104.9999,105,110,145)], [0,0,0,1,2,3,3,4])
        with self.assertRaises(ValueError):capacity(100,15,0)

    def test_three_routes_keep_their_nominal_assignment_after_pad_covers_them(self):
        _,_,_,_,engine=example(CASE)
        try:
            engine.configure_width(True,ViewSettings())
            for dx,gap,current in [(0,110,3),(3,107,3),(35,75,2),(-35,145,4),(80,30,0),(140,0,0),(300,0,0)]:
                rows=central(frame_at(engine,dx))
                self.assertTrue(rows)
                for r in rows:
                    self.assertEqual((r['original'],r['required'],r['current']),(3,3,current))
                    self.assertAlmostEqual(sum(r['slots_um']),gap)
                    self.assertEqual(r['status'],BLOCKED if current<3 else NARROW if dx==3 else ADEQUATE)
                    self.assertEqual(r['widths_um'],(20.,15.,15.))
                    self.assertEqual((r['minimum_um'],r['keep_um']),(105,110))
                    self.assertEqual(len(r['refs']),3)
                    if current<3:self.assertGreater(gate_rectangle(r,15,15).area,0)
            with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('cache miss')):
                self.assertEqual(central(frame_at(engine))[0]['current'],3)
                self.assertEqual(central(frame_at(engine,35))[0]['current'],2)
        finally:engine.close()

    def test_fixed_only_traces_are_whole_obstacles_and_have_no_demand(self):
        _,_,_,_,engine=example('示例N_三线路窄通道宽度')
        try:
            engine.configure_width(True,ViewSettings())
            f=frame_at(engine,35)
            self.assertFalse(f['rows'])
            for x in (-30,0,30):
                strip=box(x-7.5,-500,x+7.5,500)
                self.assertTrue(any(o['kind']=='固定障碍' and o['geometry'].covers(strip) for o in f['retained']))
            self.assertEqual(f['planes'],['5'])
        finally:engine.close()

    def test_fixed_pad_to_boundaries_and_intruding_third_pad(self):
        _,_,_,_,engine=example(CASE)
        try:
            engine.configure_width(True,ViewSettings())
            nominal=frame_at(engine)
            self.assertTrue(any('边框' in r['ends'] for r in nominal['rows']))
            self.assertTrue(any(o['kind']=='固定过孔孔盘' for o in nominal['retained']))
            after=frame_at(engine,35)
            split=[r for r in after['rows'] if len(r['slots_um'])>1]
            self.assertTrue(split)
            for r in split:
                self.assertEqual(r['current'],sum(capacity(g,15,15) for g in r['slots_um']))
                self.assertTrue(r['split'])
                self.assertEqual(r['status']==BLOCKED,r['current']<r['required'])
        finally:engine.close()

    def test_edge_throat_closes_and_does_not_reopen_outside_board(self):
        def polys(*bounds):return [np.asarray(box(*b).exterior.coords) for b in bounds]
        roles={'9/0':'封装边框','8/0':'芯片边框','5/0':'固定RDL','4/0':'运动过孔'}
        layers={'9/0':polys((-500,-500,500,500)),'8/0':polys((-425,-425,425,425)),
                '4/0':polys((380,-10,400,10),(438,200,452,214)),
                '5/0':polys((375,-15,405,15),(438,-200,452,214))}
        d=GDSData('edge.gds','edge','TOP',['TOP'],.001,layers,roles)
        c=build_case(d,roles,layer_order=['4/0','5/0','9/0','8/0'])
        e=PreviewEngine(GeometryCache(d),c,roles,{},Parameters(),None,c['connections'])
        try:
            e.configure_width(True,ViewSettings())
            for dx,expected in [(0,2),(60,0),(180,0)]:
                selected=[r for r in frame_at(e,dx)['rows'] if r['pair'][0].endswith('#1') and '边框' in r['ends'] and r['nominal_gap_um']==85]
                self.assertTrue(selected)
                self.assertTrue(all(r['required']==1 and r['current']==expected for r in selected))
        finally:e.close()

    def test_same_net_crosses_twice_and_tangent_is_not_a_route(self):
        turn=shapely.union_all([box(-18,-20,-12,30),box(12,-20,18,30),box(-18,24,18,30)])
        touch=box(24,0,30,15)
        geoms=np.array([turn,touch],object)
        plane=dict(routes=geoms,tree=STRtree(geoms),boundaries=shapely.boundary(geoms),route_sources=[[ (('5/0',i),g)] for i,g in enumerate(geoms)],width=6.)
        answer=_crossings(np.array([[[-40,0],[40,0]]]),plane,1.e-8)[0]
        self.assertEqual([r[1:] for r in answer],[(6.,(('5/0',0),)),(6.,(('5/0',0),))])

    def test_parallel_trace_has_zero_demand_but_a_bent_through_route_counts(self):
        flat=box(-28,-3,28,3)
        bend=shapely.union_all([flat,box(-28,-25,-22,2),box(22,-2,28,25)])
        for trace,n in ((flat,0),(bend,1)):
            plane=dict(routes=np.array([trace],object),boundaries=np.array([trace.boundary],object),
                       tree=STRtree([trace]),route_sources=[[(('5/0',0),trace)]],width=6)
            cut=np.array([[[-40,0],[40,0]]])
            self.assertEqual(len(_crossings(cut,plane,1.e-8)[0]),n)

    def test_tolerance_and_enclosure_are_independent(self):
        d,q,c,p,e=example(CASE)
        e.close()
        p=replace(p,annulus_um=25)
        e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections'])
        try:
            e.configure_width(True,ViewSettings(drc_tolerance_um=3))
            a=frame_at(e)
            self.assertEqual(a['parameters'],dict(width_um=12,space_um=12,enclosure_um=25))
            r=central(a)[0]
            self.assertEqual((r['nominal_gap_um'],r['original'],r['required']),(90,3,3))
        finally:e.close()

    def test_external_pad_owner_and_custom_pivot_match_display(self):
        d,q,c,p,e=example(CASE)
        e.close()
        p=replace(p,per_die_enabled=True,die_bounds=[[35,35,1],[35,35,1]],die_pivots=[['custom',35,70],['top_left',0,0]])
        e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,None,c['connections'])
        try:
            e.configure_width(True,ViewSettings())
            states=np.array([[35,-35,math.pi/180],[-35,35,-math.pi/180]])
            a=e.frame(states)
            for i in (0,1):
                wall=next(o for o in a['width_analysis']['retained'] if o['refs']==(('4/0',i),))
                center=a['layers']['4/0']['centers'][i]
                np.testing.assert_allclose(wall['geometry'].centroid.coords[0],center,atol=1.e-8)
        finally:e.close()

    def test_concave_fixed_island_checks_both_sides_of_its_opening(self):
        # The left limb is closest to the central pad. A route through the right
        # limb is invisible to a single global shortest-line test.
        island=shapely.union_all([box(-100,-85,-80,100),box(110,-85,130,100),box(-100,80,130,100)])
        fixed=[island,box(-20,-15,20,15),box(-54,-180,-46,42),box(56,-180,64,42)]
        vias=[box(-10,-10,10,10),box(-53,-152,-47,-146),box(57,-152,63,-146)]
        layers={'9/0':[box(-500,-500,500,500)],'8/0':[box(-320,-320,320,320)],'4/0':vias,'5/0':fixed}
        roles={'9/0':'封装边框','8/0':'芯片边框','4/0':'运动过孔','5/0':'固定RDL'}
        data=GDSData('u.gds','u','TOP',['TOP'],.001,{k:[np.array(g.exterior.coords) for g in v] for k,v in layers.items()},roles)
        c=build_case(data,roles,layer_order=['4/0','5/0'])
        e=PreviewEngine(GeometryCache(data),c,roles,{},Parameters(annulus_um=0),None,c['connections'])
        try:
            e.configure_width(True,ViewSettings())
            def mouths(a):
                return [r for r in a['rows'] if r['pair']==('运动过孔孔盘 4/0 #1','固定障碍 5/0 #1') and abs(r['point'][1])<20]
            nominal=mouths(frame_at(e))
            self.assertTrue(any(r['point'][0]<0 and r['required']==1 for r in nominal))
            self.assertTrue(any(r['point'][0]>0 and r['required']==1 for r in nominal))
            right=[r for r in mouths(frame_at(e,70)) if r['point'][0]>80]
            self.assertTrue(any(r['status']==BLOCKED and r['current']==0 for r in right))
        finally:e.close()

    def test_ordered_mixed_widths_across_slots_and_diagonal_measurement(self):
        self.assertTrue(holds_original([45,60],[15,30],15))
        self.assertFalse(holds_original([60,45],[15,30],15))
        from shapely.affinity import rotate
        g=rotate(box(-5,-100,5,100),45,origin=(0,0))
        plane=dict(routes=np.array([g],object),boundaries=np.array([g.boundary],object),
                   tree=STRtree([g]),route_sources=[[(('5/0',1),g)]],width=8)
        strand=_crossings(np.array([[[-40,0],[40,0]]]),plane,1.e-8)[0][0]
        self.assertAlmostEqual(strand[1],10.)  # the longer oblique chord is not the linewidth

    def test_fast_barrier_lookup_matches_exact_geometry_including_tangency(self):
        # Identical bounding boxes are not sufficient for a diagonal line.
        lines=shapely.linestrings([[(0.,0.),(100.,100.)],[(0.,2.),(100.,2.)],
                                   [(5.,0.),(5.,100.)],[(0.,0.),(0.,0.)]])
        walls=np.array([box(-10,-10,0,0),box(100,100,110,110),box(38,44,43,60),
                        box(40,40,50,55),box(4,40,5,55),box(1,2,3,3)],object)
        pairs=np.tile([0,1],(len(lines),1))
        actual=_intruders(lines,pairs,walls,STRtree(walls),1.e-8)
        for i,line in enumerate(lines):
            expected=set()
            if line.length>2.e-8:
                trimmed=shapely.LineString([line.interpolate(1.e-8),line.interpolate(-1.e-8)])
                expected={j for j in range(2,len(walls)) if trimmed.intersects(walls[j])}
            self.assertEqual(set(actual.get(i,[])),expected)

    def test_jobs_with_retired_fields_keep_independent_options(self):
        for version in range(1,6):
            old=normalize_project(dict(schema_version=version,settings={'width_overlap_um':7.,'width_mode':'landing',
                                  'width_search_radius_um':45.,'width_gap_threshold_um':22.,'drc_tolerance_um':-1.}))
            self.assertEqual(old['overlays'],OVERLAY_DEFAULTS)
            self.assertEqual(old['settings']['reconstruction_overlap_um'],7.)
            self.assertEqual(old['settings']['drc_tolerance_um'],-1.)
            self.assertFalse(any(k.startswith('width_') for k in old['settings']))
        p=normalize_project(dict(schema_version=5,overlays=dict(width=True),settings={'reconstruction_enabled':True,
                            'reconstruction_overlap_um':9.,'width_overlap_um':4.}))
        self.assertTrue(p['overlays']['width'] and p['overlays']['reconstruction'])
        self.assertEqual(p['settings']['reconstruction_overlap_um'],9.)
        self.assertEqual(normalize_project(p),p)
        with self.assertRaises(ValueError):normalize_project(dict(schema_version=5,overlays={'width':'false'}))
