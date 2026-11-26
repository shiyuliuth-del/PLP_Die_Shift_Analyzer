"""Verify the default straight moving-side boundary and opt-in pad controls."""
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box

from die_shift_studio.manual_regions import aligned_partition, usable_partition
from die_shift_studio.plane_route import PlaneRoute
from die_shift_studio.reconstruction_edits import key, retire_pad_intervals
from die_shift_studio.settings import ViewSettings
from test_v38 import fixture


class MovingPadTests(unittest.TestCase):
    def test_old_interval_restores_as_nearest_line_and_preserves_far_cut(self):
        engine, _, _ = fixture()
        try:
            nominal = np.zeros((1, 3))
            plan = engine.reconstructor.frame(nominal)['pieces'][0]['route_plan']
            old = {'reconstruction_intervals': ((*key(plan), 0., 520.),)}
            disallowed = ViewSettings.from_dict(old)
            self.assertFalse(disallowed.reconstruction_allow_moving_pad)
            allowed = replace(disallowed, reconstruction_allow_moving_pad=True)
            self.assertNotEqual(disallowed.reconstruction_key(), allowed.reconstruction_key())
            self.assertTrue(ViewSettings.from_dict(asdict(allowed)).reconstruction_allow_moving_pad)

            for config, expected in ((allowed, 'Point'), (disallowed, 'LineString')):
                engine.configure_reconstruction(True, config)
                result = engine.reconstructor.frame(nominal)
                piece, = result['pieces']
                current_plan = piece['route_plan']
                self.assertEqual(piece['moved_cut'].geom_type, expected)
                self.assertAlmostEqual(piece['interval'][1], 520.)
                self.assertEqual(piece['extra_cut'].geom_type, 'LineString')
                self.assertFalse(result['hard_failures'])
                drawn, _ = current_plan.edit(*piece['interval'], 0, 0.)
                self.assertEqual(drawn['near'].geom_type, expected)
                union = shapely.union_all(list(piece['nominal_regions'].values()))
                self.assertLess(union.symmetric_difference(current_plan.source).area,1e-6)
                if not config.reconstruction_allow_moving_pad:
                    cut_lo=current_plan.via_limits()[0]
                    self.assertGreaterEqual(piece['interval'][0],cut_lo-1e-3)
                    self.assertLessEqual(piece['interval'][0],cut_lo+.02)
                    migrated = retire_pad_intervals(config, engine.reconstructor)
                    self.assertEqual(migrated.reconstruction_intervals[0][5:], piece['interval'])
                    shifted = engine.reconstructor.frame(np.array([[-15., 12., .01]]))
                    self.assertFalse(shifted['hard_failures'])
                    self.assertEqual(shifted['pieces'][0]['moved_cut'].geom_type, 'LineString')
        finally:
            engine.close()

    def test_boxes_covering_the_pad_never_create_a_dot_when_disabled(self):
        source = box(-35., -30., 220., 30.)
        route = LineString([(0., 0.), (190., 0.)])
        landing = Point(0., 0.).buffer(20.)
        plan = PlaneRoute('5/0', 0, 0, (), source, route, landing, (Point(190., 0.).buffer(10.),), .001, 3.)
        selection = box(-40., -35., 100., 35.)
        for enabled, expected in ((True, 'Point'), (False, 'LineString')):
            plan.pad_modes = (enabled, False)
            for part in (plan.partition(0., 100.), usable_partition(plan, selection), aligned_partition(plan, selection)):
                self.assertIsNotNone(part)
                self.assertEqual(part['near'].geom_type, expected)
                self.assertEqual(part['far'].geom_type, 'LineString')
                self.assertAlmostEqual(part['b'], 100.)
                self.assertTrue(shapely.union_all([part[k] for k in 'ABC']).equals(source))


class APDRulesTests(unittest.TestCase):
    def test_rules_cover_requested_domain_and_use_matching_apd_region_names(self):
        from die_shift_studio.apd_export import CFT, HEADER, OBJECTS, ROOT, rule_regions, template, write

        root=ET.Element(ROOT)
        ET.SubElement(root,f'{{{CFT}}}txt-FileType',Name='FileType').text='Design'
        ET.SubElement(root,f'{{{CFT}}}txt-FormatVersion',Name='FormatVersion').text='23.100'
        h=ET.SubElement(ET.SubElement(root,f'{{{CFT}}}xml-Header'),f'{{{HEADER}}}root',Name='DEMO')
        ET.SubElement(h,f'{{{HEADER}}}precision',units='um',numberOfDecimalPlaces='3')
        objects=ET.SubElement(root,f'{{{CFT}}}dir-objects')
        layers=ET.SubElement(ET.SubElement(objects,f'{{{CFT}}}xml-objects',Name='SubClass'),f'{{{OBJECTS}}}root')
        for name in ('Etch/RDL2','ConsRegion/RDL2'):
            ET.SubElement(layers,f'{{{OBJECTS}}}object',Name=name)
        domain=box(0,0,20,20).difference(box(3,3,6,6));board=box(-5,-5,25,25)
        x=np.array([0.,10.,20.])
        field=dict(base_spacing_um=15.,maps=[dict(owner=0,domain=domain,fixed_layers=['5/0'],
                   spacing=np.array([[16.,19.],[21.,24.]]),cell_allowance=.1,x_edges=x,y_edges=x)])
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'example.dcfx'
            ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
            source=template(path)
            for mode,coverage,values in (('moving',domain,{20,25}),('package',board,{15,20,25})):
                regions=rule_regions(field,board,[[0,0]],None,5.,mode)
                self.assertTrue(shapely.union_all([r[1] for r in regions['5/0']]).equals(coverage))
                self.assertAlmostEqual(sum(r[1].area for r in regions['5/0']),coverage.area)
                dcfx,il,count=write(Path(tmp)/f'{mode}.dcfx',regions,{'5/0':'RDL2'},source)
                rows=ET.parse(dcfx).findall('.//{*}object')
                self.assertEqual(len(rows),count)
                self.assertEqual({float(q.find('{*}attribute/{*}value').get('Value')) for q in rows},values)
                code=il.read_text()
                self.assertTrue(all(q.get('Name') in code for q in rows))
                self.assertIn('CONSTRAINT REGION/RDL2',code)
