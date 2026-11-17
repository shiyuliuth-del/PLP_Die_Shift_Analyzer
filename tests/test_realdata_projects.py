"""Portable project ownership, input selection, and progress integrity."""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from RealDataAnalyse.projects import load_folder, save_folder, inside, copy_excel
from RealDataAnalyse.excel import DEFAULT_PROFILE, import_files
from RealDataAnalyse.storage import sha256

ROOT = Path(__file__).resolve().parents[1]

class Projects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
