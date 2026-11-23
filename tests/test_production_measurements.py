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
    def test_opt_in_uses_failed_rows_offsets_and_preserves_successful_repeats(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            excel = root/'Excel'
            geometry = dict(board_origin_um=[0,0], centers_um=[[10,10],[50,20]])
            paths = []
            for name, cx, cy in (('left',10,10),('right',50,20)):
                path = excel/name/'measure.csv'
                path.parent.mkdir(parents=True)
                rows = []
                for n in range(3):
                    tx, ty = (1000*n+cx)/1000, cy/1000
                    rows.append([n,0,1,tx,ty,tx+.002,ty+.003,.002,.003,.1])
                if name == 'left':
                    rows[0][2:] = [0,.010,.010,'','',-.007,.004,-1.25]
                    rows[2][2:] = [0,2.010,.010,0,0,-.007,.004,'bad']
                    rows.append([1,0,0,1.010,.010,0,0,.090,.090,5])
                with path.open('w', encoding='utf-8-sig', newline='') as stream:
                    csv.writer(stream).writerows([HEADINGS,*rows])
                paths.append(path)
            digests = [sha256(p) for p in paths]
            arguments = dict(measurement=dict(root=str(excel)),geometry=geometry)
            default = import_files(paths, root/'default.sqlite', 2, 0, threading.Event(), **arguments)
            self.assertEqual((default['packages'],default['skipped_packages']),(1,2))
            enabled = import_files(paths, root/'enabled.sqlite', 2, 0, threading.Event(),
                                   ignore_measurement_failure=True, **arguments)
            self.assertEqual((enabled['packages'],enabled['skipped_packages']),(2,1))
            with closing(sqlite3.connect(enabled['database'])) as db:
                records = {site:json.loads(payload) for site, payload in db.execute('select package,payload from packages')}
                self.assertEqual(records['0:0:1']['measured_max'],[7.,4.,1.25])
                accepted = records['0:0:1']['dies'][0]
                self.assertAlmostEqual(accepted['dx_um'],-7)
                self.assertAlmostEqual(accepted['dy_um'],4)
                self.assertEqual(accepted['angle_deg'],-1.25)
                self.assertAlmostEqual(records['1:0:1']['dies'][0]['dx_um'],2)
                audits = db.execute('select payload from measurements order by package,die').fetchall()
                self.assertEqual(sum(json.loads(audit).get('measurement_failure_ignored',False) for audit, in audits),1)
            self.assertTrue(any('忽略测量失败' in message for message in enabled['warnings']))
            self.assertEqual([sha256(p) for p in paths],digests)

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
            recovered = root/'deduplicated.sqlite'
            result = import_files(paths+[duplicate], recovered, 2, 0, stop, measurement=dict(root=str(excel)), geometry=geometry)
            self.assertEqual(result['packages'], 2)
            self.assertTrue(any('重复' in message for message in result['warnings']))

    def test_folder_names_and_saved_order_do_not_change_physical_placement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            geometry = dict(board_origin_um=[-20, 30], centers_um=[[-10,40],[30,50]])
            file_names = (('102', '101'), ('晶片 右侧', 'a 左侧'))
            payloads = []
            for run, (left, right) in enumerate(file_names):
                excel = root / str(run)
                paths = []
                for name, cx, cy, angle in ((left,10,10,1.2),(right,50,20,-2.3)):
                    path = excel/name/'measure.csv'
                    path.parent.mkdir(parents=True)
                    with path.open('w', encoding='utf-8-sig', newline='') as stream:
                        csv.writer(stream).writerows([HEADINGS, [3,4,1,(1000+cx)/1000,(500+cy)/1000,
                                                                 (1002+cx)/1000,(503+cy)/1000,.002,.003,angle]])
                    paths.append(path)
                task = root/f'run{run}.sqlite'
                result = import_files(paths[::-1], task, 2, 0, threading.Event(), geometry=geometry,
                                      measurement=dict(root=str(excel), die_order=[right,left]))
                self.assertEqual(result['die_names'], [left,right])
                self.assertTrue(any('旧工程芯片顺序' in message for message in result['warnings']))
                with closing(sqlite3.connect(task)) as db:
                    payloads.append(db.execute('select payload from packages').fetchall())
            self.assertEqual(payloads[0], payloads[1])

    def test_isolated_bad_rows_and_extra_bad_file_keep_healthy_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            excel = root/'Excel'
            geometry = dict(board_origin_um=[0,0], centers_um=[[10,10],[50,20]])
            paths = []
            for i, (name, cx, cy) in enumerate((('逻辑',10,10),('B Die',50,20))):
                path = excel/name/'measurement.csv'
                path.parent.mkdir(parents=True)
                rows = []
                for n in range(6):
                    tx, ty = (1000*n+cx)/1000, cy/1000
                    row = [n,0,1,tx,ty,tx+.003,ty+.002,.003,.002,1]
                    if n == 0 and i == 0:
                        row[3] = 'broken theoretical X'
                    if n == 1 and i == 1:
                        row[9] = 'bad angle'
                    if n == 2 and i == 1:
                        row[7] = ''  # recover independently from two confirmed coordinates
                    rows.append(row)
                    if n == 3 and i == 0:
                        conflicting = row.copy(); conflicting[9] = -1
                        rows.append(conflicting)
                with path.open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(HEADINGS)
                    writer.writerow(rows[0])
                    if not i:
                        stream.write('"unterminated column\n')
                    writer.writerows(rows[1:])
                paths.append(path)
            bad_file = paths[0].with_name('bad.csv')
            bad_file.write_text('broken heading\n', encoding='utf8')
            result = import_files([bad_file,*paths], root/'run.sqlite', 2, 0, threading.Event(),
                                  geometry=geometry, measurement=dict(root=str(excel)))
            self.assertEqual(result['packages'], 3)
            self.assertEqual(result['skipped_packages'], 3)
            self.assertEqual(result['repaired_rows'], 1)
            self.assertEqual(result['rejected_rows'], 3)
            with closing(sqlite3.connect(result['database'])) as db:
                packages = [pkg for pkg, in db.execute('select package from packages order by seq')]
                self.assertEqual(packages, ['2:0:1','4:0:1','5:0:1'])
                warnings = '\n'.join(msg for msg, in db.execute("select message from events where level='WARNING'"))
                for term in ('已跳过文件','已跳过行','实测姿态','偏移X','冲突','缺少芯片','CSV引号'):
                    self.assertIn(term, warnings)
            with self.assertRaisesRegex(ValueError, '芯片位置|图纸位置'):
                import_files(paths, root/'bad_geometry.sqlite', 2, 0, threading.Event(),
                             geometry=dict(board_origin_um=[0,0], centers_um=[[10,10],[550,20]]),
                             measurement=dict(root=str(excel), die_order=['逻辑','B Die']))
            self.assertFalse((root/'bad_geometry.sqlite').exists())

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
