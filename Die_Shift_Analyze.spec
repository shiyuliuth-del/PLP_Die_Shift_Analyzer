# PyInstaller: build on the target OS. Windows output is a double-clickable EXE.
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs,collect_data_files
root=Path(SPECPATH)
seed=[(str(p),'seed/examples') for p in sorted((root/'examples').glob('*')) if p.suffix in ('.json','.gds')]
seed += [(str(root/'Jobs'/(p.stem+'.dshift.json')),'seed/Jobs') for p in sorted((root/'examples').glob('*.json'))]
# scipy.stats' compiled Sobol initializer loads this NPZ at runtime. Missing it
# can emit an ignored Cython exception while returning a degenerate sequence.
seed += collect_data_files('scipy.stats',includes=['_sobol_direction_numbers.npz'])
# Optional GPU runtime is collected only in a build environment that has it.
import importlib.util
optional_data=[];optional_binaries=[];optional_hidden=[]
if importlib.util.find_spec('cupy'):
 from PyInstaller.utils.hooks import collect_all
 for package in ('cupy','cupyx'):
  data,binary,hidden=collect_all(package);optional_data+=data;optional_binaries+=binary;optional_hidden+=hidden
# CUDA drivers remain installed on the destination machine.
a=Analysis([str(root/'main.py')],pathex=[str(root)],binaries=collect_dynamic_libs('gdstk')+collect_dynamic_libs('klayout')+optional_binaries,datas=seed+optional_data,
 hiddenimports=['matplotlib.backends.backend_qtagg','scipy.special._ufuncs','shapely.lib','klayout.db','klayout.dbcore','klayout.tl','klayout.tlcore']+optional_hidden,
 hookspath=[],runtime_hooks=[],excludes=['PyQt6','PySide6','PySide2','tkinter'],
 hooksconfig={'matplotlib':{'backends':['QtAgg','Agg']}})
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='Die_Shift_Analyze',debug=False,
 bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False)
