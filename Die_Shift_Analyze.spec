# Build on Windows with the active Python environment. No sidecar files.
import importlib.util
import json
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
data.extend((str(root/'die_shift_studio'/name),'die_shift_studio') for name in ('native_ops.py','klayout_worker.py','cuda_kernels.ptx'))
binaries=collect_dynamic_libs('gdstk')
hidden=['matplotlib.backends.backend_qtagg','scipy.special._ufuncs','shapely.lib']
if importlib.util.find_spec('klayout'):
    binaries+=collect_dynamic_libs('klayout')
    hidden+=['klayout.db','klayout.dbcore','klayout.tl','klayout.tlcore']

qt='PyQt6' if importlib.util.find_spec('PyQt6') else 'PyQt5'
exclude=['PyQt5' if qt=='PyQt6' else 'PyQt6','PySide6','PySide2','tkinter',
         'cupy','cupyx','cupy_backends','nvidia','torch','torchvision','torchaudio','tensorflow',
         'jax','jaxlib','numba','pandas','sklearn','IPython','pytest','notebook','jupyterlab']
a=Analysis([str(root/'main.py')],pathex=[str(root)],binaries=binaries,datas=data,
           hiddenimports=hidden,excludes=exclude,
           hooksconfig={'matplotlib':{'backends':['QtAgg','Agg']}})
# Report actual analysis inputs, including unexpected libraries from custom build environments.
inputs=[dict(name=name,bytes=Path(source).stat().st_size) for name,source,_ in (*a.binaries,*a.datas) if Path(source).is_file()]
forbidden=('cublas','cufft','cudnn','cutensor','cusolver','cusparse','curand','cudart','nvrtc','nvcuda')
unexpected=[v['name'] for v in inputs if Path(v['name']).name.lower().startswith(forbidden) and v['name'].lower().endswith('.dll')]
report_path=Path(workpath)/'size-report.json'
report=dict(input_count=len(inputs),input_bytes=sum(v['bytes'] for v in inputs),
            largest_inputs=sorted(inputs,key=lambda v:v['bytes'],reverse=True)[:30],unexpected_cuda_dlls=unexpected)
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
if unexpected:raise SystemExit(f'Unexpected CUDA SDK/driver DLLs: {unexpected}. Build inputs: {report_path}')
exe=EXE(PYZ(a.pure),a.scripts,a.binaries,a.datas,[],name='Die_Shift_Analyze',icon=str(icon),
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
report['exe_bytes']=(Path(DISTPATH)/'Die_Shift_Analyze.exe').stat().st_size
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(f"EXE size: {report['exe_bytes'] / 2**20:.1f} MiB; build inventory: {report_path}")
