import importlib.util
import sys
from pathlib import Path

if sys.platform!='win32':
    raise SystemExit('LicenseManager.exe must be built on Windows.')
folder=Path(SPECPATH)
root=folder.parent
sys.path.insert(0,str(root))
from die_shift_studio.app_icon import write_ico
icon=Path(workpath)/'LicenseManager.ico';icon.parent.mkdir(parents=True,exist_ok=True);write_ico(icon)
other_qt='PyQt5' if importlib.util.find_spec('PyQt6') else 'PyQt6'
a=Analysis([str(folder/'main.py')],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],
           excludes=[other_qt,'PySide2','PySide6','numpy','scipy','matplotlib','gdstk','shapely','cupy','klayout'])
exe=EXE(PYZ(a.pure),a.scripts,a.binaries,a.datas,[],name='LicenseManager',icon=str(icon),
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
