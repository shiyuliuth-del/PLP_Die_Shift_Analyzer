"""Provision an ephemeral test public key and license on an isolated CI host."""
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from LicenseManager.manager import public_bytes, sign
from die_shift_studio.license_core import LicenseError, license_path, machine_id, store_license

root=Path.cwd()
key=Ed25519PrivateKey.generate()
with (root/'public_key.pem').open('xb') as stream:
    stream.write(public_bytes(key))
try:
    machine=machine_id()
except LicenseError as exc:
    (root/'qa-hardware-status.txt').write_text(str(exc),encoding='utf8')
else:
    path=license_path()
    if path.exists():
        raise RuntimeError('Refusing to overwrite an existing real license.')
    store_license(path,sign(key,'Github QA','QA',machine)[1])
    (root/'qa-hardware-valid').touch(exist_ok=False)
    (root/'qa-hardware-status.txt').write_text('Validated CIM hardware identifiers and prepared a machine-bound QA license.',encoding='utf8')
