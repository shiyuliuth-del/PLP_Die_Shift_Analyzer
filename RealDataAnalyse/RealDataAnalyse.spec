"""Build the independent client beside Die_Shift_Analyze.exe."""
import importlib.util
import sys
from pathlib import Path

if sys.platform != 'win32':
    raise SystemExit('Build the Windows executable on Windows.')
root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from die_shift_studio.app_icon import write_ico
from die_shift_studio.license_core import LicenseError, frozen_public_key_data

try:
    license_data = frozen_public_key_data(root, workpath)
except LicenseError as exc:
    raise SystemExit(str(exc)) from exc

icon = Path(workpath) / 'RealDataAnalyse.ico'
icon.parent.mkdir(parents=True, exist_ok=True)
write_ico(icon)
qt = 'PyQt6' if importlib.util.find_spec('PyQt6') else 'PyQt5'
a = Analysis([str(root / 'RealDataAnalyse' / 'main.py')], pathex=[str(root)],
             binaries=[], datas=[license_data], hiddenimports=['openpyxl'],
             excludes=['PyQt5' if qt == 'PyQt6' else 'PyQt6', 'PySide6', 'PySide2',
                       'cupy', 'cupyx', 'cupy_backends', 'nvidia', 'torch', 'tensorflow',
                       'matplotlib', 'scipy', 'pandas', 'klayout', 'IPython', 'pytest', 'LicenseManager'])
exe = EXE(PYZ(a.pure), a.scripts, a.binaries, a.datas, [], name='RealDataAnalyse',
          icon=str(icon), debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
