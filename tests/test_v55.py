"""Independent coverage of V55 catalogue, source, and physical output contracts."""
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gdstk
import numpy as np
import shapely
from shapely.geometry import box

from die_shift_studio.core import Parameters, read_gds
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.extreme_filter import PoseClause
from die_shift_studio.extreme_validation import catalogue_status
from die_shift_studio.manual_regions import aligned_partition, usable_partition
from die_shift_studio.resources import configure_limits, resource_profile
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from test_v38 import fixture


class V55Tests(unittest.TestCase):
    def test_resource_processes_scale_to_hardware_and_budget(self):
        memory = SimpleNamespace(total=64 * 1024**3, available=60 * 1024**3)
        with patch('os.cpu_count', return_value=32), patch('psutil.virtual_memory', return_value=memory), patch('pathlib.Path.read_text', side_effect=OSError):
            targets = patch('os.sched_getaffinity', return_value=set(range(32)), create=True)
            with targets:
                try:
                    configure_limits(28, 32768)
                    self.assertGreater(resource_profile()['process_workers'], 4)
                    configure_limits(28, 2304)
                    self.assertEqual(resource_profile()['process_workers'], 3)
                finally:
                    configure_limits()

    def test_zero_row_covers_off_page_failures_only_in_active_scope(self):
        grid = ExtremeCatalog({'dies': [box(0, 0, 100, 100)]}, Parameters())
        failure = grid.total - 1
        clause = PoseClause(tuple((i, (d,)) for i, d in enumerate(grid.choices(failure))))
        base = dict(checked=True, base=[0, 0, 0], local=[], moving_drc_local=[])
        ev = dict(complete=True, connection_factors=(clause,), red_factors=())
        self.assertEqual(catalogue_status(grid, grid, base, ev, reconstruct=True)['connection_failures'], 1)
        subset = SimpleNamespace(indices=(0,))
        self.assertTrue(catalogue_status(grid, subset, base, ev, reconstruct=True)['safe'])
        self.assertIsNone(catalogue_status(grid, grid, base, dict(ev, selected_indices=(-1, 0), connection_factors=()), reconstruct=True)['safe'])
        self.assertFalse(catalogue_status(grid, grid, base, dict(ev, connection_factors=(), red_factors=(clause,)), reconstruct=True)['safe'])

    def test_fixed_via_modes_carry_correct_geometry_to_export(self):
        engine, data, _ = fixture()
        try:
            state = np.array([[4., 7., .01]])
            cfg = ViewSettings(reconstruction_ignored=(('5/0', 0),), reconstruction_following=(('5/0', 0),))
            via = engine.contact.nodes[engine.contact.ids['6/0', 0]]['poly']
            expected = engine.contact.moved(via, 0, state)
            for mode, manual, moving in [('off', (), False), ('global', (), True),
                                         ('individual', (), False), ('individual', (('6/0', 0),), True)]:
                selected = replace(cfg, reconstruction_fixed_via_mode=mode, reconstruction_fixed_via_follows=manual)
                settings = ViewSettings.from_dict(asdict(selected))
                engine.configure_reconstruction(True, settings)
                frame = engine.frame(state)
                rows = frame['reconstruction']['replacements'].get('6/0', ())
                self.assertEqual(bool(rows), moving)
                frozen = snapshot(data, engine.bound.cache, frame, reconstruction=True, allow_partial=True)
                g = shapely.from_wkb(frozen.layers['6/0'][0])
                self.assertLess(g.symmetric_difference(expected if moving else via).area, 1e-7)
                self.assertTrue(engine.contact.nodes[engine.contact.ids['6/0', 0]]['poly'].equals(via))
        finally:
            engine.close()

    def test_manual_modes_and_legacy_fixed_endpoint(self):
        engine, _, _ = fixture()
        try:
            plan = engine.frame(np.zeros((1, 3)))['reconstruction']['pieces'][0]['route_plan']
            nominal = box(140, -60, 260, 60)
            polygon = shapely.affinity.rotate(nominal, 12)
            free = usable_partition(plan, polygon)
            aligned = aligned_partition(plan, polygon)
            self.assertTrue(free['strict_box'].equals(polygon))
            self.assertNotEqual(free['angles'], (0., 0.))
            self.assertEqual(aligned['angles'], (0., 0.))
            self.assertFalse(aligned.get('manual_angles'))
            migrated = plan.partition(100., plan.length)
            self.assertLess(migrated['b'], plan.fixed_start)
            self.assertEqual(migrated['far'].geom_type, 'LineString')
            near, _ = plan.edit(100., 500., 0, 0.)
            self.assertEqual(near['near'].geom_type, 'Point')
        finally:
            engine.close()

    def test_interleaved_terminals_get_disjoint_branch_domains(self):
        from die_shift_studio.reconstruction_ownership import divide
        source=box(-20,-25,320,25)
        pads=[shapely.Point(x,0).buffer(8) for x in (0,100,200,300)]
        groups=divide(source.wkb,tuple((i%2,pad.wkb) for i,pad in enumerate(pads)),15.,.001)
        self.assertEqual([len(groups[i]) for i in (0,1)],[2,2])
        fragments=[g for rows in groups.values() for g,_ in rows]
        self.assertTrue(shapely.union_all(fragments).equals(source))
        self.assertAlmostEqual(sum(g.area for g in fragments),source.area)
        for i,pad in enumerate(pads):
            self.assertTrue(any(g.covers(pad) for g,_ in groups[i%2]))

    def test_delete_all_scopes_shared_box_to_other_sources(self):
        from die_shift_studio.reconstruction_edits import change_zone
        ref=('5/0',0)
        other=('5/0',1)
        polygon=tuple(map(tuple,box(140,-60,260,60).exterior.coords[:-1]))
        config=ViewSettings(reconstruction_boxes=((ref[0],polygon),),
                            reconstruction_forced=(ref,other),reconstruction_following=(ref,other))
        calls=[]
        window=SimpleNamespace(view_settings=config,current_frame={},invalidate_reconstruction_edit=lambda:calls.append('edit'),
                               request_preview=lambda **kw:calls.append('preview'))
        change_zone(window,'delete_all',refs=(ref,))
        restored=ViewSettings.from_dict(asdict(window.view_settings))
        self.assertEqual(restored.reconstruction_box_excluded,(ref,))
        self.assertEqual(restored.reconstruction_ignored,(ref,))
        self.assertEqual(restored.reconstruction_boxes,config.reconstruction_boxes)
        self.assertEqual(restored.reconstruction_forced,(other,))
        self.assertEqual(restored.reconstruction_following,(other,))
        self.assertEqual(calls,['edit','preview'])

    def test_gds_oas_batched_import_keeps_source_index_and_material(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            lib = gdstk.Library()
            unit = lib.new_cell('UNIT')
            unit.add(gdstk.rectangle((0, 0), (4, 4), layer=5), gdstk.rectangle((4, 0), (8, 4), layer=5),
                     gdstk.rectangle((8, 4), (10, 6), layer=5))
            top = lib.new_cell('TOP')
            top.add(gdstk.Reference(unit, columns=40, rows=30, spacing=(20, 30)))
            lib.write_gds(root/'case.gds')
            lib.write_oas(root/'case.oas')
            for name in ('case.gds','case.oas'):
                data = read_gds(root/name, 'TOP')
                self.assertEqual(len(data.layers['5/0']), 2400)
                self.assertEqual(sorted(map(len, data.merge_sources['5/0'])), [1]*1200+[2]*1200)
                self.assertEqual(len(data.raw_to_merged), 3600)
                self.assertAlmostEqual(sum(shapely.Polygon(xy).area for xy in data.layers['5/0']), 43200.)


if __name__ == '__main__':
    unittest.main()
