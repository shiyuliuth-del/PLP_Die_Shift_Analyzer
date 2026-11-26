"""Offline signature gates, standalone DXF, project lifecycle and paired zones."""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import ezdxf
import numpy as np
import shapely

from LicenseManager.manager import History, generate, load_private, sign
from die_shift_studio import license_core as license
from die_shift_studio.dxf_input import load
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.settings import ViewSettings
from test_v59 import two_ended

SYSTEM_UUID = '7cb989c3-9213-4c12-9f30-2a798834290d'


def example_dxf(path):
    doc=ezdxf.new()
    doc.units=13
    space=doc.modelspace()
    def path_on(layer, points, closed=False):
        space.add_lwpolyline(points,close=closed,dxfattribs={'layer':layer})
    path_on('PKG_REF',[(0,0),(400,0),(400,350),(0,350)],True)
    path_on('DIE_REF',[(5,5),(100,5),(100,100),(5,100)],True)
    for y in (20,41):
        path_on('L5_D0_A',[(8,y),(190,y)])
        path_on('L5_D0_C',[(190,y),(240,y)])
        path_on('L5_D0_B',[(240,y),(290,y)])
    path_on('L5_D0_A',[(8,20),(8,41)])
    path_on('L5_D0_B',[(290,20),(290,41)])
    block=doc.blocks.new('Round')
    block.add_circle((0,0),8)
    space.add_blockref('Round',(42,60),dxfattribs={'layer':'Other'})
    doc.saveas(path)


