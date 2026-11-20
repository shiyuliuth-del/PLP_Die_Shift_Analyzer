"""Geometric and input contract for production lots and separated issue layers."""
from contextlib import closing
import csv
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from RealDataAnalyse.excel import import_files
from RealDataAnalyse.measurements import HEADINGS
from RealDataAnalyse.projects import load_folder
from RealDataAnalyse.storage import sha256

ROOT = Path(__file__).resolve().parents[1]


class ProductionMeasurements(unittest.TestCase):
    def test_attachment_format_multiple_files_actual_centers_and_repeated_indices(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            excel = root/'Excel'
            geometry = dict(board_origin_um=[-20, 30], centers_um=[[-10, 40], [30, 50]])
            dies = {'101': [50, 20], 'ASIC': [10, 10]}
            paths = []
            for die, (cx, cy) in dies.items():
                folder = excel/die
                folder.mkdir(parents=True)
                for part, (bx, by) in enumerate(((1000., 4000.), (-4000., -5000.), (0., 0.))):
                    status = int(part != 2 or die == 'ASIC')
                    row = [0, 0, status, (bx+cx)/1000, (by+cy)/1000,
                           (bx+cx+6)/1000, (by+cy-2)/1000, 0, 0, 12 if part == 0 else -2]
                    path = folder/(f'{part}.xlsx' if part == 0 and die == 'ASIC' else f'{part}.csv')
                    if path.suffix == '.xlsx':
                        book = Workbook(write_only=True)
                        sheet = book.create_sheet('工艺')
                        sheet.append(HEADINGS)
                        sheet.append(row)
                        book.save(path)
                    else:
                        with path.open('w', encoding='gb18030', newline='') as file:
                            csv.writer(file).writerows([HEADINGS, row])
                    paths.append(path)
            stop = threading.Event()
            digest = [sha256(p) for p in paths]
            database = root/'tasks.sqlite'
            result = import_files(paths[::-1], database, 2, 0, stop, measurement=dict(root=str(excel)), geometry=geometry)
            self.assertEqual(result['packages'], 2)
            self.assertEqual(result['skipped_packages'], 1)
            self.assertEqual(result['die_names'], ['ASIC', '101'])
            with closing(sqlite3.connect(database)) as db:
                actual = [json.loads(payload) for payload, in db.execute('select payload from packages order by seq')]
                self.assertEqual(actual[0]['position_um'], [-4000, -5000])
                self.assertEqual(actual[1]['position_um'], [1000, 4000])
                for package in actual:
                    for pose, center in zip(package['dies'], geometry['centers_um']):
                        self.assertAlmostEqual(pose['dx_um'], 6)
                        self.assertAlmostEqual(pose['dy_um'], -2)
                        self.assertEqual(pose['rotation_center_um'], center)
                self.assertEqual(json.loads(db.execute("select value from meta where key='skipped_packages'").fetchone()[0]), 1)
            self.assertEqual([sha256(p) for p in paths], digest)
            duplicate = excel/'101'/'duplicate.csv'
            duplicate.write_bytes(paths[0].read_bytes())
            bad = root/'reject.sqlite'
            with self.assertRaisesRegex(ValueError, '无法唯一匹配'):
                import_files(paths+[duplicate], bad, 2, 0, stop, measurement=dict(root=str(excel)), geometry=geometry)
            self.assertFalse(bad.exists())

    def test_shipped_production_fixture_discovers_two_dies_and_four_files(self):
        config = load_folder(ROOT/'Jobs/SH22')
        self.assertEqual(config['controls']['dies'], 2)
        self.assertEqual(config['controls']['workers'], 0)
        self.assertEqual(config['controls']['memory'], 0)
        self.assertEqual(len(config['files']), 4)


class SeverityMarkers(unittest.TestCase):
    def test_minimum_oriented_witness_and_severe_markers_read_back_on_own_layer(self):
        import gdstk
        import klayout.db as k
        import shapely
        from shapely.affinity import rotate
        from die_shift_studio.current_drc import locations
        from die_shift_studio.measured_issues import catalogue
        from die_shift_studio.oas_stream import Store

        wafer = shapely.box(-20, -20, 30, 30)
        witness = rotate(shapely.box(0, 0, 10, 1), 33)
        clearance = rotate(shapely.box(0, 5, 10, 6), 33)
        short_pair = (witness, witness)
        items = [dict(geometry=witness.union(clearance), kind='异网短接 / 线距不足', _measurement_pair=short_pair,
                      nets=(1, 2), owner=0),
                 dict(geometry=rotate(shapely.box(12, 10, 22, 12), -19),
                      kind='重构连接失败', owner=0, connection_failure=True)]
        issues = catalogue(dict(drc_locations=locations(items, measure=False, separate_shorts=True), states=[[0, 0, .4]]),
                           dict(board=wafer))
        self.assertEqual(sorted(row['severity'] for row in issues), ['drc', 'serious', 'serious'])
        short = next(row for row in issues if row['short_circuit'])
        self.assertAlmostEqual(shapely.Polygon(short['marker_corners_um']).area, 10.)
        self.assertAlmostEqual(math.remainder(short['marker_angle_deg']-33, 90), 0.)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'test.oas'
            store = Store(folder, .001, error_layer=(65535,0), error2_layer=(65534,0), failure_layer=(65533,0))
            try:
                store.add('Test', {}, wafer.bounds[:2], wafer.bounds[:2], errors=issues, die_count=1, failure_outline=wafer.wkb)
                result = store.write(output)
            finally:
                store.close()
            self.assertEqual(result['error_rectangles'], 3)
            self.assertEqual(result['error2_rectangles'], 2)
            layout = k.Layout(); layout.read(str(output))
            self.assertEqual({x.name for x in layout.layer_infos()}, {'ERROR', 'ERROR2', 'FAIL'})
            cells = {cell.name: cell for cell in gdstk.read_oas(output).cells}
            self.assertEqual(len(cells['G_ERROR_Test_DIE_1'].get_polygons()), 1)
            polygons = cells['G_ERROR2_Test_DIE_1'].get_polygons()
            self.assertEqual(len(polygons), 2)
            self.assertTrue(all(len(p.points)==4 for p in polygons))
            self.assertTrue(any(shapely.Polygon(p.points).covers(witness) for p in polygons))


if __name__ == '__main__':
    unittest.main()
