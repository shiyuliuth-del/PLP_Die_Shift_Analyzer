"""Physical end motions, pad priority and persistent single-zone repair."""
from contextlib import closing
from dataclasses import asdict
from types import SimpleNamespace
import unittest

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio.core import GDSData, Parameters, build_case
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.moving_pair import boundary_pose
from die_shift_studio.reconstruction import transform
from die_shift_studio.reconstruction_edits import change_zone, key
from die_shift_studio.settings import ViewSettings
from die_shift_studio.topology_routing import connected_candidate, separate_pair


def two_ended(different=True):
    route = LineString([(0, 0), (500, 0)])
    vias = [Point(x, 0).buffer(10) for x in (0, 500)]
    copper = shapely.union_all([route.buffer(18), *(Point(x, 0).buffer(35) for x in (0, 500))])
    dies = [box(-100, -100, 100, 100), box(400, -100, 600, 100)] if different else [box(-100, -100, 600, 100)]
    shapes = {'127/0': [box(-250, -250, 750, 250)], '128/0': dies,
              '1/0': vias, '2/0': [copper]}
    roles = dict(zip(shapes, ('封装边框','芯片边框','运动过孔','固定RDL')))
    data = GDSData('pair.gds','v58-pair','TOP',['TOP'],.001,
                   {layer: [np.asarray(g.exterior.coords) for g in values] for layer, values in shapes.items()}, roles)
    case = build_case(data, roles, layer_order=['1/0','2/0'])
    p = Parameters(line_width_um=15, annulus_um=10)
    engine = PreviewEngine(GeometryCache(data), case, roles, {}, p, None, case['connections'])
    settings = ViewSettings(reconstruction_boundary_mode='perpendicular',reconstruction_two_moving_zones=2)
    engine.configure_reconstruction(True, settings)
    return engine, settings, copper


