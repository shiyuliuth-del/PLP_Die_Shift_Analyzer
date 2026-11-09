"""Source and PyInstaller windowed entry point."""
import argparse
import multiprocessing
import sys

if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        from die_shift_studio.frozen_support import prepare_runtime,hide_owned_console,smoke
        if not {'--smoke-test','--gpu-info'}.intersection(sys.argv):hide_owned_console()
        prepare_runtime()
        parser=argparse.ArgumentParser(description='Die Shift Analyze')
        parser.add_argument('--project');parser.add_argument('--smoke-test',help=argparse.SUPPRESS)
        parser.add_argument('--gpu-info',metavar='FILE',help='write offline GPU self-test results to a JSON file')
        args=parser.parse_args()
        if args.gpu_info:
            import json
            from pathlib import Path
            from die_shift_studio.acceleration import capabilities,graphics_inventory
            report=dict(capabilities(),adapters=graphics_inventory())
            Path(args.gpu_info).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            raise SystemExit(0)
        from die_shift_studio.dependencies import verify
        verify()
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
        if sys.platform=='win32' and not {'--smoke-test','--gpu-info'}.intersection(sys.argv):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动未完成，请查看程序目录 startup-error.log。\n'+str(error[-1800:]),'Die Shift Analyze',16)
        else:print(error,file=sys.stderr)
        raise SystemExit(1)
