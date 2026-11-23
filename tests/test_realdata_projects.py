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
        raw = json.loads((self.folder/'RealDataAnalyse/RealDataAnalyse.project.json').read_text())
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
        shutil.rmtree(self.folder/'RealDataAnalyse/Excel')
        (self.folder/'RealDataAnalyse/Excel').symlink_to(external_dir, target_is_directory=True)
        source = self.root/'outside.xlsx'; source.write_bytes(b'measurements')
        with self.assertRaisesRegex(ValueError, '文件必须位于'):
            copy_excel(self.folder, [source], threading.Event())
        self.assertEqual(list(external_dir.iterdir()), [])

    def test_excel_subfolder_follows_disk_while_retaining_user_selection(self):
        folder=self.folder
        config=load_folder(folder)
        files=config['files']
        removed=Path(files[0]['path'])
        hidden=Path(files.pop(1)['path'])
        files[0]['enabled']=False
        added=folder/'RealDataAnalyse/Excel/NewInput.xlsx'
        shutil.copyfile(hidden,added)  # arrived after loading, before saving
        save_folder(folder,config['project'],files,config['database'],config['output'],config['profile'],{},
                    template=config['template'],excluded_excel=[hidden])
        removed.unlink()
        loaded=load_folder(folder)
        self.assertEqual({Path(row['path']).name:row['enabled'] for row in loaded['files']},
                         {added.name:True,Path(files[1]['path']).name:True})
        self.assertEqual(loaded['template'],config['template'])

    def test_archive_wrapper_discovers_each_die_and_all_tables(self):
        excel=self.folder/'RealDataAnalyse/Excel'
        for table in excel.glob('*.xlsx'):
            table.unlink()
        wrapper=excel/'Partition0'
        for die, files in (('chip X',('01.CSV','02.xlsx')),('第二颗',('A.csv',))):
            directory=wrapper/die
            directory.mkdir(parents=True)
            for name in files:
                (directory/name).touch()
        found=load_folder(self.folder)
        self.assertEqual(found['measurement']['root'],str(wrapper))
        self.assertEqual(found['controls']['dies'],2)
        self.assertEqual({Path(item['path']).name for item in found['files']},{'01.CSV','02.xlsx','A.csv'})
        self.assertTrue(all(item['enabled'] for item in found['files']))

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

    def test_new_project_discovers_dropped_inputs(self):
        from die_shift_studio.jobs import provision_project
        config = load_folder(self.folder)
        new = self.root / 'new'
        workspace = provision_project(new)
        for source in (Path(config['project']), self.folder / 'GD55_1105.gds'):
            shutil.copy(source, new)
        template = shutil.copy(config['template'], workspace / 'Template')
        excel = shutil.copy(config['files'][0]['path'], workspace / 'Excel')
        shutil.copy(config['files'][1]['path'], new)
        loaded = load_folder(new)
        self.assertEqual(loaded['template'], template)
        self.assertEqual([record['path'] for record in loaded['files']], [excel])
        self.assertEqual(Path(loaded['database']).parent, workspace / 'Tasks')
        self.assertEqual(Path(loaded['output']).parent, workspace / 'Output')

    def test_flat_project_retains_existing_task_paths(self):
        config = load_folder(self.folder)
        workspace = Path(config['workspace'])
        (workspace / 'Excel').replace(self.folder / 'Excel')
        Path(config['template']).replace(self.folder / 'legacy.gds')
        shutil.rmtree(workspace)
        database = self.folder / 'Tasks' / 'last.sqlite'
        database.parent.mkdir()
        database.write_bytes(b'original journal')
        settings = self.folder / 'RealDataAnalyse.project.json'
        settings.write_text(json.dumps(dict(template='legacy.gds', database='Tasks/last.sqlite',
                                            output='Output/legacy.oas')), encoding='utf8')
        loaded = load_folder(self.folder)
        self.assertEqual(len(loaded['files']), 3)
        self.assertEqual(Path(loaded['database']), database)
        self.assertEqual(Path(loaded['output']), self.folder / 'Output/legacy.oas')
        self.assertEqual(Path(loaded['template']), self.folder / 'legacy.gds')
        self.assertEqual(database.read_bytes(), b'original journal')

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
        self.assertEqual(result['packages'], 36)
        ledger = Ledger(database)
        try:
            self.assertEqual(ledger.get('profiles')[str(second)], profile)
            payload = json.loads(ledger.db.execute('select payload from packages where seq=2').fetchone()[0])
            self.assertEqual([d['die'] for d in payload['dies']], [1,2,3])
            from openpyxl import load_workbook
            expected = []
            for file in paths:
                source = load_workbook(file,read_only=True)
                try:expected.append(source.active.cell(4 if file == str(second) else 3,3).value)
                finally:source.close()
            self.assertEqual([d['dx_um'] for d in payload['dies']], expected)
        finally: ledger.close()
        config['files'][1]['profile'] = profile
        save_folder(self.folder, config['project'], config['files'], str(database), config['output'], DEFAULT_PROFILE, {}, shared_mapping=False)
        again = load_folder(self.folder)
        self.assertFalse(again['shared_mapping'])
        self.assertEqual(again['files'][1]['profile'], profile)

    def test_project_change_between_prepare_and_batch_cannot_submit(self):
        from test_v44 import FakeService
        from RealDataAnalyse.excel import generate_example
        from RealDataAnalyse.runner import Runner
        from RealDataAnalyse.storage import Ledger
        paths = generate_example(self.root/'race-input', count=2, die_count=2)
        database = self.root/'race.sqlite'
        import_files(paths, database, 2, 2, threading.Event())
        class ChangedProject(FakeService):
            def request(self, command, args=None, **kwargs):
                reply = super().request(command, args, **kwargs)
                if command == 'batch.begin':
                    self.identity = 'a-different-frozen-project'
                return reply
        service = ChangedProject(self.root)
        output = self.root/'retained.oas'; output.write_bytes(b'previous valid target')
        runner = Runner(database, self.root/'endpoint.json', self.root/'case.json', output,
                        transport=service, retries=0, timeout=10)
        with self.assertRaisesRegex(ValueError, '工程身份或归属已变化'):
            runner.run()
        self.assertEqual(service.rows, {})
        self.assertEqual(output.read_bytes(), b'previous valid target')
        self.assertIsNone(service.batch_id)
        ledger = Ledger(database)
        try:self.assertEqual(ledger.get('state'), 'blocked')
        finally:ledger.close()

    def test_real_case_import_progress_counts(self):
        config = load_folder(self.folder)
        events = []
        result = import_files([x['path'] for x in config['files']], config['database'], 3, 3, threading.Event(), events.append)
        self.assertEqual(result['packages'], 36)
        self.assertTrue(any('封装匹配' in e['text'] for e in events))
        self.assertEqual(events[-1]['current'], events[-1]['total'])
        self.assertIn('36封装', events[-1]['text'])
        self.assertTrue(Path(config['database']).is_file())

if __name__ == '__main__': unittest.main()
