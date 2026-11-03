"""Offline preflight; desktop KLayout can supply the native DRC engine."""
import importlib
import sys
MODULES=('numpy','scipy','matplotlib','shapely','gdstk','die_shift_studio.qt_compat','psutil','PIL')

def verify():
    failures=[]
    for name in MODULES:
        try:importlib.import_module(name)
        except (ImportError,OSError) as exc:failures.append(f'{name}: {exc}')
    if failures:
        raise RuntimeError('当前 Python 环境缺少应用依赖。不会联网或自动安装；请使用已有完整环境，或已打包的 EXE。\n'
            f'当前解释器：{sys.executable}\n'+'\n'.join(failures))
    from .native_backend import backend
    return backend().info

if __name__=='__main__':print(verify())
