"""Check native DRC before opening the UI, not halfway through a worker."""
import importlib
import sys

MODULES=('numpy','scipy','matplotlib','shapely','gdstk','PyQt5.QtCore','psutil','PIL','klayout.db')

def verify():
    failures=[]
    for name in MODULES:
        try:importlib.import_module(name)
        except (ImportError,OSError) as exc:failures.append(f'{name}: {exc}')
    if failures:
        raise RuntimeError('运行依赖不完整。请通过 RUN.bat 启动（自动建立并修复本地环境）。\n'
            f'当前解释器：{sys.executable}\n'+'\n'.join(failures)+'\n'
            f'手动修复："{sys.executable}" -m pip install -r requirements.txt')
    import klayout.db as kdb
    region=kdb.Region(kdb.Box(0,0,100,10))
    assert not region.width_check(20).is_empty(),'KLayout native DRC self-check failed'

if __name__=='__main__':verify()
