"""Layer DXF recovery, figure waivers and Windows work-budget arithmetic."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import shapely
from shapely.geometry import box

from die_shift_studio.drc_waivers import event_id,figure_decision,figure_refs,summary
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_edits import key
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_import import configure,metadata_for,read,read_domains,recover_metadata,validate_rebuild
from die_shift_studio.resources import geometry_budget
from die_shift_studio.runtime_log import LogBus
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from test_v38 import fixture


class V70Tests(TestCase):
    def test_large_worker_budget_converts_numpy_counts_before_multiplication(self):
        profile=dict(memory_budget_mb=64*1024,available_memory_mb=128*1024,workers=32)
        self.assertEqual(geometry_budget(profile,np.int64(4_000_000_000)),2)
        self.assertEqual(geometry_budget(profile,np.int64(1<<61)),1)
        self.assertEqual(geometry_budget(dict(available_memory_mb=128,workers=4),np.int64(10)),2)

    def test_parallel_source_batches_match_serial_copper_at_every_pose(self):
        from test_v59 import two_ended
        from die_shift_studio.reconstruction_joint import prepare
        from die_shift_studio.reconstruction_status import clone_plan_pool
        from die_shift_studio.resources import resource_profile
        engine,_,_=two_ended()
        try:
            r=engine.reconstructor
            grid=ExtremeCatalog(r.model.case,r.p)
            picks=(-1,*range(0,grid.total,15))
            profile=resource_profile()
            r._plans_for_all_poses(None)
            with patch('die_shift_studio.resources.resource_profile',return_value=dict(profile,workers=1)):
                serial=prepare(r,grid,indices=picks)
            r._joint_catalog=None;r.geometry_pool.clear()
            with patch('die_shift_studio.resources.resource_profile',return_value=dict(profile,workers=4,worker_capacity=4)), \
                 patch('die_shift_studio.reconstruction_status.clone_plan_pool',wraps=clone_plan_pool) as clones:
                parallel=prepare(r,grid,indices=picks)
            self.assertGreater(clones.call_count,1)
            a=serial['2/0',0]['values'];b=parallel['2/0',0]['values']
            self.assertEqual(a.keys(),b.keys())
            for digits in a:self.assertTrue(a[digits]['geometry'].equals_exact(b[digits]['geometry'],1e-9,normalize=True),digits)
        finally:engine.close()

    def test_figure_waiver_covers_all_catalogue_poses_and_restores_events(self):
        engine,_,case=fixture()
        try:
            ref=('5/0',0);other=('5/0',1)
            row=dict(kind='线宽不足',refs=(ref,),geometry=box(0,0,4,2))
            second=dict(kind='线距不足',refs=(ref,other),geometry=box(10,0,14,1))
            short=dict(second,kind='异网短接',short_circuit=True)
            a,b,c=map(event_id,(row,second,short))
            bank=dict(complete=True,drc_event_probability={a:1.e-9},drc_event_refs={a:(ref,),b:(ref,other),c:()},
                      drc_event_poses={0:(a,),1:(a,b),2:(a,c)},drc_event_sources={ref:{0:(a,),1:(a,b)}})
            grid=ExtremeCatalog(case,engine.p)
            cfg=ViewSettings(drc_waived_refs=(ref,))
            self.assertEqual(summary(cfg,bank,{},None,grid,None)[1:],({0,1},{ref:{0,1}}))
            retained=replace(cfg,drc_waived_refs=(),drc_retained_refs=(ref,),reconstruction_auto_waive_drc=True)
            self.assertEqual(summary(retained,bank,{},None,grid,None)[1:],(set(),{}))
            self.assertIs(figure_decision(figure_refs(second),{other},{ref}),False)
            self.assertIsNone(figure_decision(figure_refs(short),{ref},set()))
        finally:engine.close()

    def test_runtime_buffer_keeps_history_and_stops_writing_in_measured_mode(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'runtime.log'
            log=LogBus(path)
            log.emit('first')
            index,lines=log.read_after(0)
            self.assertEqual(index,1)
            self.assertTrue(lines[0].endswith('[INFO] first'))
            log.set_enabled(False)
            log.emit('unrecorded','ERROR')
            self.assertEqual(log.read_after(index),(index,[]))
            self.assertNotIn('unrecorded',path.read_text())
            log.set_enabled(True)
            log.emit('saved')
            self.assertEqual(len(log.read_after(index)[1]),1)

    def test_dxf_roundtrip_restores_an_angled_cut_with_and_without_metadata(self):
        engine,data,case=fixture()
        try:
            original=engine.reconstructor
            src=original.frame(np.zeros((1,3)))['pieces'][0]['route_plan']
            identity=key(src);a,b=105.,490.
            cfg=replace(original.settings,reconstruction_forced=(('5/0',0),),
                        reconstruction_intervals=((*identity,a,b),),
                        reconstruction_angles=((*identity,0,1.35),(*identity,1,1.7)))
            solver=Reconstructor(original.model,original.p,cfg)
            try:frame=solver.frame(np.zeros((1,3)))
            finally:solver.close()
            edit_zones=[q['nominal_regions']['A'] for q in frame['pieces']]
            frame['pieces']=[dict(q,regions=q['nominal_regions']) for q in frame['pieces']]
            dxf=DxfSettings(scale=2)
            offsets={'5/0':1.5}
            with TemporaryDirectory() as tmp:
                path=Path(tmp)/'ABC.dxf'
                write_regions(snapshot(data,original.model.cache),frame,engine.roles,path,settings=dxf,
                              compensation=offsets,dbu=.001,edit_metadata=metadata_for(
                                  data,engine.roles,case['connections'].order,original.p,cfg,['5/0'],dxf,offsets))
                contours,record=read(path,with_metadata=True)
                restored=recover_metadata(record,contours,data,engine.roles,case['connections'].order,original.p,original.settings)
                self.assertEqual(restored.reconstruction_intervals,cfg.reconstruction_intervals)
                self.assertTrue(validate_rebuild(original.model,original.p,restored,contours,dxf,offsets))
                imported=configure(original.model,original.p,original.settings,
                                   read_domains(contours,original.model.cache,engine.roles,offsets,dxf.scale,.001))
                r=Reconstructor(original.model,original.p,imported)
                try:actual=[q['nominal_regions']['A'] for q in r.frame(np.zeros((1,3)))['pieces']]
                finally:r.close()
                self.assertLess(shapely.union_all(edit_zones).symmetric_difference(shapely.union_all(actual)).area,.1)
                moved={layer:{name:[shapely.transform(g,lambda xy:xy+[91.,-73.]) for g in geos]
                                      for name,geos in parts.items()} for layer,parts in contours.items()}
                self.assertIsNone(recover_metadata(record,moved,data,engine.roles,case['connections'].order,original.p,original.settings))
                with self.assertRaisesRegex(ValueError,'无法唯一匹配'):
                    read_domains(moved,original.model.cache,engine.roles,offsets,dxf.scale,.001)
        finally:engine.close()
