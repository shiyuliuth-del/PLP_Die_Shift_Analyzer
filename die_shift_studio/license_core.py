"""Permanent offline license verification. Signing belongs to the admin tool."""
from base64 import b64decode
import binascii
from dataclasses import dataclass
import hashlib
import hmac
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
import uuid

from .jobs import installation_root

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = 'DIE_SHIFT_ANALYZE'
FIELDS = {'schema_version','license_id','product_id','machine_id','hwid_version'}
LEGACY_FIELDS = FIELDS | {'user_name','employee_id'}
LIMIT = 16384
MACHINE_ID = re.compile(r'[0-9A-F]{64}')
INVALID_BOARD = {'TO BE FILLED BY O.E.M.','TO BE FILLED BY OEM','DEFAULT STRING','SYSTEM SERIAL NUMBER',
                 'UNKNOWN','NONE','NOT SPECIFIED','NOT APPLICABLE','N/A','NA','NULL','INVALID','NO SERIAL NUMBER'}
BOARD_HWID = 1
IPV4_HWID = 2
HWID_VERSIONS = (BOARD_HWID, IPV4_HWID)


class LicenseError(ValueError):
    pass


def normalized_uuid(system_uuid):
    try:
        value = uuid.UUID(str(system_uuid).strip())
        if value.int in (0, 2**128-1):
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise LicenseError('电脑 UUID 无效，请联系管理员。') from exc
    return str(value).upper()


def fingerprint(system_uuid, board_serial):
    system_uuid = normalized_uuid(system_uuid)
    board = board_serial.strip().upper() if isinstance(board_serial,str) else ''
    if not board or board in INVALID_BOARD or re.fullmatch(r'(0+|F+)', board):
        raise LicenseError('主板序列号无效，请联系管理员。')
    try:
        raw=f'HWID_V1|UUID={system_uuid}|BOARD={board}'.encode('utf-8')
    except UnicodeError as exc:
        raise LicenseError('主板序列号的字符无效。') from exc
    return hashlib.sha256(raw).hexdigest().upper()


@dataclass(frozen=True)
class MachineBindings:
    version: int
    codes: tuple[str, ...]

    @property
    def primary(self):
        return self.codes[0] if self.codes else ''


def _windows_cim(query):
    program = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    command = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
               "$u=@((Get-CimInstance Win32_ComputerSystemProduct).UUID);" + query)
    try:
        result = subprocess.run([str(program),'-NoProfile','-NonInteractive','-Command',command],
                                capture_output=True, text=True, encoding='utf-8-sig', check=True,
                                timeout=10, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))
        value = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise LicenseError('读取电脑授权信息超时。') from exc
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise LicenseError('无法通过 Windows CIM 读取电脑授权信息。') from exc
    ids = value.get('uuid') if isinstance(value, dict) else None
    if not isinstance(ids, list) or len(ids) != 1 or not ids[0]:
        raise LicenseError('无法读取唯一的电脑 UUID。')
    return ids[0], value


def _ipv4(value):
    if not isinstance(value, str):
        return None
    try:
        address = IPv4Address(value.strip())
    except ValueError:
        return None
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or int(address)>>24==0:
        return None
    return address


def ipv4_bindings(system_uuid, adapters):
    system_uuid = normalized_uuid(system_uuid)
    if not isinstance(adapters, list):
        raise LicenseError('Windows 返回的本机 IPv4 信息无效。')
    priorities = []
    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        addresses, gateways = adapter.get('addresses'), adapter.get('gateways')
        if not isinstance(addresses, list) or not isinstance(gateways, list):
            continue
        metric, index = adapter.get('metric'), adapter.get('index')
        priority = (not any(_ipv4(gateway) for gateway in gateways),
                    metric if type(metric) is int and metric >= 0 else 2**32,
                    index if type(index) is int and index >= 0 else 2**32)
        for address in addresses:
            ip = _ipv4(address)
            if ip is not None:
                priorities.append((*priority, ip))
    addresses = dict.fromkeys(row[-1] for row in sorted(priorities))
    if not addresses:
        raise LicenseError('未找到有效的本机 IPv4 地址。请连接局域网或为网卡分配 IPv4 地址。')
    codes = tuple(hashlib.sha256(f'HWID_V2|UUID={system_uuid}|IPV4={ip}'.encode('ascii')).hexdigest().upper()
                  for ip in addresses)
    return MachineBindings(IPV4_HWID, codes)


def windows_board_id():
    system_uuid, value = _windows_cim("$b=@((Get-CimInstance Win32_BaseBoard).SerialNumber);"
                                      "@{uuid=$u;board=$b}|ConvertTo-Json -Compress")
    boards = value.get('board')
    if not isinstance(boards, list) or len(boards) != 1 or not boards[0]:
        raise LicenseError('无法读取旧版许可证所需的主板序列号。')
    return fingerprint(system_uuid, boards[0])


def machine_bindings():
    if sys.platform == 'win32':
        # Read only local adapters. No DNS, remote IP lookup, or mainboard query.
        system_uuid, value = _windows_cim(
            '$n=@(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled = TRUE" | ForEach-Object {'
            '@{addresses=@($_.IPAddress);gateways=@($_.DefaultIPGateway);metric=$_.IPConnectionMetric;index=$_.InterfaceIndex}});'
            '@{uuid=$u;adapters=$n}|ConvertTo-Json -Compress -Depth 5')
        return ipv4_bindings(system_uuid, value.get('adapters'))

    def dmi(name, description):
        try:
            return (Path('/sys/class/dmi/id')/name).read_text(encoding='utf8')
        except (OSError, UnicodeError) as exc:
            raise LicenseError(f'无法读取{description}。') from exc

    return MachineBindings(BOARD_HWID,(fingerprint(dmi('product_uuid','电脑 UUID'), dmi('board_serial','主板序列号')),))


