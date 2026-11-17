"""Standalone source / frozen entry point and optional unattended batch mode."""
import argparse
import json
import multiprocessing
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from RealDataAnalyse.transport import installation
    from RealDataAnalyse.storage import configure_logging
    configure_logging(installation())
    parser = argparse.ArgumentParser(description='RealDataAnalyse')
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--run', help='恢复运行配置JSON（database/endpoint/project/output/workers等）')
    operation.add_argument('--import-data', help='Excel导入配置JSON（paths/database/die_count/workers/profile）')
    parser.add_argument('--result', help='无界面运行结果JSON')
    args = parser.parse_args()
    if args.run or args.import_data:
        try:
            config = json.loads(Path(args.run or args.import_data).read_text(encoding='utf8'))
            if args.import_data:
                log_root = Path(config['database']).resolve().parent
                if log_root.name == 'Tasks':
                    log_root = log_root.parent
                logger = configure_logging(log_root)
                import threading
                from RealDataAnalyse.excel import import_files
                result = import_files(**config, stop=threading.Event(), progress=lambda update: logger.info('%s', update))
            else:
                configure_logging(Path(config['project']).resolve().parent)
                from RealDataAnalyse.runner import Runner
                result = Runner(**config).run()
            code = 0
        except Exception as exc:
            import logging
            logging.getLogger('RealDataAnalyse').exception('自动化任务失败')
            result, code = dict(state='error', message=str(exc)), 1
        if args.result:
            Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        return code
    from RealDataAnalyse.gui import launch
    return launch()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from RealDataAnalyse.transport import installation
        error = traceback.format_exc()
        (installation() / 'RealDataAnalyse-startup-error.log').write_text(error, encoding='utf8')
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, error[-1800:], 'RealDataAnalyse 启动失败', 16)
        else:
            print(error, file=sys.stderr)
        raise SystemExit(1)