class AuthorizationTests(unittest.TestCase):
    def test_cim_canonical_fingerprint_and_fail_closed(self):
        expected=sha256(f'HWID_V1|UUID={SYSTEM_UUID.upper()}|BOARD=BOARD-001'.encode()).hexdigest().upper()
        self.assertEqual(license.fingerprint(' {'+SYSTEM_UUID+'} ',' board-001 '),expected)
        response=subprocess.CompletedProcess([],0,json.dumps({'uuid':[SYSTEM_UUID],'board':[' board-001 ']}),'')
        with patch.object(license.sys,'platform','win32'),patch.object(license.subprocess,'run',return_value=response) as run:
            self.assertEqual(license.windows_board_id(),expected)
            run.assert_called_once()
            self.assertIn('Get-CimInstance Win32_BaseBoard',run.call_args.args[0][-1])
            self.assertEqual(run.call_args.kwargs['timeout'],10)
            run.side_effect=subprocess.TimeoutExpired([],10)
            with self.assertRaisesRegex(license.LicenseError,'超时'):license.windows_board_id()
        for uuid,serial in (('0'*32,'BOARD-001'),(SYSTEM_UUID,'DEFAULT STRING'),(SYSTEM_UUID,None)):
            with self.assertRaises(license.LicenseError):license.fingerprint(uuid,serial)

    def test_sign_store_verify_tamper_and_history(self):
        first=license.fingerprint(SYSTEM_UUID,'ALPHA')
        second=license.fingerprint(SYSTEM_UUID,'BETA')
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            keys=root/'keys'
            private,key=generate(keys,'测试保护口令')
            original_key=private.read_bytes()
            self.assertIn(b'ENCRYPTED PRIVATE KEY',original_key)
            with self.assertRaises(license.LicenseError):generate(keys,'test')
            self.assertEqual(private.read_bytes(),original_key)
            with self.assertRaises(license.LicenseError):load_private(private,'wrong')
            key=load_private(private,'测试保护口令')
            target=root/'user'/'license.lic'
            state=license.Authorization(first,key.public_key(),target)
            with self.assertRaises(license.LicenseError):state.check()
            history=History(root)
            issued=history.issue(key,'测试姓名','测试工号',first,'QA',root/'files')
            original=issued.read_bytes()
            self.assertEqual(state.activate(issued)['user_name'],'测试姓名')
            self.assertEqual(license.Authorization(first,key.public_key(),target).check()['machine_id'],first)
            with self.assertRaisesRegex(license.LicenseError,'此电脑'):
                license.Authorization(second,key.public_key(),target).check()
            for field,changed in (('machine_id',second),('user_name','修改'),('product_id','OTHER'),('schema_version',9)):
                data=json.loads(original);data['payload'][field]=changed
                with self.assertRaisesRegex(license.LicenseError,'签名'):
                    license.verify(json.dumps(data).encode(),key.public_key(),first)
            invalid=root/'broken.lic'
            invalid.write_bytes(original.replace(b'"signature":',b'"signature":"x","signature":',1))
            with self.assertRaisesRegex(license.LicenseError,'重复'):state.activate(invalid)
            self.assertEqual(target.read_bytes(),original)
            _,other=generate(root/'other','pass')
            with self.assertRaisesRegex(license.LicenseError,'签名'):
                license.verify(sign(other,'other','employee',first)[1],key.public_key(),first)
            wildcard=history.issue(key,'Administrator','UNIVERSAL','*','QA',root/'admin',universal=True)
            shared=wildcard.read_bytes()
            for machine in (first,second):
                self.assertEqual(license.verify(shared,key.public_key(),machine)['machine_id'],'*')
            with self.assertRaises(license.LicenseError):license.verify(shared,key.public_key(),'')
            history.issue(key,'Administrator','UNIVERSAL','*','QA',root/'admin',universal=True)
            self.assertEqual(wildcard.read_bytes(),shared)
            row,=history.search('测试姓名')
            reexport=root/'reexport.lic'
            history.export_original(row[0],reexport)
            self.assertEqual(reexport.read_bytes(),original)
            history.export_csv(root/'licenses.csv')
            history.backup(root/'history-backup.db')
            self.assertTrue((root/'history-backup.db').stat().st_size)

    def test_activation_only_window(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from die_shift_studio.license_ui import LicenseDialog, W
        app=W.QApplication.instance() or W.QApplication([])
        with TemporaryDirectory() as tmp:
            p=Path(tmp)
            _,key=generate(p,'pass')
            machine=license.fingerprint(SYSTEM_UUID,'BOARD')
            state=license.Authorization(machine,key.public_key(),p/'local.lic')
            dlg=LicenseDialog(authorization=state)
            self.assertEqual(dlg.machine.text(),machine)
            self.assertTrue(dlg.machine.isReadOnly())
            self.assertFalse(dlg.apply_btn.isEnabled())
            selected=p/'one.lic';selected.write_bytes(sign(key,'tester','1',machine)[1])
            dlg.selected=selected
            dlg.apply()
            dlg.query.wait()
            app.processEvents()
            self.assertIsNotNone(state.payload)
            dlg.deleteLater();app.processEvents()


class GeometryLifecycleTests(unittest.TestCase):
    def test_pair_zone_count(self):
        engine,cfg,_=two_ended()
        try:
            self.assertEqual(ViewSettings().reconstruction_two_moving_zones,1)
            pose=np.array([[9.,13.,.013],[-5.,-12.,-.012]])
            for count in range(4):
                settings=replace(cfg,reconstruction_two_moving_zones=count)
                solver=Reconstructor(engine.reconstructor.model,engine.p,settings)
                try:
                    frame=solver.frame(np.zeros((2,3)))
                    self.assertEqual(len(frame['pieces']),count)
                    if count:
                        moved=solver.frame(pose)
                        self.assertFalse(moved['hard_failures'])
                        self.assertTrue(all(piece['regions']['A'].is_valid for piece in moved['pieces']))
                finally:solver.close()
        finally:engine.close()

    def test_standalone_dxf_and_empty_project_reset(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from die_shift_studio.gui import MainWindow,W
        from die_shift_studio.export_ui import import_regions
        from qt_shutdown import close_window
        app=W.QApplication.instance() or W.QApplication([])
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            source=folder/'standalone.dxf';example_dxf(source)
            w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1540,950);w.show()
            errors=[];w.error=lambda message:errors.append(str(message))

            def wait():
                end=monotonic()+30
                while monotonic()<end:
                    app.processEvents()
                    if not w._tasks and not w.viewport_timer.isActive():
                        self.assertFalse(errors,errors)
                        return
                    sleep(.01)
                self.fail('background operations did not finish')
            try:
                w.workspace_split.setSizes([900,600])
                app.processEvents()
                self.assertGreater(w.workspace_split.sizes()[0],530)
                self.assertGreater(w.workspace_split.sizes()[1],0)
                with patch.object(W.QFileDialog,'getOpenFileName',return_value=(str(source),'')):
                    import_regions(w)
                wait()
                self.assertIsNotNone(w.data)
                self.assertEqual(w.roles()['5/0'],'固定RDL')
                self.assertEqual(w.roles()['127/0'],'封装边框')
                self.assertEqual(w.roles()['128/0'],'芯片边框')
                self.assertEqual({key[1] for key in w.dxf_outline_artists},{'A','B','C'})
                actual=w.geometry_cache.layers['5/0']['geometry'][0]
                self.assertAlmostEqual(actual.symmetric_difference(shapely.box(8,20,290,41)).area,0.)
                saved=folder/'saved.dshift.json'
                w.fields['line_width_um'].setValue(69)
                w.write_project(saved);w.refresh_jobs()
                old=w.cache_group
                class Engine:
                    closed=False
                    def close(self):self.closed=True
                engine=Engine();old.engines['test']=engine;old.values['test']=dict(sample=1)
                w.job_combo.setCurrentIndex(0)
                wait()
                self.assertIsNone(w.data)
                self.assertFalse(w.role_boxes)
                self.assertFalse(w.dxf_outline_artists)
                self.assertEqual(w.fields['line_width_um'].value(),15.)
                self.assertTrue(engine.closed)
                self.assertFalse(old.values.memory)
                self.assertEqual(old.values['test'],dict(sample=1))
                w.load_project(saved);wait()
                self.assertEqual(Path(w.dxf_source),source.resolve())
                self.assertEqual(w.fields['line_width_um'].value(),69.)
                self.assertEqual({key[1] for key in w.dxf_outline_artists},{'A','B','C'})
                generic=folder/'arbitrary.dxf'
                other=ezdxf.new();other.units=4
                other.modelspace().add_circle((0,0),.05,dxfattribs={'layer':'External'})
                other.saveas(generic)
                with patch.object(W.QFileDialog,'getOpenFileName',return_value=(str(generic),'')):
                    import_regions(w)
                wait()
                self.assertEqual(list(w.roles().values()),['不参与'])
                self.assertFalse(w.dxf_outline_artists)
                self.assertIsNone(w.project_path)
            finally:
                close_window(app,w)
