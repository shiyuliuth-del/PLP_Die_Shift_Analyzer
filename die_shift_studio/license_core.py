"""Permanent offline license verification. Signing belongs to the admin tool."""
from base64 import b64decode
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = 'DIE_SHIFT_ANALYZE'
FIELDS = {'schema_version','license_id','product_id','user_name','employee_id','machine_id','hwid_version'}
LIMIT = 16384
MACHINE_ID = re.compile(r'[0-9A-F]{64}')
INVALID_BOARD = {'TO BE FILLED BY O.E.M.','TO BE FILLED BY OEM','DEFAULT STRING','SYSTEM SERIAL NUMBER',
                 'UNKNOWN','NONE','NOT SPECIFIED','NOT APPLICABLE','N/A','NA','NULL','INVALID','NO SERIAL NUMBER'}


class LicenseError(ValueError):
    pass


def fingerprint(system_uuid, board_serial):
    try:
        value = uuid.UUID(str(system_uuid).strip())
        if value.int in (0, 2**128-1):
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise LicenseError('电脑 UUID 无效，请联系管理员。') from exc
    board = board_serial.strip().upper() if isinstance(board_serial,str) else ''
    if not board or board in INVALID_BOARD or re.fullmatch(r'(0+|F+)', board):
        raise LicenseError('主板序列号无效，请联系管理员。')
    try:
        raw=f'HWID_V1|UUID={str(value).upper()}|BOARD={board}'.encode('utf-8')
    except UnicodeError as exc:
        raise LicenseError('主板序列号的字符无效。') from exc
    return hashlib.sha256(raw).hexdigest().upper()


def machine_id():
    if sys.platform == 'win32':
        program = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
        command = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                   "$u=@((Get-CimInstance Win32_ComputerSystemProduct).UUID);"
                   "$b=@((Get-CimInstance Win32_BaseBoard).SerialNumber);"
                   "@{uuid=$u;board=$b}|ConvertTo-Json -Compress")
        try:
            result = subprocess.run([str(program),'-NoProfile','-NonInteractive','-Command',command],
                                    capture_output=True, text=True, encoding='utf-8-sig', check=True,
                                    timeout=10, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))
            value = json.loads(result.stdout)
            system_ids, boards = value['uuid'], value['board']
        except subprocess.TimeoutExpired as exc:
            raise LicenseError('读取电脑硬件信息超时。') from exc
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
            raise LicenseError('无法通过 Windows CIM 读取 UUID 或主板序列号。') from exc
        if not isinstance(system_ids, list) or len(system_ids) != 1 or not system_ids[0]:
            raise LicenseError('无法读取唯一的电脑 UUID。')
        if not isinstance(boards, list) or len(boards) != 1 or not boards[0]:
            raise LicenseError('无法读取唯一的主板序列号。')
        return fingerprint(system_ids[0], boards[0])

    def dmi(name, description):
        try:
            return (Path('/sys/class/dmi/id')/name).read_text(encoding='utf8')
        except (OSError, UnicodeError) as exc:
            raise LicenseError(f'无法读取{description}。') from exc

    return fingerprint(dmi('product_uuid','电脑 UUID'), dmi('board_serial','主板序列号'))


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')


def load_public_key(data):
    try:
        key = serialization.load_pem_public_key(data)
    except (ValueError, TypeError) as exc:
        raise LicenseError('公钥文件无效。') from exc
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError('正式公钥必须为 Ed25519。')
    return key


def embedded_public_key():
    if getattr(sys,'frozen',False):
        path = Path(sys._MEIPASS)/'die_shift_studio'/'license_public.pem'
    else:
        path = Path(__file__).resolve().parent.parent/'public_key.pem'
    try:
        return load_public_key(path.read_bytes())
    except OSError as exc:
        raise LicenseError('软件缺少正式授权公钥，请联系管理员。') from exc


def local_directory(name):
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')
    return base/name


def license_path():
    return local_directory('DieShiftAnalyze')/'License'/'license.lic'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LicenseError('许可证包含重复字段。')
        result[key] = value
    return result


def verify(content, key, current_machine):
    if len(content) > LIMIT:
        raise LicenseError('许可证文件过大。')
    try:
        value = json.loads(content.decode('utf8'), object_pairs_hook=_unique)
    except LicenseError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LicenseError('许可证文件损坏或不是 UTF-8 JSON。') from exc
    if not isinstance(value, dict) or set(value) != {'payload','signature'}:
        raise LicenseError('许可证格式错误。')
    payload, signature = value['payload'], value['signature']
    if not isinstance(payload, dict) or set(payload) != FIELDS or not isinstance(signature, str):
        raise LicenseError('许可证字段不正确。')
    if any(type(payload[k]) is not int for k in ('schema_version','hwid_version')) or any(
            not isinstance(payload[k], str) or not payload[k].strip() or len(payload[k]) > 256
            for k in FIELDS-{'schema_version','hwid_version'}):
        raise LicenseError('许可证字段类型不正确。')
    try:
        decoded = b64decode(signature, validate=True)
        if len(decoded) != 64:
            raise ValueError
    except (ValueError, binascii.Error) as exc:
        raise LicenseError('许可证签名格式错误。') from exc
    try:
        key.verify(decoded, canonical(payload))
    except (InvalidSignature, UnicodeError) as exc:
        raise LicenseError('许可证数字签名无效。') from exc
    if payload['schema_version'] != 1 or payload['hwid_version'] != 1:
        raise LicenseError('许可证协议版本不受支持。')
    if payload['product_id'] != PRODUCT:
        raise LicenseError('许可证不适用于此软件。')
    try:
        uuid.UUID(payload['license_id'])
    except ValueError as exc:
        raise LicenseError('许可证授权编号无效。') from exc
    bound = payload['machine_id']
    if not MACHINE_ID.fullmatch(current_machine):
        raise LicenseError('本机硬件信息无效。')
    if bound != '*' and (not MACHINE_ID.fullmatch(bound) or not hmac.compare_digest(bound,current_machine)):
        raise LicenseError('许可证不适用于此电脑。')
    return payload


def read_license(path):
    try:
        with Path(path).open('rb') as stream:
            return stream.read(LIMIT+1)
    except FileNotFoundError as exc:
        raise LicenseError('尚未导入许可证。') from exc
    except OSError as exc:
        raise LicenseError('无法读取许可证。') from exc


def store_license(path, content):
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise LicenseError('无法保存许可证。') from exc
    finally:
        if temp is not None:
            try:temp.unlink(missing_ok=True)
            except OSError:pass


class Authorization:
    def __init__(self, current_machine, key, path):
        self.machine_id = current_machine
        self.key = key
        self.path = path
        self.payload = None

    def check(self):
        self.payload = verify(read_license(self.path), self.key, self.machine_id)
        return self.payload

    def activate(self, path):
        content = read_license(path)
        payload = verify(content, self.key, self.machine_id)
        store_license(self.path, content)
        self.payload = payload
        return payload


def installed_authorization():
    state=Authorization(machine_id(),embedded_public_key(),license_path())
    state.check()
    return state