def machine_id():
    return machine_bindings().primary


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
        path = build_public_key_path(installation_root())
    try:
        return load_public_key(path.read_bytes())
    except OSError as exc:
        raise LicenseError('软件缺少正式授权公钥，请联系管理员。') from exc


def admin_key_directory():
    return local_directory('DieShiftLicenseManager')/'keys'


def build_public_key_path(root):
    override = os.environ.get('DIE_SHIFT_LICENSE_PUBLIC_KEY')
    if override:
        return Path(override).expanduser()
    bundled = Path(root)/'public_key.pem'
    return bundled if bundled.exists() else admin_key_directory()/'public_key.pem'


def build_public_key(root):
    path = build_public_key_path(root)
    try:
        return load_public_key(path.read_bytes())
    except FileNotFoundError as exc:
        raise LicenseError(
            'Formal Ed25519 public key required. Run LicenseManager_BUILD.bat, open LicenseManager '
            'and generate a key pair, or provide public_key.pem next to BUILD.bat.'
        ) from exc
    except OSError as exc:
        raise LicenseError(f'无法读取构建公钥：{path}') from exc


def frozen_public_key_data(root, workpath):
    key = build_public_key(root)
    target = Path(workpath)/'license_public.pem'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return str(target), 'die_shift_studio'


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


def verify(content, key, bindings, *, legacy_board_id=None, binding_error=''):
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
    if not isinstance(payload, dict) or set(payload) not in (FIELDS, LEGACY_FIELDS) or not isinstance(signature, str):
        raise LicenseError('许可证字段不正确。')
    if any(type(payload[k]) is not int for k in ('schema_version','hwid_version')) or any(
            not isinstance(payload[k], str) or not payload[k].strip() or len(payload[k]) > 256
            for k in payload.keys()-{'schema_version','hwid_version'}):
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
    version = payload['hwid_version']
    if payload['schema_version'] != 1 or version not in HWID_VERSIONS:
        raise LicenseError('许可证协议版本不受支持。')
    if payload['product_id'] != PRODUCT:
        raise LicenseError('许可证不适用于此软件。')
    try:
        uuid.UUID(payload['license_id'])
    except ValueError as exc:
        raise LicenseError('许可证授权编号无效。') from exc
    bound = payload['machine_id']
    if bound != '*' and not MACHINE_ID.fullmatch(bound):
        raise LicenseError('许可证机器码无效。')
    if isinstance(bindings, str):
        bindings = MachineBindings(BOARD_HWID, (bindings,))
    codes = tuple(code for code in bindings.codes if MACHINE_ID.fullmatch(code))
    if bound == '*' and codes:
        return payload
    if version != bindings.version or not codes:
        codes = (legacy_board_id(),) if version == BOARD_HWID and legacy_board_id is not None else ()
    if not codes:
        raise LicenseError(binding_error or '本机无法生成此版本许可证所需的 Machine ID。')
    if bound != '*' and not any(hmac.compare_digest(bound, code) for code in codes):
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
    def __init__(self, bindings, key, path, *, root=None, legacy_board_id=None, binding_error='', key_error=''):
        self.bindings = MachineBindings(BOARD_HWID, (bindings,)) if isinstance(bindings,str) else bindings
        self.legacy_board_id = legacy_board_id
        self.key = key
        self.path = path
        self.root = root
        self.binding_error = binding_error
        self.key_error = key_error
        self.payload = None

    @property
    def machine_id(self):
        return self.bindings.primary

    def _require_key(self):
        if self.key is None:
            raise LicenseError(self.key_error or '软件缺少授权公钥。')

    def _verify(self, content):
        return verify(content, self.key, self.bindings,
                      legacy_board_id=self.legacy_board_id, binding_error=self.binding_error)

    def check(self):
        self.payload = None
        self._require_key()

        candidates = []
        if self.root is not None:
            try:
                candidates = sorted((p for p in self.root.iterdir() if p.suffix.lower() == '.lic' and p.is_file()),
                                    key=lambda p:(p.name.lower() != 'license.lic', p.name.casefold(), p.name))
            except OSError as exc:
                raise LicenseError(f'无法读取软件目录：{self.root}') from exc
        if self.path.is_file() and self.path not in candidates:
            candidates.append(self.path)

        first_error = None
        for path in candidates:
            try:
                self.payload = self._verify(read_license(path))
            except LicenseError as exc:
                if first_error is None:
                    first_error = f'{path.name}：{exc}'
            else:
                return self.payload
        raise LicenseError(first_error or '尚未找到许可证。请将管理员签发的 .lic 放到软件根目录。')

    def activate(self, path):
        self._require_key()
        content = read_license(path)
        payload = self._verify(content)
        store_license(self.path, content)
        self.payload = payload
        return payload


def local_authorization():
    key = None
    key_error = ''
    windows = sys.platform == 'win32'
    legacy = windows_board_id if windows else None
    error = ''
    try:
        bindings = machine_bindings()
    except LicenseError as exc:
        bindings = MachineBindings(IPV4_HWID if windows else BOARD_HWID, ())
        error = str(exc)
    try:
        key = embedded_public_key()
    except LicenseError as exc:
        key_error = str(exc)
    return Authorization(bindings,key,license_path(),root=installation_root(),legacy_board_id=legacy,
                         binding_error=error,key_error=key_error)


def installed_authorization():
    state=local_authorization()
    state.check()
    return state


if __name__=='__main__':
    root = Path(__file__).resolve().parent.parent
    try:build_public_key(root)
    except LicenseError as exc:raise SystemExit(str(exc)) from exc
    print(f'Build public key: {build_public_key_path(root)}')
