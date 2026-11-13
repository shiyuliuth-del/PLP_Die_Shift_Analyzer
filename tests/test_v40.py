from dataclasses import replace,asdict
import itertools,json,math,tempfile,time,threading,unittest
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import Point,LineString,box
from unittest.mock import patch
from test_v38 import fixture
from die_shift_studio.core import GDSData,Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.reconstruction_events import scan,current_failures,zone_key,color_for,active_region
from die_shift_studio.reconstruction_probability import attach


def make_engine(shapes,p,cfg=ViewSettings()):
    roles=dict(zip(shapes,('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
    d=GDSData('v40.gds','v40-'+str(len(shapes['5/0'])),'TOP',['TOP'],.001,{k:[np.asarray(g.exterior.coords) for g in v] for k,v in shapes.items()},roles)
    c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0'])
    e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,cfg)
    return e,d,c


class Reconstruction40Tests(unittest.TestCase):
    def test_each_style_discrete_filter_matches_full_native_pose_rules(self):
        base,d,c=fixture();roles=base.roles;p=replace(base.p,single_bound_x_um=0,single_bound_y_um=400,bound_angle_deg=0);base.close()
        for style in ('linear','c1','c2','arc'):
            e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);cfg=ViewSettings(reconstruction_style=style);e.configure_reconstruction(True,cfg)
            try:
                grid=ExtremeCatalog(c,p);bank=scan(e.reconstructor,grid);idx=ProblemIndex(grid,bank['red_factors']);some_bad=False
                for i in range(-1,grid.total):
                    z=np.zeros((1,3)) if i<0 else grid[i]['states'];digits=[0] if i<0 else grid.choices(i)
                    actual=current_failures(e.reconstructor.frame(z));some_bad |= bool(actual)
                    self.assertEqual(idx.matches(digits),bool(actual),(style,i))
                self.assertTrue(some_bad);self.assertEqual(bank['geometry_checks'],3)
            finally:e.close()

    def test_all_safe_is_green_other_failure_is_yellow_and_nominal_is_red(self):
        e,_,c=fixture()
        try:
            r=e.reconstructor;bank=scan(r,ExtremeCatalog(c,e.p));q=r.frame(np.zeros((1,3)))['pieces'][0]
            self.assertEqual(color_for(q,bank,False,6),'green')
            row=bank['zones'][zone_key(q)];row.update(has_failure=True)
            self.assertEqual(color_for(q,bank,False,6),'yellow');self.assertEqual(color_for(q,bank,True,6),'red')
            row['probability']=dict(certified=True,upper=1e-10,threshold=1e-9)
            self.assertEqual(color_for(q,bank,True,6),'yellow')
            row['failing_states']=bank['package_states'];self.assertEqual(color_for(q,bank,True,6),'gray')
            bad=dict(q,rule_witnesses=[{'kind':'线距不足'}]);self.assertEqual(color_for(bad,bank,False,6),'gray')
            self.assertEqual(color_for(q,bank,True,0),'red');self.assertEqual(color_for(q,bank,True,math.inf),'red')
            row['probability']['certified']=False
            self.assertEqual(color_for(q,bank,True,6),'red')
            self.assertEqual(color_for(q,None,False,6),'pending')
        finally:e.close()

    def test_risk_fill_is_only_active_region_even_when_b_has_an_original_fault(self):
        e,_,_=fixture()
        try:
            r=e.reconstructor;f=r.frame(np.zeros((1,3)));q=f['pieces'][0]
            from die_shift_studio.reconstruction import decorate_problems
            from copy import deepcopy
            f=dict(f,pieces=[dict(q)],violations=[dict(kind='线宽不足',geometry=q['regions']['B'].representative_point().buffer(1),layers=('5/0',),net=('5',q['net']))])
            decorate_problems(f);q=f['pieces'][0]
            self.assertTrue(q['problem_region'].equals(q['regions']['A']))
            self.assertLess(q['problem_region'].intersection(q['regions']['B']).area,1e-8)
            self.assertTrue(q['problem_reasons'])
        finally:e.close()

    def test_two_die_pair_dependencies_cover_mixed_non_diagonal_cases(self):
        ys=(0,70)
        shapes={'127/0':[box(-200,-200,800,300)],'128/0':[box(-40,y-25,40,y+25) for y in ys],
                '3/0':[Point(0,y).buffer(20) for y in ys],'4/0':[Point(0,y).buffer(7) for y in ys],
                '5/0':[LineString([(0,y),(600,y)]).buffer(15).union(Point(0,y).buffer(25)).union(Point(600,y).buffer(25)) for y in ys],
                '6/0':[Point(600,y).buffer(7) for y in ys]}
        p=Parameters(multi_bound_x_um=0,multi_bound_y_um=30,bound_angle_deg=0,annulus_um=5,moving_annulus_um=5)
        e,_,c=make_engine(shapes,p,ViewSettings(reconstruction_style='c1'))
        try:
            grid=ExtremeCatalog(c,p);bank=scan(e.reconstructor,grid);idx=ProblemIndex(grid,bank['red_factors'])
            self.assertGreater(bank['pair_checks'],0)
            for i in range(-1,grid.total):
                digits=[0,0] if i<0 else grid.choices(i);z=np.array([v[d] for v,d in zip(grid.locals,digits)])
                f=e.reconstructor.frame(z);self.assertEqual(idx.matches(digits),bool(current_failures(f)),digits)
                for q in f['pieces']:
                    zidx=ProblemIndex(grid,bank['zones'][zone_key(q)]['clauses'])
                    self.assertEqual(zidx.matches(digits),zone_key(q) in current_failures(f),(zone_key(q),digits))
        finally:e.close()

    def test_ignored_feature_stays_source_and_reuses_plans_after_unignore(self):
        e,d,c=fixture()
        try:
            r=e.reconstructor;f=r.frame(np.zeros((1,3)));plan=f['pieces'][0]['route_plan'];pool=r.plan_pool
            cfg=replace(r.settings,reconstruction_ignored=(('5/0',0),))
            e.configure_reconstruction(True,cfg);g=e.frame(np.array([[20.,20.,.01]]))
            self.assertFalse(g['reconstruction']['pieces']);self.assertFalse(g['reconstruction']['replacements'])
            self.assertTrue(shapely.Polygon(g['layers']['5/0']['polygons'][0]).equals(e.contact.cache.layers['5/0']['geometry'][0]))
            e.configure_reconstruction(True,replace(cfg,reconstruction_ignored=()))
            self.assertIs(e.reconstructor._plans_for_all_poses(lambda *_:None)[0]['plan'],plan)
        finally:e.close()

    def test_unrelated_die_does_not_round_a_shared_nominal_feature(self):
        points=[(-600.,0.),(600.,0.),(0.,950.)]
        shapes={'127/0':[box(-800,-400,800,1200)],'128/0':[Point(x,y).buffer(45).envelope for x,y in points],
                '3/0':[Point(x,y).buffer(20) for x,y in points],'4/0':[Point(x,y).buffer(7) for x,y in points],
                '5/0':[LineString([(-600,0),(0,.1234567),(600,0)]).buffer(20)],'6/0':[]}
        p=Parameters(multi_bound_x_um=5,multi_bound_y_um=0,bound_angle_deg=0,annulus_um=5,moving_annulus_um=5)
        e,_,_=make_engine(shapes,p)
        try:
            r=e.reconstructor;zero=np.zeros((3,3));first=r.frame(zero)
            owners={q['die'] for q in first['pieces']};self.assertEqual(len(owners),2)
            unrelated=int(np.argmax(e.bound.centers[:,1]));self.assertNotIn(unrelated,owners)
            z=zero.copy();z[unrelated,0]=5;second=r.frame(z)
            self.assertTrue(first['replacements']['5/0'][0]['geometry'].equals(shapes['5/0'][0]))
            self.assertEqual(first['replacements']['5/0'][0]['geometry'].wkb,second['replacements']['5/0'][0]['geometry'].wkb)
            self.assertEqual(current_failures(first),current_failures(second))
        finally:e.close()

    def test_source_bound_ignore_roundtrip_and_old_defaults(self):
        from die_shift_studio.project import normalize_project,restored_width_ignores
        e,d,_=fixture()
        try:
            p={'schema_version':5,'parameters':{},'settings':{},'ignored_reconstruction':{'sha256':d.sha256,'top_cell':d.top_name,'refs':[['5/0',0]]}}
            normalized=normalize_project(p)
            self.assertEqual(restored_width_ignores(normalized,d,'ignored_reconstruction'),{('5/0',0)})
            normalized['ignored_reconstruction']['sha256']='wrong';self.assertFalse(restored_width_ignores(normalized,d,'ignored_reconstruction'))
            self.assertEqual(ViewSettings.from_dict({}).reconstruction_boundary_mode,'perpendicular')
            cfg=ViewSettings(reconstruction_boundary_mode='balanced',reconstruction_ignored=(('5/0',0),))
            self.assertEqual(ViewSettings.from_dict(json.loads(json.dumps(asdict(cfg)))),cfg)
        finally:e.close()

    def test_pose_dependent_boundary_modes_preserve_positions_and_improve_objectives(self):
        from die_shift_studio.reconstruction_angles import solve,measure
        e,_,_=fixture()
        try:
            r=e.reconstructor;plan=r._plans_for_all_poses(lambda *_:None)[0]['plan'];part=plan.partition(100,400)
            pose=np.array([20.,65.,.01]);scores={}
            for mode in ('perpendicular','wide','balanced'):
                r.settings=replace(r.settings,reconstruction_style='linear',reconstruction_boundary_mode=mode)
                a,g,err,quality=solve(r,plan,part,np.zeros(2),pose)
                self.assertIsNone(err);self.assertEqual((a['a'],a['b']),(100,400));self.assertEqual(quality[0],0)
                scores[mode]=measure(a,plan,np.zeros(2),pose,g)
                if mode!='perpendicular':self.assertTrue(any(abs(v)>1e-5 for v in a['angles']))
                original=g['B'];g['B']=box(-999,-999,999,999)
                replay=solve(r,plan,part,np.zeros(2),pose)[1]
                self.assertTrue(replay['B'].equals(original),'assembling one frame mutated cached angle regions')
            self.assertGreater(scores['wide'][0],scores['perpendicular'][0]+1)
            self.assertGreater(scores['balanced'][1],scores['perpendicular'][1]+.03)
        finally:e.close()

    def test_native_width_failure_on_curves_is_not_replaced_by_contact_result(self):
        e,d,c=fixture();p=replace(e.p,line_width_um=50);roles=e.roles;e.close()
        e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings(reconstruction_style='arc'))
        try:
            grid=ExtremeCatalog(c,p);bank=scan(e.reconstructor,grid)
            self.assertTrue(any('线宽不足' in z['kinds'] for z in bank['zones'].values()))
            self.assertGreater(ProblemIndex(grid,bank['red_factors']).total,0)
        finally:e.close()

    def test_continuous_probability_bound_is_not_a_grid_zero_claim(self):
        shapes={'127/0':[box(-500,-400,1100,500)],'128/0':[box(-70,-70,70,70)],'3/0':[box(-20,-20,20,20)],
                '4/0':[Point(0,0).buffer(10)],'5/0':[box(-100,-50,700,50),box(-150,164.9,100,200)],'6/0':[Point(600,0).buffer(10)]}
        p=Parameters(single_bound_x_um=0,single_bound_y_um=100,bound_angle_deg=0,annulus_um=10,moving_annulus_um=5)
        e,_,c=make_engine(shapes,p)
        try:
            bank=attach(scan(e.reconstructor,ExtremeCatalog(c,p)),e.reconstructor);row=next(iter(bank['zones'].values()))
            self.assertTrue(row['has_failure']);bound=row['probability']
            self.assertTrue(bound['certified'],bound);self.assertLess(bound['upper'],bound['threshold'])
            # Independently sample the certified interval; exact native rules must pass.
            fraction=bound['safe_fraction']
            for y in np.linspace(-100*fraction,100*fraction,15):
                self.assertFalse(current_failures(e.reconstructor.frame(np.array([[0.,y,0.]]))))
            e.configure_reconstruction(True,ViewSettings(reconstruction_style='arc'))
            unsupported=attach(scan(e.reconstructor,ExtremeCatalog(c,p)),e.reconstructor)
            self.assertFalse(next(iter(unsupported['zones'].values()))['probability']['certified'])
        finally:e.close()

    def test_cancellation_propagates_to_geometry_threads_and_cache_waiters(self):
        from die_shift_studio.resources import parallel_map,cancellation_scope,cancel_check
        from die_shift_studio.session_cache import SessionCache
        event=threading.Event();started=threading.Event();errors=[]
        def work(i):
            started.set()
            while True:cancel_check();time.sleep(.005)
        def run():
            try:
                with cancellation_scope(event):parallel_map(work,range(8),profile={'workers':2,'worker_capacity':2,'process_workers':1})
            except InterruptedError:errors.append('cancelled')
        t=threading.Thread(target=run);t.start();self.assertTrue(started.wait(2));now=time.monotonic();event.set();t.join(2)
        self.assertFalse(t.is_alive());self.assertLess(time.monotonic()-now,1);self.assertEqual(errors,['cancelled'])
        with SessionCache() if hasattr(SessionCache,'__enter__') else tempfile.TemporaryDirectory() as _:
            cache=SessionCache();cache.inflight['busy']=threading.Event()
            try:
                with cancellation_scope(event):
                    with self.assertRaises(InterruptedError):cache.get_or_compute('busy',lambda:None)
            finally:cache.close()

    def test_streamed_export_matches_serial_batches_and_keeps_open_exact_joins(self):
        from die_shift_studio.dxf_export import snapshot
        from die_shift_studio.region_export import contours,write_regions
        from die_shift_studio.dxf_settings import DxfSettings
        from die_shift_studio.resources import resource_profile
        import ezdxf
        e,d,_=fixture()
        try:
            frame=e.frame(np.array([[20.,15.,.01]]));snap=snapshot(d,e.bound.cache,frame,reconstruction=True)
            serial=dict(resource_profile(),workers=1)
            with patch('die_shift_studio.region_export.resource_profile',return_value=serial):a=contours(snap,frame['reconstruction'],e.roles,DxfSettings())[0]
            with tempfile.TemporaryDirectory() as tmp:
                result=write_regions(snap,frame['reconstruction'],e.roles,Path(tmp)/'abc.dxf');doc=ezdxf.readfile(result['path'])
                b=[(x.dxf.layer,np.array(list(x.get_points('xy')))) for x in doc.modelspace()]
                self.assertEqual(len(a),len(b));self.assertTrue(all(not x.closed for x in doc.modelspace()))
                for (ka,xy),(kb,uv) in zip(a,b):self.assertEqual(ka,kb);np.testing.assert_allclose(xy,uv,atol=1e-10,rtol=0)
        finally:e.close()

if __name__=='__main__':unittest.main()
