"""Compatibility entry point for the consolidated production GD55 acceptance."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_name('gui_realdata_errors.py')),run_name='__main__')
