from dataclasses import asdict,replace
import itertools,json,math,unittest
import numpy as np
import shapely
from shapely.geometry import Point,LineString,box
from test_v38 import fixture
from die_shift_studio.settings import ViewSettings
from die_shift_studio.reconstruction_route import warp
from die_shift_studio.connection_curves import biarc
from die_shift_studio.reconstruction_yield import measure_reconstruction_yield
from die_shift_studio.width_grid import WidthGrid


class V39Tests(unittest.TestCase):
    def test_straight_matches_four_real_intersections(self):
        e,_,_=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan'];part=plan.partition(*q['interval'])
            pose=np.array([20.,18.,.02]);center=e.bound.centers[0]
            from die_shift_studio.preview import rigid_transform
            n=np.asarray(part['near'].coords)[[0,-1]];f=np.asarray(part['far'].coords)[[0,-1]]
            n=n[np.argsort(n[:,1])];f=f[np.argsort(f[:,1])]
            expected=shapely.Polygon(np.vstack([rigid_transform(n,center,pose),f[::-1]]))
            r,error=warp(part,plan,center,pose,'linear');self.assertIsNone(error)
            self.assertLess(r['A'].symmetric_difference(expected).area,expected.length*plan.tolerance*2)
            alternatives=[warp(part,plan,center,pose,k)[0]['A'] for k in ('c1','c2','arc')]
            self.assertTrue(all(g.symmetric_difference(expected).area>1 for g in alternatives))
            self.assertGreater(alternatives[0].symmetric_difference(alternatives[1]).area,1)
        finally:e.close()

    def test_biarc_tangents_and_circular_segments(self):
        curve=biarc([0,0],[100,30],[1,0],[1,0],.0001)
        self.assertLess(abs((curve[1]-curve[0])[1]/np.linalg.norm(curve[1]-curve[0])),.003)
        self.assertLess(abs((curve[-1]-curve[-2])[1]/np.linalg.norm(curve[-1]-curve[-2])),.003)
        # Parallel end tangents give symmetric equal-radius circles meeting at mid.
        first=curve[curve[:,0]<=50+1e-9]
        center=np.array([0.,(50**2+15**2)/(2*15)])
        self.assertLess(np.ptp(np.linalg.norm(first-center,axis=1)),1e-7)
        with self.assertRaises(ValueError):biarc([0,0],[-10,0],[1,0],[1,0],.001)

    def test_whole_pad_never_collapses_to_the_blue_point(self):
        e,_,_=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan'];part=plan.partition(0,q['interval'][1])
            pose=np.array([15.,12.,.01])
            from die_shift_studio.reconstruction import transform
            landing=transform(Point(0,0).buffer(29),e.bound.centers[0],pose)
            for style in ('linear','c1','c2','arc'):
                regs,error=warp(part,plan,e.bound.centers[0],pose,style)
                self.assertIsNone(error,(style,error));self.assertTrue(regs['C'].is_empty)
                self.assertLess(landing.difference(regs['A']).area,.001)
        finally:e.close()

    def test_style_and_yield_grid_saved_and_old_jobs_default(self):
        self.assertEqual(ViewSettings.from_dict({}).reconstruction_style,'c2')
        keys=[]
        for style in ('linear','c1','c2','arc'):
            q=ViewSettings(reconstruction_style=style,reconstruction_grid_nodes=5)
            self.assertEqual(ViewSettings.from_dict(json.loads(json.dumps(asdict(q)))),q)
            keys.append(q.reconstruction_key())
        self.assertEqual(len(set(keys)),4)
        with self.assertRaises(ValueError):ViewSettings.from_dict({'reconstruction_style':'unknown'})
        with self.assertRaises(ValueError):ViewSettings.from_dict({'reconstruction_grid_nodes':2})

    def test_live_rules_have_width_spacing_and_enclosure_witnesses(self):
        e,_,_=fixture()
        try:
            book=e.reconstructor.rules;key=next(iter(book.nets));states=np.zeros((1,3))
            narrow=LineString([(0,0),(600,0)]).buffer(4)
            rows=book.local(key,narrow,states)
            self.assertIn('线宽不足',[k for k,g in rows]);self.assertIn('孔盘包围不足',[k for k,g in rows])
            self.assertFalse(book.pair(box(0,0,50,50),box(0,55,50,100)).is_empty)
            self.assertTrue(book.pair(box(0,0,50,50),box(0,80,50,100)).is_empty)
            self.assertFalse(book.passes(key,narrow,states))
        finally:e.close()

    def test_joint_grid_matches_full_pose_rule_check(self):
        e,_,_=fixture()
        try:
            # Enclosure too large at the original endpoint: no false 100% yield.
            cfg=ViewSettings(reconstruction_grid_nodes=3)
            from die_shift_studio.reconstruction import Reconstructor
            p=replace(e.p,rho_xy=.6,annulus_um=23)
            r=Reconstructor(e.contact,p,cfg)
            bank=measure_reconstruction_yield(r,p,cfg,lambda *_:None)
            grid=WidthGrid(p,1,3);fail=[];safe=[]
            for i,pose in enumerate(grid.locals[0]):
                if not grid.weights[0][i]:continue
                f=r.frame(pose[None,:]);bad=bool(r.rules.check(f['replacements'],pose[None,:]))
                (fail if bad else safe).append(grid.weights[0][i])
            self.assertAlmostEqual(bank['failure'],math.fsum(fail),12)
            self.assertAlmostEqual(bank['value'],math.fsum(safe),12)
            self.assertGreater(bank['failure'],0);r.close()
        finally:e.close()

    def test_hover_blit_erases_old_line_and_keeps_new_line(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from die_shift_studio.render_cache import SceneRenderer
        fig=Figure(figsize=(4,3),dpi=80);canvas=FigureCanvasAgg(fig);ax=fig.add_subplot()
        ax.set(xlim=(0,10),ylim=(0,10));line,=ax.plot([2,2],[2,8],color='blue',lw=5)
        renderer=SceneRenderer(canvas,ax)
        renderer.draw('same');renderer.draw('same')
        line.set_xdata([7,7]);renderer.draw('same');actual=np.asarray(canvas.buffer_rgba()).copy()
        canvas.draw();expected=np.asarray(canvas.buffer_rgba()).copy()
        self.assertTrue(np.array_equal(actual,expected),'old boundary was left in cached background')

    def test_short_transition_does_not_oversample_rigid_pad(self):
        from die_shift_studio.reconstruction_route import taper_mesh
        e,_,_=fixture()
        try:
            q=e.reconstructor.frame(np.zeros((1,3)))['pieces'][0];plan=q['route_plan']
            part=plan.partition(0,plan.land_end+.05);self.assertIsNotNone(part)
            mesh=taper_mesh(part,plan,plan.land_end,part['b'])
            self.assertLess(shapely.get_num_coordinates(mesh),1000)
            self.assertLess(mesh.symmetric_difference(part['A']).area,1e-6)
        finally:e.close()

    def test_native_boolean_matches_full_native_witnesses(self):
        from die_shift_studio.native_backend import any_violation,geometry
        for g in (box(0,0,100,8),box(0,0,100,20),Point(0,0).buffer(30),
                  shapely.union_all([box(0,0,10,100),box(15,0,25,100)])):
            for op in ('width','space'):
                for distance in (5.,15.,22.):
                    self.assertEqual(any_violation(op,g.wkb,.001,distance),not geometry(op,g,.001,distance).is_empty)

    def test_infinity_still_prioritizes_reference_distribution(self):
        e,_,_=fixture()
        try:
            from die_shift_studio.reconstruction import Reconstructor
            p=replace(e.p,target_nsigma=math.inf)
            r=Reconstructor(e.contact,p,ViewSettings())
            f=r.frame(np.zeros((1,3)));self.assertTrue(f['pieces']);r.close()
        finally:e.close()

    def test_two_die_joint_event_matches_cartesian_geometry(self):
        from die_shift_studio.core import GDSData,Parameters,build_case
        from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
        from die_shift_studio.drc import GlobalDRC
        shapes={'127/0':[box(-200,-200,800,300)],'128/0':[box(-40,y-25,40,y+25) for y in (0,70)],
                '3/0':[Point(0,y).buffer(20) for y in (0,70)],'4/0':[Point(0,y).buffer(7) for y in (0,70)],
                '5/0':[LineString([(0,y),(600,y)]).buffer(15).union(Point(0,y).buffer(25)).union(Point(600,y).buffer(25)) for y in (0,70)],
                '6/0':[Point(600,y).buffer(7) for y in (0,70)]}
        roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
        d=GDSData('joint.gds','joint','TOP',['TOP'],.001,{k:[np.asarray(g.exterior.coords) for g in gs] for k,gs in shapes.items()},roles)
        c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0'])
        p=Parameters(multi_bound_x_um=0,multi_bound_y_um=30,bound_angle_deg=0,annulus_um=5,moving_annulus_um=5)
        cfg=ViewSettings();e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,cfg)
        try:
            bank=measure_reconstruction_yield(e.reconstructor,p,cfg,lambda *_:None)
            grid=WidthGrid(p,2,3);failure=[];drc=GlobalDRC(e.contact,p)
            for i,j in itertools.product(range(len(grid.locals[0])),range(len(grid.locals[1]))):
                z=np.array([grid.locals[0][i],grid.locals[1][j]])
                f=e.reconstructor.frame(z)
                if drc.frame(z,f['replacements'],replace(cfg,drc_min_angle_deg=1)):
                    failure.append(grid.weights[0][i]*grid.weights[1][j])
            self.assertGreater(bank['pair_checks'],0);self.assertGreater(bank['failure'],0)
            self.assertLess(bank['failure'],1)
            self.assertAlmostEqual(bank['failure'],math.fsum(failure),12)
        finally:e.close()

    def test_lower_failure_mass_precedes_longer_interval(self):
        e,_,_=fixture(bent=True)
        try:
            scored=[]
            def risk(plan,part,*args):
                length=part['b']-part['a'];scored.append(length)
                return (0.8 if length>200 else 0.1),1
            e.reconstructor.rules.candidate_mass=risk
            f=e.reconstructor.frame(np.zeros((1,3)))
            q=f['pieces'][0]
            self.assertTrue(any(s>200 for s in scored) and any(s<200 for s in scored))
            self.assertLess(q['interval'][1]-q['interval'][0],200)
        finally:e.close()

    def test_shared_source_paints_union_of_all_fixed_regions(self):
        from types import SimpleNamespace
        from matplotlib.figure import Figure
        from matplotlib.patches import PathPatch
        from die_shift_studio.qt_compat import QtCore
        from die_shift_studio.plot_geometry import geometry_path
        from die_shift_studio.reconstruction_overlay import ReconstructionOverlay
        class Window(QtCore.QObject):pass
        w=Window();fig=Figure();w.ax=fig.add_subplot();w._closing=False
        on=SimpleNamespace(isChecked=lambda:True)
        w.reconstruction_toggle=on;w.visible_boxes={'5/0':on};w.view_bounds=(-10,-10,210,110)
        w.ordered_layers=lambda:['5/0'];w.geometry_patch=lambda g:PathPatch(geometry_path(g))
        empty=shapely.Polygon();pieces=[]
        for owner,g in enumerate((box(0,0,100,100),box(100,0,200,100))):
            pieces.append(dict(layer='5/0',index=17,die=owner,vias=[owner],route_plan=None,fixed=g,
                               regions=dict(A=empty,B=g,C=empty),warning='',moved_cut=None,extra_cut=None))
        w.current_frame={'reconstruction':{'pieces':pieces}}
        overlay=ReconstructionOverlay(w);overlay.draw()
        paths=overlay.artists['5/0','B'].get_paths()
        self.assertTrue(any(p.contains_point((50,50)) for p in paths))
        self.assertTrue(any(p.contains_point((150,50)) for p in paths))

if __name__=='__main__':unittest.main()
