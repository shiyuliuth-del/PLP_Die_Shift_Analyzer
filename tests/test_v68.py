"""DRC event probability, compensated source identity and measured safety."""
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
from shapely.geometry import LineString, box

from die_shift_studio.core import build_case
from die_shift_studio.drc_waivers import cleared_sources, event_id, summary, waivable
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.measured_core import Session
from die_shift_studio.reconstruction_joint import scan
from die_shift_studio.reconstruction_probability import extreme_weights
from die_shift_studio.resources import serial_scope
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from test_v38 import fixture


class V68Tests(TestCase):
    def test_failure_outline_and_dashed_segments_exclude_clean_sibling(self):
        from types import SimpleNamespace
        from die_shift_studio.reconstruction_diagnostic import display
        from die_shift_studio.reconstruction_events import active_region,zone_severity

        source=box(0,0,100,10)
        healthy=box(55,0,90,10)
        bad=dict(layer='5/0',index=4,die=0,interval=(0.,100.),connection_failed=True,
                 regions=dict(A=source),route_plan=SimpleNamespace(partition=lambda *_:{}))
        good=dict(layer='5/0',index=4,die=0,connection_failed=False,regions=dict(A=healthy))
        result=dict(pieces=[bad,good],hard_failures=[(('5/0',4),'连接失败')],
                    replacements={'5/0':[dict(indices=(4,),geometry=source)]})
        sections=(None,None,None,None,np.array([[0.,0.],[0.,10.]]),np.array([[100.,0.],[100.,10.]]))
        with patch('die_shift_studio.connection_curves.connection_data',return_value=sections):
            frame=display(result,np.zeros((1,2)),np.zeros((1,3)))
        rejected,clean=frame['pieces']
        self.assertEqual(zone_severity(rejected),'red')
        self.assertEqual(zone_severity(clean),'green')
        self.assertTrue(active_region(rejected).equals(source.difference(healthy)))
        self.assertEqual(rejected['diagnostic_outline'].intersection(healthy).length,0.)
        self.assertTrue(frame['replacements']['5/0'][0]['geometry'].equals(healthy))
        self.assertEqual(len(frame['failed_segments']),4)
        for edge in frame['failed_segments']:
            self.assertEqual(LineString(edge['points']).intersection(healthy).length,0.)

    def test_corner_snap_distance_is_optional_and_local(self):
        from die_shift_studio.reconstruction_route import RoutePlan
        route=LineString([(0,0),(80,0),(80,100)])
        plan=RoutePlan('5/0',0,0,(),route.buffer(8),route,box(-2,-3,2,3),(box(76,97,84,101),),.001,0)
        self.assertEqual(ViewSettings().reconstruction_corner_guard_um,0)
        self.assertEqual(plan.snap(79,0,150,corner_snap=-1),79)
        self.assertEqual(plan.snap(79,0,150,corner_snap=5),75)
        self.assertEqual(plan.snap(79,0,150,corner_snap=.5),79)

    def test_exact_state_event_mass_and_strict_threshold(self):
        engine,_,case=fixture()
        try:
            solver=engine.reconstructor
            settings=replace(solver.settings,reconstruction_auto_waive_drc=True)
            solver.settings=settings
            grid=ExtremeCatalog(case,solver.p)
            rare=grid.index([grid.radix-1])
            common='common';rare_rule='rare'
            def events(_solver,states,_frame):
                hits=(rare_rule,) if np.array_equal(states,grid[rare]['states']) else ()
                return (common,*hits),(),{('5/0',0):hits} if hits else {},False
            with serial_scope(),patch('die_shift_studio.drc_waivers.scene_events',side_effect=events):
                bank=scan(solver,grid,None)
            probability=bank['drc_event_probability']
            weights,_=extreme_weights(grid,solver.p)
            self.assertAlmostEqual(probability[rare_rule],weights[0][-1],11)
            self.assertEqual(probability[common],1.)
            self.assertTrue(bank['drc_event_sources'])
            auto,passed,cleared=summary(settings,bank,{},None,grid,lambda index:grid[index]['states'])
            self.assertEqual(auto,{rare_rule})
            self.assertFalse(passed)
            self.assertEqual(cleared,{('5/0',0):{rare}})
            self.assertEqual(summary(replace(settings,reconstruction_auto_waive_drc=False),bank,{},None,grid,None),(set(),set(),{}))
        finally:engine.close()

    def test_source_gate_uses_every_problem_and_retains_electrical_failure(self):
        a=dict(kind='线宽不足',refs=(('5/0',3),),layer='5/0',geometry=box(1,0,4,2))
        b=dict(a,kind='最小内角不足',geometry=box(10,0,11,1))
        q=dict(layer='5/0',index=3,rule_witnesses=[a,b])
        frame=dict(reconstruction=dict(pieces=[q]))
        self.assertFalse(cleared_sources(frame,[a,b],[True,False]))
        self.assertEqual(cleared_sources(frame,[a,b],[True,True]),{('5/0',3)})
        self.assertNotEqual(event_id(a),event_id(b))
        self.assertEqual(event_id(a),event_id(dict(a,geometry=box(-3,-3,-1,-1))))
        self.assertFalse(waivable(dict(a,kind='异网短接',short_circuit=True)))
        frame['reconstruction']['hard_failures']=[(('5/0',3),'连接失败')]
        self.assertFalse(cleared_sources(frame,[a,b],[True,True]))

    def test_skip_measured_size_drc_keeps_forbidden_conductors_and_real_output(self):
        engine,data,_=fixture()
        static_narrow=box(680,0,750,2)
        keepout=box(690,0,700,6)
        data=replace(data,layers={**data.layers,'5/0':[*data.layers['5/0'],np.asarray(static_narrow.exterior.coords)],
                                  '10/0':[np.asarray(keepout.exterior.coords)]})
        roles=dict(engine.roles,**{'10/0':'禁布区'})
        case=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0','10/0'])
        session=Session(data,case,roles,{},engine.p,ViewSettings(),GeometryCache(data))
        rows=[dict(die=1,dx_um=0.,dy_um=0.,angle_deg=0.)]
        try:
            with serial_scope():
                _,checked,details=session.run(rows,preview=False)
                _,skipped,result=session.run(rows,preview=False,skip_drc=True)
                _,_,repeated=session.run(rows,preview=False)
            self.assertEqual(checked.layers,skipped.layers)
            self.assertTrue(any('线宽' in q['kind'] for q in details['drc_issues']))
            self.assertEqual(details,repeated)
            self.assertTrue(result['skip_drc'])
            self.assertIsNone(result['drc_passed'])
            self.assertFalse(result['fixed_rdl_drc_checked'])
            self.assertTrue(any('禁布' in q['kind'] and q['severity']=='serious' for q in result['drc_issues']))
            self.assertFalse(any('线宽' in q['kind'] for q in result['drc_issues']))
        finally:session.close();engine.close()
