"""Portable project ownership, input selection, and progress integrity."""
import json
import copy
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from RealDataAnalyse.projects import load_folder, save_folder, inside, copy_excel, install_case
from RealDataAnalyse.excel import DEFAULT_PROFILE, import_files
from RealDataAnalyse.storage import sha256

ROOT = Path(__file__).resolve().parents[1]

class Projects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / '项目 空格'
        shutil.copytree(ROOT / 'Jobs/GD55', self.folder)

    def test_folder_move_and_selection_roundtrip(self):
        data = load_folder(self.folder)
        self.assertEqual(len(data['files']), 3)
        data['files'][1]['enabled'] = False
        save_folder(self.folder, data['project'], data['files'], data['database'], data['output'], DEFAULT_PROFILE, {'workers': 5})
        raw = json.loads((self.folder/'RealDataAnalyse.project.json').read_text())
        self.assertFalse(Path(raw['project']).is_absolute())
        moved = self.root/'moved'
        shutil.copytree(self.folder, moved)
        loaded = load_folder(moved)
        self.assertEqual(loaded['controls']['workers'], 5)
        self.assertFalse(loaded['files'][1]['enabled'])
        for key in ('project','database','output'):
            self.assertTrue(Path(loaded[key]).is_relative_to(moved))

    def test_escape_missing_source_and_ambiguity(self):
        with self.assertRaises(ValueError): inside(self.folder, '../outside.sqlite')
        with self.assertRaises(ValueError): inside(self.folder, self.root/'outside.oas')
        shutil.copy(self.folder/'GD55.dshift.json', self.folder/'duplicate.json')
        with self.assertRaisesRegex(ValueError, '唯一'): load_folder(self.folder)
        (self.folder/'duplicate.json').unlink()
        (self.folder/'GD55_1105.gds').unlink()
        with self.assertRaisesRegex(ValueError, '不存在'): load_folder(self.folder)

    def test_project_symlinks_cannot_escape_folder(self):
        external = self.root/'outside.json'
        external.write_bytes((self.folder/'GD55.dshift.json').read_bytes())
        target = self.folder/'GD55.dshift.json'
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError:
            self.skipTest('This Windows account cannot create symbolic links')
        with self.assertRaisesRegex(ValueError, '文件必须位于'):
            load_folder(self.folder)
        external_dir = self.root/'outside-excel'
        external_dir.mkdir()
        (self.folder/'Excel').symlink_to(external_dir, target_is_directory=True)
        source = self.root/'outside.xlsx'; source.write_bytes(b'measurements')
        with self.assertRaisesRegex(ValueError, '文件必须位于'):
            copy_excel(self.folder, [source], threading.Event())
        self.assertEqual(list(external_dir.iterdir()), [])

    def test_external_copy_collision_and_cancel(self):
        external = self.root/'measurements.xlsx'
        external.write_bytes(b'first')
        one = Path(copy_excel(self.folder, [external], threading.Event())[0])
        original = sha256(one)
        external.write_bytes(b'second')
        two = Path(copy_excel(self.folder, [external], threading.Event())[0])
        self.assertNotEqual(one, two)
        self.assertEqual(sha256(one), original)
        self.assertEqual(two.read_bytes(), b'second')
        stop = threading.Event(); stop.set()
        with self.assertRaises(InterruptedError): copy_excel(self.folder, [external], stop)

    def test_build_installs_case_without_overwriting_user_project(self):
        destination = self.root/'dist'
        install_case(destination)
        project = destination/'Jobs/GD55/GD55.dshift.json'
        self.assertEqual(project.read_bytes(), (ROOT/'Jobs/GD55/GD55.dshift.json').read_bytes())
        project.write_text('user changes')
        install_case(destination)
        self.assertEqual(project.read_text(), 'user changes')

    def test_different_sheet_header_and_columns_per_excel(self):
        from openpyxl import load_workbook
        from RealDataAnalyse.storage import Ledger
        config = load_folder(self.folder)
        paths = [entry['path'] for entry in config['files']]
        second = Path(paths[1])
        book = load_workbook(second)
        sheet = book['Measurements']; sheet.title = 'DieTwo'
        sheet.insert_rows(1)
        for cell in sheet[2]:
            if cell.value is not None: cell.value = 'D2_' + str(cell.value)
        book.save(second); book.close()
        profile = copy.deepcopy(DEFAULT_PROFILE)
        profile.update(sheet='DieTwo', header_row=2)
        profile['columns'] = {k: 'D2_' + v for k, v in profile['columns'].items()}
        with self.assertRaises(ValueError):
            import_files(paths, self.folder/'wrong.sqlite', 3, 3, threading.Event())
        database = self.folder/'correct.sqlite'
        result = import_files(paths, database, 3, 3, threading.Event(), profiles={str(second): profile})
        self.assertEqual(result['packages'], 12)
        ledger = Ledger(database)
        try:
            self.assertEqual(ledger.get('profiles')[str(second)], profile)
            payload = json.loads(ledger.db.execute('select payload from packages where seq=2').fetchone()[0])
            self.assertEqual([d['die'] for d in payload['dies']], [1,2,3])
            self.assertEqual([d['dx_um'] for d in payload['dies']], [1,0,0])
        finally: ledger.close()
        config['files'][1]['profile'] = profile
        save_folder(self.folder, config['project'], config['files'], str(database), config['output'], DEFAULT_PROFILE, {}, shared_mapping=False)
        again = load_folder(self.folder)
        self.assertFalse(again['shared_mapping'])
        self.assertEqual(again['files'][1]['profile'], profile)

    def test_real_case_import_progress_counts(self):
        config = load_folder(self.folder)
        events = []
        result = import_files([x['path'] for x in config['files']], config['database'], 3, 3, threading.Event(), events.append)
        self.assertEqual(result['packages'], 12)
        self.assertTrue(any('封装匹配' in e['text'] for e in events))
        self.assertEqual(events[-1]['current'], events[-1]['total'])
        self.assertIn('12封装', events[-1]['text'])
        self.assertTrue(Path(config['database']).is_file())

if __name__ == '__main__': unittest.main()