class V59Tests(unittest.TestCase):
    def test_later_pose_can_publish_while_first_pose_is_running(self):
        from threading import Event
        from die_shift_studio.resources import parallel_iter
        first=Event()
        def calculate(index):
            if index==0 and not first.wait(5):raise TimeoutError('first pose blocked')
            return index
        profile={'workers':2,'worker_capacity':2}
        with closing(parallel_iter(calculate,range(4),profile,ordered=False)) as results:
            try:self.assertNotEqual(next(results),0)
            finally:first.set()
            self.assertIn(0,list(results))

    def test_joint_fallback_does_not_reverify_current_copper(self):
        from unittest.mock import Mock, patch
        from die_shift_studio.reconstruction import Reconstructor
        name=('2/0',0)
        current=dict(replacements={},pieces=[dict(layer=name[0],index=name[1],route_available=True)])
        alternate=dict(replacements={},pieces=[])
        solver=SimpleNamespace(_raw_frame=Mock(return_value=alternate))
        blocked=[(name,'open')]
        with patch('die_shift_studio.topology_routing.resolve',side_effect=[({},current['pieces'],(),blocked),({},[],(),())]) as resolve:
            raw,_,_,_,problems=Reconstructor.complete_copper(solver,current,np.zeros((1,3)))
        self.assertIs(raw,alternate)
        self.assertFalse(problems)
        self.assertEqual(resolve.call_count,2)

    def test_triangulated_path_runs_around_an_interior_blockage(self):
        if not hasattr(shapely,'constrained_delaunay_triangles'):
            self.skipTest('triangulation unavailable on this Shapely version')
        from die_shift_studio.reconstruction_visibility import triangulated_path
        region=box(0,0,100,100).difference(box(42,20,58,80))
        path=triangulated_path(region,(15,50),(85,50))
        self.assertIsNotNone(path)
        self.assertTrue(region.covers(path))
        self.assertGreater(path.length,70.)

    def test_spawned_joint_lane_matches_in_process_copper_evidence(self):
        from die_shift_studio.evaluation_scope import focused_catalog,digits_for
        from die_shift_studio.reconstruction_joint import prepare,scene
        from die_shift_studio.reconstruction_workers import isolated_scenes
        e,_,_=two_ended()
        try:
            r=e.reconstructor;grid,indices=focused_catalog(r.model,r.p,scope='focus')
            indices=indices[:2];bundles=prepare(r,grid,indices=indices)
            poses=[tuple(digits_for(grid,i)) for i in indices]
            expected={digits:scene(r,grid,bundles,digits)[1] for digits in poses}
            with closing(isolated_scenes(r,grid,bundles,poses,1,lambda:None)) as values:
                actual={digits:frame for digits,_,frame,_ in values}
            self.assertEqual(set(actual),set(expected))
            for digits,frame in actual.items():
                self.assertEqual(frame['hard_failures'],expected[digits]['hard_failures'])
                self.assertEqual(frame['violations'],bool(expected[digits]['violations']))
                self.assertEqual(frame['repair_refs'],expected[digits]['repair_refs'])
        finally:e.close()

    def test_crashed_lane_retries_only_unreturned_pose_results(self):
        from concurrent.futures import Future
        from concurrent.futures.process import BrokenProcessPool
        from unittest.mock import patch
        from die_shift_studio.reconstruction_workers import isolated_scenes

        retried=[]
        class Pool:
            def __init__(self, broken):self.broken=broken
            def submit(self, fn, digits, fresh):
                result=Future()
                if self.broken and digits==(2,):result.set_exception(BrokenProcessPool('test'))
                else:result.set_result(digits)
                if fresh:retried.append(digits)
                return result
            def shutdown(self, **kwargs):pass

        model=SimpleNamespace(data=None,case=None,roles={},connections=SimpleNamespace(order=()),cache=None)
        solver=SimpleNamespace(model=model,p=None,settings=None,plan_pool={},_plans=[])
        with patch('die_shift_studio.reconstruction_workers.ProcessPoolExecutor',side_effect=[Pool(True),Pool(False)]):
            self.assertEqual(sorted(isolated_scenes(solver,None,None,[(0,),(1,),(2,)],1,lambda:None)),[(0,),(1,),(2,)])
        self.assertEqual(retried,[(2,)])

    def test_deletion_keeps_both_physical_ends_and_persists(self):
        e, cfg, _ = two_ended()
        try:
            nominal = e.reconstructor.frame(np.zeros((2,3)))
            self.assertEqual(len(nominal['pieces']), 2)
            for q in nominal['pieces']:
                self.assertGreater(q['nominal_regions']['A'].area, 0.)
            self.assertLess(nominal['pieces'][0]['span'].intersection(nominal['pieces'][1]['span']).area, 1e-8)
            z = np.array([[14., 5., .031],[-8., -9., -.024]])
            for deleted in nominal['pieces']:
                w = SimpleNamespace(view_settings=cfg,current_frame={'reconstruction':nominal},
                                    invalidate_reconstruction_edit=lambda:None,request_preview=lambda **_:None)
                change_zone(w,'delete',piece=deleted)
                selected = ViewSettings.from_dict(asdict(w.view_settings))
                self.assertEqual(selected.reconstruction_deleted, (key(deleted['route_plan']),))
                self.assertFalse(selected.reconstruction_ignored)
                e.configure_reconstruction(True, selected)
                r=e.reconstructor
                frame=r.frame(z)
                self.assertTrue(frame['electrically_valid'],frame['hard_failures'])
                self.assertEqual(len(frame['pieces']),1);q=frame['pieces'][0]
                actual=frame['replacements']['2/0'][0]['geometry']
                for via in r.vias_by_target['2/0',0]:
                    moved=r.model.moved(via['poly'].buffer(10),via['owner'],z)
                    self.assertLess(moved.difference(actual).area,.1)
                opposite = deleted['die']
                _,end=boundary_pose(q,{'rotation_centers':r.model.bound.centers,'states':z},1)
                point=Point(q['route_plan'].route.interpolate(q['interval'][1]).coords[0])
                same=transform(point,r.model.bound.centers[opposite],z[opposite])
                self.assertLess(transform(point,r.model.bound.centers[q['die']],end).distance(same),1e-8)
        finally:
            e.close()

    def test_same_die_has_two_controls_and_keeps_a_single_rigid_motion(self):
        e, _, copper = two_ended(different=False)
        try:
            r=e.reconstructor
            result=r.frame(np.array([[5.,-8.,.035]]))
            self.assertEqual(len(result['pieces']),2)
            self.assertNotEqual(result['pieces'][0]['via_refs'],result['pieces'][1]['via_refs'])
            geometry=result['replacements']['2/0'][0]['geometry']
            expected=transform(copper,r.model.bound.centers[0],np.array([5.,-8.,.035]))
            self.assertLess(geometry.symmetric_difference(expected).area,.1)
        finally:
            e.close()

    def test_positive_contact_does_not_count_as_full_pad_coverage(self):
        copper=box(0,0,100,20);pad=Point(5,10).buffer(11)
        free=shapely.Polygon()
        result=connected_candidate(copper,copper,[pad],free,None,15,.001,require_full=True)
        self.assertTrue(result.is_valid)
        self.assertLess(pad.difference(result).area,.02)
        obstacle=box(-20,9,-2,11)
        self.assertIsNone(connected_candidate(copper,copper,[pad],obstacle,None,15,.001,require_full=True))
        self.assertTrue(connected_candidate(copper,copper,[pad],obstacle,None,15,.001).equals(copper))

    def test_joint_trim_chooses_contour_that_preserves_larger_pad(self):
        left=box(-40,-20,12,20);right=box(0,-20,55,20)
        p=Point(0,0).buffer(2);q=Point(32,0).buffer(3)
        big=Point(0,0).buffer(10);other=Point(32,0).buffer(8)
        one,two=separate_pair(left,right,left,right,[p],[q],.001,protected=((big,),(other,)))
        self.assertFalse(one.intersects(two))
        self.assertLess(big.difference(one).area,.05)
        self.assertLess(other.difference(two).area,.05)

    def test_remote_shared_subgrid_problem_exports_a_real_error_marker(self):
        from die_shift_studio.measured_issues import catalogue
        from die_shift_studio.oas_errors import rectangles
        x,y=-243677.4,244296.1
        witness=LineString([(x,y),(x+30.,y+9.),(x+44.,y+13.2000000002)])
        case={'board':box(x-50,y-50,x+100,y+100)}
        location=dict(geometry=witness,point=(x,y),dependencies=(0,1),kind='连线失败')
        rows=catalogue({'drc_locations':[location]},case)
        groups=rectangles(rows,2,.001)
        self.assertEqual(set(groups),{1,2})
        self.assertTrue(shapely.from_wkb(groups[1][0]).covers(witness))


if __name__=='__main__':
    unittest.main()
