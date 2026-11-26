"""Offline signing and administrator history. Never imported by the user EXE."""
from base64 import b64encode
from contextlib import closing, contextmanager
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from die_shift_studio.license_core import BOARD_HWID, HWID_VERSIONS, LicenseError, MACHINE_ID, PRODUCT, canonical, read_license


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def fingerprint(key):
    return hashlib.sha256(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest().upper()


def new_file(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)


def generate(directory, password):
    if not password:
        raise LicenseError('请输入私钥保护密码。')
    directory.mkdir(parents=True, exist_ok=True)
    private, public = directory/'private_key.pem', directory/'public_key.pem'
    if private.exists() or public.exists():
        raise LicenseError('已有密钥文件，请加载或选择其他目录。')
    key = Ed25519PrivateKey.generate()
    new_file(private, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.BestAvailableEncryption(password.encode('utf8'))))
    new_file(public, public_bytes(key))
    return private, key


def load_private(path, password):
    if not password:
        raise LicenseError('请输入私钥保护密码。')
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password.encode('utf8'))
    except (ValueError, TypeError) as exc:
        raise LicenseError('私钥文件无效或密码错误。') from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError('需要 Ed25519 私钥。')
    return key


def sign(key, user, employee, machine, *, hwid_version=BOARD_HWID, universal=False):
    user, employee, machine = user.strip(), employee.strip(), machine.strip().upper()
    if not user or not employee or len(user)>256 or len(employee)>256:
        raise LicenseError('姓名和工号各需要 1–256 个字符。')
    if not universal and not MACHINE_ID.fullmatch(machine):
        raise LicenseError('Machine ID 必须为 64 位十六进制。')
    if type(hwid_version) is not int or hwid_version not in HWID_VERSIONS:
        raise LicenseError('Machine ID 版本无效。')
    payload = dict(schema_version=1, license_id=str(uuid.uuid4()),product_id=PRODUCT,user_name=user,
                   employee_id=employee,machine_id='*' if universal else machine,hwid_version=hwid_version)
    signature = b64encode(key.sign(canonical(payload))).decode('ascii')
    return payload, (json.dumps(dict(payload=payload,signature=signature),ensure_ascii=False,indent=2)+'\n').encode('utf8')


FIELDS = ('license_id','user_name','employee_id','machine_id','issued','product_id','path','fingerprint','notes','hwid_version')


class History:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'license_history.db'
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS issuance (license_id TEXT PRIMARY KEY, user_name TEXT, employee_id TEXT, machine_id TEXT, issued TEXT, product_id TEXT, path TEXT, fingerprint TEXT, notes TEXT, content BLOB, hwid_version INTEGER NOT NULL DEFAULT 1)')
            if 'hwid_version' not in {column[1] for column in conn.execute('PRAGMA table_info(issuance)')}:
                conn.execute('ALTER TABLE issuance ADD COLUMN hwid_version INTEGER NOT NULL DEFAULT 1')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            yield conn

    def issue(self, key, user, employee, machine, notes, folder, *, hwid_version=BOARD_HWID, universal=False):
        folder.mkdir(parents=True, exist_ok=True)
        if universal:
            existing = folder/'Universal.lic'
            if existing.exists():
                # Return the previously signed copy only if it corresponds to this key.
                from die_shift_studio.license_core import verify
                payload = verify(read_license(existing),key.public_key(),'0'*64)
                if payload['machine_id'] != '*':
                    raise LicenseError('已有同名文件，且不是通用授权。')
                return existing
        payload, content = sign(key,user,employee,machine,hwid_version=hwid_version,universal=universal)
        employee_tag = re.sub(r'[^\w-]+', '_', payload['employee_id'])[:40]
        filename = 'Universal.lic' if universal else f"DSA_{employee_tag}_{payload['machine_id'][:8]}_{payload['license_id']}.lic"
        path = folder/filename
        new_file(path,content)
        row = dict(payload, issued=datetime.now().astimezone().isoformat(timespec='seconds'),
                   path=str(path), fingerprint=fingerprint(key), notes=notes)
        try:
            with self.connect() as conn:
                conn.execute(f"INSERT INTO issuance ({','.join(FIELDS)},content) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (*[row[k] for k in FIELDS],content))
        except sqlite3.Error:
            path.unlink()
            raise
        return path

    def search(self, query=''):
        with self.connect() as conn:
            return conn.execute(
                f"SELECT {','.join(FIELDS)} FROM issuance "
                'WHERE instr(lower(user_name),lower(?))>0 OR instr(lower(employee_id),lower(?))>0 OR instr(lower(machine_id),lower(?))>0 OR ?=\'\' ORDER BY issued DESC',
                (query,)*4).fetchall()

    def export_original(self, number, path):
        with self.connect() as conn:
            row = conn.execute('SELECT content FROM issuance WHERE license_id=?',(number,)).fetchone()
        if row is None:
            raise LicenseError('未找到该授权记录。')
        new_file(path,row[0])

    def export_csv(self, path, query=''):
        with open(path,'x',encoding='utf-8-sig',newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(FIELDS)
            writer.writerows([prefix_csv(cell) for cell in row] for row in self.search(query))

    def backup(self, path):
        with open(path,'xb'):
            pass
        try:
            with self.connect() as source, closing(sqlite3.connect(path)) as destination:
                source.backup(destination)
        except sqlite3.Error:
            Path(path).unlink()
            raise


def prefix_csv(value):
    return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value
