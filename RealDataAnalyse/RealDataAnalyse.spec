"""Build the independent client beside Die_Shift_Analyze.exe."""
import importlib.util
import sys
from pathlib import Path

if sys.platform != 'win32':
    raise SystemExit('Build the Windows executable on Windows.')
root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from die_shift_studio.app_icon import write_ico
icon = Path(workpath) / 'RealDataAnalyse.ico'
icon.parent.mkdir(parents=True, exist_ok=True)
write_ico(icon)
qt = 'PyQt6' if importlib.util.find_spec('PyQt6') else 'PyQt5'
a = Analysis([str(root / 'RealDataAnalyse' / 'main.py')], pathex=[str(root)],
             binaries=[], datas=[], hiddenimports=['openpyxl'],
             excludes=['PyQt5' if qt == 'PyQt6' else 'PyQt6', 'PySide6', 'PySide2',
                       'cupy', 'cupyx', 'cupy_backends', 'nvidia', 'torch', 'tensorflow',
                       'matplotlib', 'scipy', 'pandas', 'klayout', 'IPython', 'pytest'])
exe = EXE(PYZ(a.pure), a.scripts, a.binaries, a.datas, [], name='RealDataAnalyse',
          icon=str(icon), debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
