"""Independent ternary grid, full-frame oracle and cache metadata regressions."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import shapely
from shapely.geometry import box
from die_shift_studio.core import Parameters,GDSData,build_case,uncertainty_bound
from die_shift_studio.fast_preview import PreviewEngine,GeometryCache
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog,mean_and_bounds
from die_shift_studio.extreme_validation import evaluate_states,status_for,frame_status
from die_shift_studio.extreme_footprints import extreme_footprints
from die_shift_studio.preview import rigid_transform
from die_shift_studio.session_cache import ProjectCacheRegistry,SessionCache
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.acceleration import _rigid_kernel


def fixture():
    roles={'9/0':'封装边框','8/0':'芯片边框','5/0':'固定RDL','4/0':'运动过孔','3/0':'运动RDL','10/0':'禁布区'}
    copper={'9/0':[box(-250,-120,250,120)],'8/0':[box(-190,-65,-5,65),box(10,-65,190,65)],
            '5/0':[box(-52,-18,-24,18),box(24,-17,52,17),box(-6,-50,5,45)],
            '4/0':[box(-40,-5,-32,5),box(32,-5,40,5)],
            '3/0':[box(-90,-4,-32,4),box(32,-4,90,4)],'10/0':[box(-122,-5,-116,5)]}
    data=GDSData('local.gds','v26fixture','TOP',['TOP'],.001,{k:[np.array(g.exterior.coords) for g in gs] for k,gs in copper.items()},roles)
    case=build_case(data,roles,layer_order=['3/0','4/0','5/0'])
    p=Parameters(multi_bound_x_um=35,multi_bound_y_um=5,bound_angle_deg=1,annulus_um=3,line_width_um=5,line_space_um=5,
                 min_die_gap_um=8,min_package_clearance_um=3)
    e=PreviewEngine(GeometryCache(data),case,roles,{},p,None,case['connections'])
    return e,case,p


class V26Tests(unittest.TestCase):
    def test_zero_rotation_does_not_certify_an_unbounded_local_transition(self):
        from die_shift_studio.deformation import Field
        settings=replace(ViewSettings(),deformation_continuity='C1',deformation_method='bezier')
        def bound(span):
            field=Field([{'center':(5.,0.),'owner':0}],np.zeros((1,2)),
                        np.array([[3.,0.,0.]]),[5.],span,settings)
            return field.jacobian_bound()
        with np.errstate(invalid='raise',divide='raise'):
            self.assertAlmostEqual(bound(10.),.45)
            for span in (np.inf,np.nan,0.):
                self.assertEqual(bound(span),np.inf)

    def test_ternary_dedup_frozen_bias_and_big_integer_last_page(self):
        c={'dies':[None,None,None]};p=Parameters(per_die_enabled=True,die_bounds=[[0,0,0],[5,0,0],[4,9,1]],mean_x_um=5,mean_y_um=3)
        g=ExtremeCatalog(c,p)
        self.assertEqual(g.radices,[1,2,27]);self.assertEqual(g.total,53)
        self.assertFalse(any(np.any(e['states'][0]) or e['states'][1,1]!=0 for e in g))
        self.assertEqual(len({e['states'].tobytes() for e in g}),53)
        np.testing.assert_array_equal(mean_and_bounds(p.for_die(0),3)[0],[0,0,0])
        self.assertEqual(uncertainty_bound([[500,700]],(0,0),p.for_die(0),3)[0],0)
        huge=ExtremeCatalog({'dies':[None]*20},Parameters());self.assertTrue(huge);self.assertEqual(huge.total,27**20-1)
        page=huge.page((huge.total-1)//64)
        self.assertEqual(page[-1][0],huge.total-1)
        self.assertEqual(huge.index(page[-1][1]['choices']),page[-1][0])

    def test_all_729_statuses_match_full_geometry_and_drc(self):
        e,c,p=fixture();g=ExtremeCatalog(c,p);settings=ViewSettings()
        try:
            factors=evaluate_states(e,g,settings)
            self.assertEqual((factors['local_states'],factors['batches'],factors['pair_states']),(54,27,729))
            self.assertTrue(any(any(row) for row in factors['pairs'][0]['hits']))
            safe=bad=0
            for i in range(-1,g.total):
                digits=g.choices(i) if i>=0 else [0,0]
                z=np.array([g.locals[j][digit] for j,digit in enumerate(digits)])
                truth=frame_status(e.frame(z));fact=status_for(factors,digits)
                for key in ('safe','contact','failures','drc','checked'):
                    self.assertEqual(fact[key],truth[key],(i,digits,key))
                safe+=bool(fact['safe']);bad+=not fact['safe']
            self.assertTrue(safe and bad)
            focused=RepresentativeExtremes(c,p,g,pairs=factors['pairs'])
            for row in factors['pairs']:
                for kind in ('near','far'):
                    key=g.index(row[kind])
                    self.assertTrue(key<0 or key in focused.indices)
        finally:e.close()

    def test_mixed_and_all_locked_grid_match_every_direct_pose(self):
        old,c,_=fixture();model=old.contact
        try:
            for bounds in ([[0,0,0],[20,0,1]],[[0,0,0],[0,0,0]]):
                p=replace(old.p,per_die_enabled=True,die_bounds=bounds,mean_x_um=3,mean_y_um=12,mean_angle_deg=.1)
                grid=ExtremeCatalog(c,p)
                e=PreviewEngine(model.cache,c,model.roles,{},p,None,model.connections)
                try:
                    factors=evaluate_states(e,grid,ViewSettings())
                    for i in range(-1,grid.total):
                        digits=grid.choices(i) if i>=0 else [0,0]
                        pose=np.array([v[d] for v,d in zip(grid.locals,digits)])
                        truth=frame_status(e.frame(pose));fast=status_for(factors,digits)
                        self.assertEqual([truth[k] for k in ('contact','failures','drc')],[fast[k] for k in ('contact','failures','drc')])
                    self.assertTrue(np.array_equal(grid.locals[0],np.zeros((1,3))))
                finally:e.close()
        finally:old.close()

    def test_discrete_overlay_is_exact_union_including_zero_no_cartesian_duplicates(self):
        e,c,p=fixture()
        try:
            p=replace(p,per_die_enabled=True,die_bounds=[[0,0,0],[35,5,1]])
            grid=ExtremeCatalog(c,p);shapes=extreme_footprints(e.contact,p)
            self.assertEqual(shapes['package_states'],27);self.assertTrue(shapes['features'])
            for q in shapes['features']:
                self.assertEqual((q['owner'],q['poses']),(1,27))
                via=e.contact.cache.layers[q['key']]['geometry'][q['index']]
                coords=shapely.get_coordinates(via);center=e.bound.centers[1]
                oracle=shapely.union_all([shapely.Polygon(rigid_transform(coords,center,z)) for z in grid.locals[1]])
                self.assertLess(oracle.symmetric_difference(q['geometry']).area,1e-7)
        finally:e.close()

    def test_cache_index_never_loads_or_touches_and_evictions_remove_colors(self):
        reg=ProjectCacheRegistry();group=reg.get('w',2);store=SessionCache(0,group)
        store.pose_source=('A','预演帧',None)
        a=bytes(24);b=np.array([10.,0.,0.]).tobytes();key=(('mask',),b,False)
        store[key]='frame';group.motions[('validated-motion-v25','A',(),a,b,3)]={'complete':True}
        group.values[('repair','A',b)]=1
        recent=list(group.recent)
        try:
            with patch.object(SessionCache,'__getitem__',side_effect=AssertionError('reading consumed cache')):
                rev,data=group.pose_inventory('A',a)
                self.assertEqual(data,{b:3,a:1});self.assertEqual(list(group.recent),recent)
                self.assertIsNone(group.pose_inventory('A',a,rev));self.assertEqual(group.pose_inventory('different',a)[1],{})
            # The frame store itself has no source_config; do not shadow the
            # actual overlay mask requested by the GUI or lose the third result.
            group.frame_stores['A']=store
            _,counts,statuses=group.pose_inventory('A',a,config=('active',))
            self.assertEqual(counts[b],3);self.assertEqual(statuses,{})
            group.values['new entry']=2
            self.assertEqual(group.pose_inventory('A',a)[1][b],2)
            group.clear();self.assertEqual(group.pose_inventory('A',a)[1],{})
        finally:reg.close()

    def test_foreign_index_ignores_empty_and_preserves_distant_geometry(self):
        from die_shift_studio.screening import ForeignIndex
        wall=box(1e12,0,1e12+2,2);via=box(0,0,1,1)
        index=ForeignIndex({'5':[(shapely.Polygon(),2),(via,0)],'6':[(wall,2)]})
        self.assertEqual(index.distance(via,0,['5'],1),float('inf'))
        self.assertEqual(index.distance(via,0,['5','6'],1),wall.distance(via))

    def test_batched_rigid_transform_matches_per_owner_reference(self):
        random=np.random.default_rng(16);xy=random.normal(size=(1000,2))*300;owners=random.integers(-2,3,len(xy))
        pivots=random.normal(size=(3,2))*1000;poses=random.normal(size=(3,3))
        expected=xy.copy()
        for i in range(3):expected[owners==i]=rigid_transform(xy[owners==i],pivots[i],poses[i])
        np.testing.assert_allclose(_rigid_kernel(np,xy,owners,pivots,poses),expected,atol=1e-10)
        pivots[1]=1e16;poses[1]=0
        np.testing.assert_array_equal(_rigid_kernel(np,xy,owners,pivots,poses)[owners==1],xy[owners==1])
