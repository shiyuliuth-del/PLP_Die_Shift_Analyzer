"""Offline signing and administrator history. Never imported by the user EXE."""
from base64 import b64encode
import calendar
from contextlib import closing, contextmanager
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from die_shift_studio.license_core import IPV4_HWID, LicenseError, MACHINE_ID, PRODUCT, canonical, read_license, verify


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


def add_months(dt, months):
    total = dt.year * 12 + (dt.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def sign(key, machine, *, universal=False, duration_months=0, now=None):
    machine = machine.strip().upper()
    if not universal and not MACHINE_ID.fullmatch(machine):
        raise LicenseError('Machine ID 必须为 64 位十六进制。')
    if type(duration_months) is not int or not (0 <= duration_months <= 12000):
        raise LicenseError('授权时长必须为 0～12000 的整数月。')
    payload = dict(schema_version=1, license_id=str(uuid.uuid4()), product_id=PRODUCT,
                   machine_id='*' if universal else machine, hwid_version=IPV4_HWID)
    if duration_months > 0:
        issued_dt = (now if now is not None else datetime.now().astimezone())
        if issued_dt.tzinfo is None:
            issued_dt = issued_dt.astimezone()
        expires_dt = add_months(issued_dt, duration_months)
        payload.update(duration_months=duration_months,
                       issued_at=issued_dt.isoformat(timespec='seconds'),
                       expires_at=expires_dt.isoformat(timespec='seconds'))
    signature = b64encode(key.sign(canonical(payload))).decode('ascii')
    return payload, (json.dumps(dict(payload=payload,signature=signature),ensure_ascii=False,indent=2)+'\n').encode('utf8')


FIELDS = ('license_id','machine_id','issued','path','fingerprint')


class History:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'license_history.db'
        with self.connect() as conn:
            conn.execute(
                'CREATE TABLE IF NOT EXISTS issuance (license_id TEXT PRIMARY KEY, machine_id TEXT, '
                'issued TEXT, path TEXT, fingerprint TEXT, duration_months INTEGER DEFAULT 0, expires_at TEXT DEFAULT \'\', content BLOB)'
            )
            cols = {row[1] for row in conn.execute('PRAGMA table_info(issuance)').fetchall()}
            if 'duration_months' not in cols:
                conn.execute('ALTER TABLE issuance ADD COLUMN duration_months INTEGER DEFAULT 0')
            if 'expires_at' not in cols:
                conn.execute("ALTER TABLE issuance ADD COLUMN expires_at TEXT DEFAULT ''")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            yield conn

    def issue(self, key, machine, folder, *, universal=False, duration_months=0):
        folder.mkdir(parents=True, exist_ok=True)
        if universal:
            existing = folder/'Universal.lic'
            if existing.exists():
                # Return the previously signed copy only if it corresponds to this key.
                payload = verify(read_license(existing),key.public_key(),'0'*64)
                if payload['machine_id'] != '*':
                    raise LicenseError('已有同名文件，且不是通用授权。')
                return existing
        payload, content = sign(key,machine,universal=universal,duration_months=duration_months)
        filename = 'Universal.lic' if universal else f"DSA_{payload['machine_id'][:8]}_{payload['license_id']}.lic"
        path = folder/filename
        new_file(path,content)
        row = dict(payload, issued=payload.get('issued_at') or datetime.now().astimezone().isoformat(timespec='seconds'),
                   path=str(path), fingerprint=fingerprint(key),
                   duration_months=int(payload.get('duration_months', 0)),
                   expires_at=str(payload.get('expires_at', '')))
        try:
            with self.connect() as conn:
                conn.execute(f"INSERT INTO issuance ({','.join(FIELDS)},duration_months,expires_at,content) VALUES (?,?,?,?,?,?,?,?)",
                             (*[row[k] for k in FIELDS],row['duration_months'],row['expires_at'],content))
        except sqlite3.Error:
            path.unlink()
            raise
        return path

    def search(self, query=''):
        with self.connect() as conn:
            return conn.execute(
                f"SELECT {','.join(FIELDS)} FROM issuance "
                'WHERE instr(lower(license_id),lower(?))>0 OR instr(lower(machine_id),lower(?))>0 ORDER BY issued DESC',
                (query,query)).fetchall()

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
