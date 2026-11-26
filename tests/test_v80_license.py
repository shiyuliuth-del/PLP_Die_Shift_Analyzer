"""Windows IPv4 machine IDs and compatibility with signed V79 licenses."""
from base64 import b64encode
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from die_shift_studio import license_core as license
from LicenseManager.manager import History, sign

UUID = '7CB989C3-9213-4C12-9F30-2A798834290D'


def adapter(addresses, gateways=(), *, metric=50, index=1):
    return dict(addresses=list(addresses), gateways=list(gateways), metric=metric,index=index)


def digest(address):
    return hashlib.sha256(f'HWID_V2|UUID={UUID}|IPV4={address}'.encode('ascii')).hexdigest().upper()


class IPv4LicenseTests(unittest.TestCase):
    def test_cim_uses_uuid_and_local_network_without_board_query(self):
        adapters = [
            adapter(['127.0.0.1','169.254.23.4','0.0.0.0','224.14.0.1','::1']),
            adapter(['192.168.56.1'],metric=1,index=7),
            adapter(['192.168.8.7','fe80::17'],['192.168.8.1'],metric=400,index=52),
            adapter(['10.1.1.20','10.1.1.12'],['10.1.1.1'],metric=30,index=8),
        ]
        value = dict(uuid=[UUID.lower()],adapters=adapters)
        proc = subprocess.CompletedProcess([],0,json.dumps(value),'')
        with patch.object(license.sys,'platform','win32'), patch.object(license.subprocess,'run',return_value=proc) as run:
            binding=license.machine_bindings()
        self.assertEqual(binding.version, 2)
        self.assertEqual(binding.codes,tuple(map(digest,('10.1.1.12','10.1.1.20','192.168.8.7','192.168.56.1'))))
        self.assertEqual(license.ipv4_bindings(UUID,list(reversed(adapters))),binding)
        run.assert_called_once()
        command=run.call_args.args[0][-1]
        self.assertIn('Win32_ComputerSystemProduct', command)
        self.assertIn('Win32_NetworkAdapterConfiguration', command)
        self.assertNotIn('Win32_BaseBoard', command)
        self.assertEqual(run.call_args.kwargs['timeout'], 10)
        with self.assertRaisesRegex(license.LicenseError,'IPv4'):
            license.ipv4_bindings(UUID,adapters[:1])

    def test_v2_accepts_present_ip_and_rejects_a_changed_ip_or_uuid(self):
        key=Ed25519PrivateKey.generate()
        binding=license.ipv4_bindings(UUID,[adapter(['10.2.1.4','172.16.0.10'])])
        signed=sign(key,digest('172.16.0.10'))[1]
        old_board=Mock(side_effect=license.LicenseError('板号不可读'))
        self.assertEqual(license.verify(signed,key.public_key(),binding,legacy_board_id=old_board)['hwid_version'],2)
        for other in (license.ipv4_bindings(UUID,[adapter(['172.16.0.11'])]),
                      license.ipv4_bindings('2CB989C3-9213-4C12-9F30-2A798834290D',[adapter(['172.16.0.10'])])):
            with self.assertRaisesRegex(license.LicenseError,'不适用于此电脑'):
                license.verify(signed,key.public_key(),other,legacy_board_id=old_board)
        old_board.assert_not_called()

    def test_v79_license_and_wildcard_are_compatible(self):
        key=Ed25519PrivateKey.generate()
        new=license.ipv4_bindings(UUID,[adapter(['192.168.42.11'])])
        old_code=license.fingerprint(UUID,'KNOWN-BOARD')
        old=Mock(return_value=old_code)
        def legacy_license(machine):
            payload, _ = sign(key, machine, universal=machine=='*')
            payload.update(user_name='旧许可证', employee_id='08', hwid_version=1)
            signature = b64encode(key.sign(license.canonical(payload))).decode()
            return json.dumps(dict(payload=payload,signature=signature)).encode()
        old_signed=legacy_license(old_code)
        legacy_universal=legacy_license('*')
        self.assertEqual(license.verify(legacy_universal,key.public_key(),new,legacy_board_id=old)['machine_id'],'*')
        old.assert_not_called()
        self.assertEqual(license.verify(old_signed,key.public_key(),new,legacy_board_id=old)['hwid_version'],1)
        old.assert_called_once_with()
        self.assertEqual(license.verify(old_signed,key.public_key(),license.MachineBindings(2,()),legacy_board_id=old)['hwid_version'],1)
        data=json.loads(old_signed)
        data['payload']['user_name']='已篡改'
        old.reset_mock()
        with self.assertRaisesRegex(license.LicenseError,'数字签名'):
            license.verify(json.dumps(data).encode(),key.public_key(),new,legacy_board_id=old)
        old.assert_not_called()

    def test_history_reads_v79_rows_and_adds_current_fields(self):
        key=Ed25519PrivateKey.generate()
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            with closing(sqlite3.connect(folder/'license_history.db')) as db, db:
                db.execute('CREATE TABLE issuance (license_id TEXT PRIMARY KEY,user_name TEXT,employee_id TEXT,machine_id TEXT,issued TEXT,product_id TEXT,path TEXT,fingerprint TEXT,notes TEXT,content BLOB)')
                db.execute('INSERT INTO issuance VALUES (?,?,?,?,?,?,?,?,?,?)',('old','用户','1','test','2026','app','old','fingerprint','',b'old'))
            history=History(folder)
            self.assertEqual(history.search('test')[0][0],'old')
            machine=digest('192.168.42.11')
            path=history.issue(key,machine,folder/'signed')
            row,=history.search(machine)
            self.assertEqual(row[1],machine)
            payload=json.loads(path.read_bytes())['payload']
            self.assertEqual(set(payload),license.FIELDS)
            self.assertEqual(payload['hwid_version'],2)

    def test_activation_recovers_when_only_ipv4_is_available(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from die_shift_studio.license_ui import LicenseDialog, W
        app=W.QApplication.instance() or W.QApplication([])
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            binding=license.ipv4_bindings(UUID,[adapter(['192.168.42.11'])])
            key=Ed25519PrivateKey.generate()
            old=Mock(side_effect=license.LicenseError('无法读取主板序列号。'))
            state=license.Authorization(binding,key.public_key(),folder/'active.lic',legacy_board_id=old)
            chosen=folder/'selected.lic'
            chosen.write_bytes(sign(key,binding.primary)[1])
            with patch('die_shift_studio.license_ui.local_authorization',return_value=state):
                dialog=LicenseDialog()
                dialog.query.wait()
                app.processEvents()
            self.assertEqual(dialog.machine.text(),binding.primary)
            self.assertTrue(dialog.machine.isReadOnly())
            dialog.selected=chosen
            dialog.apply()
            dialog.query.wait()
            app.processEvents()
            self.assertEqual(state.payload['hwid_version'],2)
            old.assert_not_called()
            dialog.deleteLater()
            app.processEvents()
