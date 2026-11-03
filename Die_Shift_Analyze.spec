# Build on Windows with the active Python environment. No sidecar files.
import importlib.util
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs,collect_data_files

if sys.platform!='win32':
    raise SystemExit('The Windows EXE must be built on Windows.')
root=Path(SPECPATH)
sys.path.insert(0,str(root))
from die_shift_studio.app_icon import write_ico
icon=Path(workpath)/'Die_Shift_Analyze.ico'
icon.parent.mkdir(parents=True,exist_ok=True);write_ico(icon)

data=collect_data_files('scipy.stats',includes=['_sobol_direction_numbers.npz'])
data.extend((str(root/'die_shift_studio'/name),'die_shift_studio') for name in ('native_ops.py','klayout_worker.py'))
binaries=collect_dynamic_libs('gdstk')
hidden=['matplotlib.backends.backend_qtagg','scipy.special._ufuncs','shapely.lib']
if importlib.util.find_spec('klayout'):
    binaries+=collect_dynamic_libs('klayout')
    hidden+=['klayout.db','klayout.dbcore','klayout.tl','klayout.tlcore']
if importlib.util.find_spec('cupy'):
    from PyInstaller.utils.hooks import collect_all
    for package in ('cupy','cupyx'):
        extra,libs,imports=collect_all(package)
        data+=extra;binaries+=libs;hidden+=imports

qt='PyQt6' if importlib.util.find_spec('PyQt6') else 'PyQt5'
exclude=['PyQt5' if qt=='PyQt6' else 'PyQt6','PySide6','PySide2','tkinter']
a=Analysis([str(root/'main.py')],pathex=[str(root)],binaries=binaries,datas=data,
           hiddenimports=hidden,excludes=exclude,
           hooksconfig={'matplotlib':{'backends':['QtAgg','Agg']}})
exe=EXE(PYZ(a.pure),a.scripts,a.binaries,a.datas,[],name='Die_Shift_Analyze',icon=str(icon),
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
