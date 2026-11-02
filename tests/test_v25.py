"""Nominal through-routes, real throat witnesses, cache accounting and Job settings."""
import unittest
import ctypes
import os
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box
from shapely.strtree import STRtree
from die_shift_studio.prepared_motion import prepare_motion
from die_shift_studio.project import normalize_project
from die_shift_studio.session_cache import ProjectCacheRegistry
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import _crossings,gate_rectangle,Gate,WidthAnalyzer
from tests.test_v21 import example
from tests.test_width_channels import frame_at


class V25Tests(unittest.TestCase):
    def test_connected_route_must_exit_both_mouths(self):
        cut=np.array([[[-55.,0.],[55.,0.]]]);genuine=box(22,-35,28,35)
        # First stub crosses y=0, but terminates inside the rectangle. The
        # same network reaches the opposite side only outside this throat.
        stub=shapely.union_all([box(-21,-34,-15,2),box(-65,-34,-15,-29),
                                box(-65,-34,-60,32),box(-65,26,-15,32)])
        tracks=np.array([stub,genuine],object)
        plane=dict(routes=tracks,boundaries=shapely.boundary(tracks),tree=STRtree(tracks),
                   route_sources=[[(('5/0',i),g)] for i,g in enumerate(tracks)],width=10,space=8)
        rows=_crossings(cut,plane,1e-7)[0]
        self.assertEqual([(r[1],r[2]) for r in rows],[(6.,(('5/0',1),))])

    def test_aperture_uses_adjacent_physical_walls_and_has_one_marker_per_mouth(self):
        _,_,_,_,e=example('示例P_单点PATH与相邻孔盘')
        try:
            e.configure_width(True,ViewSettings());a=frame_at(e)
            walls={q['name']:q['geometry'] for q in a['retained']}
            rows=[r for r in a['rows'] if '运动过孔孔盘 4/0 #2' in r['pair']]
            self.assertEqual(len(rows),2)
            for r in rows:
                ends=np.asarray(r['witness_um']);left,right=(walls[k] for k in r['pair'])
                self.assertLess(left.distance(shapely.Point(ends[0])),1e-7)
                self.assertLess(right.distance(shapely.Point(ends[1])),1e-7)
                self.assertAlmostEqual(np.linalg.norm(ends[1]-ends[0]),left.distance(right),places=6)
                np.testing.assert_allclose(r['point'],ends.mean(axis=0))
                self.assertEqual(r['required'],1)
                self.assertEqual(r['locked'],1)
                np.testing.assert_allclose(gate_rectangle(r,15,15).centroid.coords[0],ends.mean(axis=0),atol=1e-8)
        finally:e.close()

    def test_disjoint_intervals_on_same_parallel_walls_stay_separate(self):
        # Each y is a sampled valid witness; an intervening y had no original
        # through-line. Both sections carry the same net on a later return.
        group=[]
        for run,y in [(0,0.),(1,10.)]:
            ends=np.array([[0.,y],[40.,y]])
            gate=Gate((0,1),(0,1),ends,True,(5.,),(20.,),((('5/0',3),),),40.,(0,1,run,0))
            row=dict(current=1,required=1,gap_um=40.,keep_um=30.,locked=0,split=False)
            group.append((gate,row))
        self.assertEqual(len(WidthAnalyzer._distinct_throats(SimpleNamespace(tol=1e-8),group,20.)),2)

    def test_owned_replay_and_disk_hits_do_not_consume_result_slots(self):
        reg=ProjectCacheRegistry();group=reg.get('low',2)
        values=group.values;values.budget=0
        for key in ('A','B'):values[key]=key
        cache=group.motions;cache.budget=0;start=np.zeros((1,3));end=np.array([[70.,0,0]])
        engine=SimpleNamespace(signature='sample',cache_key=lambda:(),bound=SimpleNamespace(owner_radii=np.array([50.])),
                               frame=lambda s,*_,**kw:dict(states=s.copy()))
        try:
            cfg=ViewSettings();prepare_motion(engine,start,end,cfg,1000,cache,lambda *_:None)
            files=lambda:{p:(p.stat().st_size,p.stat().st_mtime_ns) for c in group.caches.values() for p in c.root.iterdir()}
            before=files();sizes=reg.statistics('low')
            self.assertEqual((len(group.recent),len(group.replays),len(before)),(2,1,3))
            with patch.object(engine,'frame',side_effect=AssertionError('recomputed a replay')):
                for i in range(24):
                    self.assertEqual(values['A' if i%2 else 'B'],'A' if i%2 else 'B')
                    origin,target=(start,end) if i%2 else (end,start)
                    packet=prepare_motion(engine,origin,target,cfg,1000,cache,lambda *_:None)
                    self.assertTrue(packet['cached'])
                    for f,frame in packet['frames']:np.testing.assert_allclose(frame['states'],(1-f)*origin+f*target,atol=1e-12)
            self.assertEqual(files(),before);self.assertEqual(reg.statistics('low'),sizes)
            # Even genuinely new transitions at the replay limit cannot evict A/B.
            for value in (10.,20.,30.):prepare_motion(engine,start,np.array([[value,0,0]]),cfg,1000,cache,lambda *_:None)
            self.assertEqual(set(values),{'A','B'});self.assertEqual(len(group.replays),2)
        finally:reg.close()

    def test_incomplete_packet_is_reused_when_requested_in_reverse(self):
        states=[];cache={};start=np.zeros((1,3));end=np.array([[100.,0,0]])
        def frame(s,*_,**kw):states.append(float(s[0,0]));return dict(states=s.copy())
        e=SimpleNamespace(signature='sample',cache_key=lambda:(),bound=SimpleNamespace(owner_radii=np.array([50.])),frame=frame)
        def interrupt(*_):
            if len(states)==2:raise InterruptedError()
        with self.assertRaises(InterruptedError):prepare_motion(e,start,end,ViewSettings(),1000,cache,interrupt)
        packet=prepare_motion(e,end,start,ViewSettings(),1000,cache,lambda *_:None)
        self.assertEqual(len(states),packet['count']);self.assertEqual(len(cache),1)
        for f,row in packet['frames']:np.testing.assert_allclose(row['states'],end*(1-f)+start*f,atol=1e-12)

    def test_windows_gui_hides_only_its_own_console(self):
        from unittest.mock import Mock
        from die_shift_studio.frozen_support import hide_owned_console
        kernel=Mock();user=Mock();kernel.GetConsoleWindow.return_value=123
        system=SimpleNamespace(kernel32=kernel,user32=user)
        def single(pids,count):pids[0]=os.getpid();return 1
        with patch('sys.platform','win32'),patch.object(ctypes,'windll',system,create=True):
            kernel.GetConsoleProcessList.side_effect=single
            hide_owned_console()
            self.assertEqual(user.ShowWindow.call_count,1)
            self.assertEqual(user.ShowWindow.call_args.args[1],0)
            kernel.GetConsoleProcessList.side_effect=None
            for count in (0,2,4):
                kernel.GetConsoleProcessList.return_value=count;hide_owned_console()
            self.assertEqual(user.ShowWindow.call_count,1)

    def test_label_moves_to_a_free_side_or_yields_to_the_rectangles(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from die_shift_studio.width_labels import LabelLayout
        figure=Figure(figsize=(6,4),dpi=100);canvas=FigureCanvasAgg(figure)
        ax=figure.add_axes([.05,.05,.9,.90]);ax.set(xlim=(0,100),ylim=(0,70));canvas.draw()
        gate=box(28,30,75,33)
        # Both lateral neighbours and the upper half leave only the lower
        # side available. The two-line label has to change sides upon pan.
        obstacles=[gate,box(0,33,100,70),box(0,0,28,33),box(75,0,100,33)]
        text='原宽:4,必过:3,现宽:2\n预期收窄:5微米'
        layout=LabelLayout(ax,obstacles,10,{})
        found=layout.place(0,text,0);self.assertIsNotNone(found)
        self.assertLess(layout.labels[0][1][1],ax.transData.transform((0,30))[1]/layout.scale)
        self.assertFalse(len(layout.tree.query(shapely.box(*layout.labels[0][0],*layout.labels[0][1]),predicate='intersects')))
        blocked=LabelLayout(ax,[gate,box(0,0,100,70)],10,{})
        self.assertIsNone(blocked.place(0,text))
        ax.set_ylim((30,70));layout=LabelLayout(ax,[gate],10,{})
        self.assertIsNotNone(layout.place(0,text,found[0]))
        self.assertGreater(layout.labels[0][0][1],ax.bbox.y0/layout.scale)

    def test_dense_label_prescreen_matches_exact_rectangle_queries(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from die_shift_studio.width_labels import LabelLayout
        figure=Figure(figsize=(8,4),dpi=100);canvas=FigureCanvasAgg(figure)
        ax=figure.add_axes([.03,.03,.95,.93]);ax.set(xlim=(0,100),ylim=(0,65));canvas.draw()
        gates=[box(x-1,y-1,x+1,y+1) for x in range(4,65,3) for y in range(2,64,2)]
        gates+=[box(80,10,85,12),box(79,55,87,56)]
        texts=['A:6 B:4 C:3\nwidth:10']*len(gates)
        exact=LabelLayout(ax,gates,8,{})
        fast=LabelLayout(ax,gates,8,{});fast.prescreen(texts)
        self.assertTrue(any(not x for x in fast.viable))
        self.assertEqual([exact.place(i,t) for i,t in enumerate(texts)],
                         [fast.place(i,t) for i,t in enumerate(texts)])
        self.assertGreater(len(exact.labels),0)

    def test_display_settings_restore_independently_of_calculation(self):
        old=normalize_project({'schema_version':1})
        self.assertEqual(old['expanded_settings'],[]);self.assertEqual(old['settings']['width_label_font_pt'],8.)
        saved=normalize_project(dict(old,expanded_settings=['缓存管理'],settings={'width_label_font_pt':14}))
        self.assertEqual(saved,normalize_project(saved))
        self.assertEqual(saved['expanded_settings'],['缓存管理'])
        cfg=ViewSettings.from_dict(saved['settings'])
        self.assertEqual(cfg.width_key(),ViewSettings().width_key())
        self.assertEqual(cfg.numeric_key(),ViewSettings().numeric_key())
        with self.assertRaises(ValueError):ViewSettings.from_dict({'width_label_font_pt':0})
