"""Shared startup authorization and standalone signing-tool build."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from die_shift_studio import license_core as license
from LicenseManager.manager import generate, public_bytes, sign


SOURCE = Path(__file__).resolve().parents[1]
MACHINE = 'A'*64
BINDINGS = license.MachineBindings(license.IPV4_HWID,(MACHINE,))


class SharedLicenseTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.root = self.folder/'installed'
        self.root.mkdir()
        self.cached = self.folder/'profile'/'license.lic'
        self.key = Ed25519PrivateKey.generate()
        env = patch.dict(os.environ,{'DIE_SHIFT_LICENSE_PUBLIC_KEY':''})
        env.start()
        self.addCleanup(env.stop)

    def test_root_license_shared_by_both_frozen_entries(self):
        (self.root/'license.lic').write_text('invalid',encoding='utf8')
        _,content = sign(self.key,MACHINE)
        (self.root/'Machine.LIC').write_bytes(content)
        (self.root/'public_key.pem').write_bytes(public_bytes(self.key))
        for program in ('Die_Shift_Analyze.exe','RealDataAnalyse.exe'):
            extraction = self.folder/program/'_MEI'
            key_path,destination = license.frozen_public_key_data(
                self.root,extraction/'work')
            embedded=extraction/destination
            embedded.mkdir()
            shutil.copyfile(key_path,embedded/'license_public.pem')
            with patch.object(sys,'frozen',True,create=True), patch.object(sys,'_MEIPASS',str(extraction),create=True), \
                 patch.object(sys,'executable',str(self.root/program)), patch.object(license,'license_path',return_value=self.cached), \
                 patch.object(license,'machine_bindings',return_value=BINDINGS):
                self.assertEqual(license.installed_authorization().payload['machine_id'],MACHINE)
        self.assertFalse(self.cached.exists())

    def test_first_build_resolves_admin_key_and_prefers_explicit_key(self):
        admin = self.folder/'keys'
        root_public = self.root/'public_key.pem'
        with patch.object(license,'admin_key_directory',return_value=admin), \
             patch.dict(os.environ,{'DIE_SHIFT_LICENSE_PUBLIC_KEY':''}):
            with self.assertRaisesRegex(license.LicenseError,'LicenseManager_BUILD.bat'):
                license.build_public_key(self.root)
            _,generated = generate(admin,'Temporary test password')
            selected = license.build_public_key(self.root)
            self.assertEqual(selected,generated.public_key())
            root_public.write_bytes(public_bytes(self.key))
            self.assertEqual(license.build_public_key(self.root),self.key.public_key())
            with patch.dict(os.environ,{'DIE_SHIFT_LICENSE_PUBLIC_KEY':str(admin/'public_key.pem')}):
                self.assertEqual(license.build_public_key(self.root),generated.public_key())
            with patch.dict(os.environ,{'DIE_SHIFT_LICENSE_PUBLIC_KEY':str(admin/'absent.pem')}):
                with self.assertRaises(license.LicenseError):
                    license.build_public_key(self.root)

    def test_machine_id_is_displayed_even_without_public_key_and_can_retry(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from die_shift_studio.license_ui import LicenseDialog, W
        app=W.QApplication.instance() or W.QApplication([])
        with patch.object(license,'installation_root',return_value=self.root), \
             patch.object(license,'license_path',return_value=self.cached), \
             patch.object(license,'machine_bindings',return_value=BINDINGS), \
             patch.object(license,'admin_key_directory',return_value=self.folder/'empty'), \
             patch.dict(os.environ,{'DIE_SHIFT_LICENSE_PUBLIC_KEY':''}):
            dialog=LicenseDialog(application='RealDataAnalyse')
            self.assertTrue(dialog.query.wait(10000))
            app.processEvents()
            self.assertEqual(dialog.machine.text(),MACHINE)
            self.assertIn('RealDataAnalyse',dialog.windowTitle())
            self.assertTrue(dialog.copy.isEnabled())
            self.assertFalse(dialog.import_btn.isEnabled())
            self.assertIn('公钥',dialog.status.text())
            (self.root/'public_key.pem').write_bytes(public_bytes(self.key))
            (self.root/'Valid.lic').write_bytes(sign(self.key,MACHINE)[1])
            dialog.refresh_btn.click()
            self.assertTrue(dialog.query.wait(10000))
            app.processEvents()
            self.assertEqual(dialog.authorization.payload['machine_id'],MACHINE)
            self.assertIsNone(dialog.query)
            dialog.deleteLater()
            app.processEvents()

    def test_realdata_batch_rejects_missing_license_and_uses_root_license(self):
        from RealDataAnalyse.main import main
        config=self.folder/'input.json'
        config.write_text('{}',encoding='utf8')
        result=self.folder/'result.json'
        with patch.object(sys,'argv',['RealDataAnalyse','--import-data',str(config),'--result',str(result)]), \
             patch.object(sys,'frozen',True,create=True), \
             patch('RealDataAnalyse.storage.configure_logging'), patch('RealDataAnalyse.excel.import_files') as work, \
             patch.object(license,'embedded_public_key',return_value=self.key.public_key()), \
             patch.object(license,'installation_root',return_value=self.root), \
             patch.object(license,'license_path',return_value=self.cached), \
             patch.object(license,'machine_bindings',return_value=BINDINGS):
            self.assertEqual(main(),1)
            self.assertIn('.lic',json.loads(result.read_text(encoding='utf8'))['message'])
            work.assert_not_called()
            (self.root/'production.lic').write_bytes(sign(self.key,MACHINE)[1])
            config.write_text(json.dumps({'database':str(self.folder/'measurements.db')}),encoding='utf8')
            work.return_value={'state':'completed'}
            self.assertEqual(main(),0)
            work.assert_called_once()

    @unittest.skipUnless(sys.platform=='win32','Windows CMD entry point')
    def test_admin_batch_launches_builder_without_a_key(self):
        script = self.root/'LicenseManager_BUILD.bat'
        shutil.copyfile(SOURCE/script.name,script)
        fake = self.root/'PyInstaller'
        fake.mkdir()
        (fake/'__init__.py').touch()
        (fake/'__main__.py').write_text(
            'from pathlib import Path\nimport sys\n'
            'assert Path(sys.argv[-1]).name == "LicenseManager.spec"\n'
            'assert not Path("public_key.pem").exists()\n'
            'folder = Path(sys.argv[sys.argv.index("--distpath")+1])\n'
            'folder.mkdir()\n(folder/"LicenseManager.exe").touch()\n',encoding='utf8')
        env={**os.environ,'CI':'1','DIE_SHIFT_PYTHON':sys.executable,'PYTHONPATH':''}
        run=subprocess.run(['cmd.exe','/d','/c','call',script.name],cwd=self.root,env=env,
                           capture_output=True,text=True,timeout=25)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('dist_admin',run.stdout)


if __name__=='__main__':
    unittest.main()
