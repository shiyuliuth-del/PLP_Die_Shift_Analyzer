"""Source and PyInstaller windowed entry point."""
import argparse
import multiprocessing
import sys

if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        from die_shift_studio.frozen_support import prepare_runtime,hide_owned_console,smoke
        if '--smoke-test' not in sys.argv:hide_owned_console()
        prepare_runtime()
        from die_shift_studio.dependencies import verify
        verify()
        parser=argparse.ArgumentParser(description='Die Shift Analyze')
        parser.add_argument('--project');parser.add_argument('--smoke-test',help=argparse.SUPPRESS)
        args=parser.parse_args()
        if args.smoke_test:raise SystemExit(smoke(args.smoke_test))
        from die_shift_studio.gui import launch
        raise SystemExit(launch(args.project))
    except Exception:
        import traceback
        from pathlib import Path
        from die_shift_studio.jobs import installation_root
        error=traceback.format_exc()
        try:(installation_root()/'startup-error.log').write_text(error,encoding='utf-8')
        except OSError:pass
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动未完成，请查看程序目录 startup-error.log。\n'+str(error[-1800:]),'Die Shift Analyze',16)
        else:print(error,file=sys.stderr)
        raise SystemExit(1)
