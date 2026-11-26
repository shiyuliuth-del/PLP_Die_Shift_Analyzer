"""Standalone source / frozen entry point and optional unattended batch mode."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
else:
    for _stream in ('stdout', 'stderr'):
        if getattr(sys, _stream, None) is None:
            setattr(sys, _stream, open(os.devnull, 'w', encoding='utf-8'))


def main():
    from die_shift_studio.jobs import measured_workspace
    from RealDataAnalyse.transport import installation
    from RealDataAnalyse.storage import configure_logging
    logger = configure_logging(installation())
    parser = argparse.ArgumentParser(description='RealDataAnalyse')
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--run', help='恢复运行配置JSON（database/endpoint/project/output/workers等）')
    operation.add_argument('--import-data', help='Excel导入配置JSON（paths/database/die_count/workers/profile）')
    operation.add_argument('--run-plans', help='按顺序执行计划任务数据库')
    parser.add_argument('--result', help='无界面运行结果JSON')
    args = parser.parse_args()
    frozen = bool(getattr(sys, 'frozen', False))
    if args.run or args.import_data or args.run_plans:
        from die_shift_studio.license_core import LicenseError, installed_authorization
        try:
            if frozen:
                installed_authorization()
            config = json.loads(Path(args.run or args.import_data).read_text(encoding='utf8')) if not args.run_plans else None
            if args.run_plans:
                from RealDataAnalyse.plans import Plans, PlanRunner
                from RealDataAnalyse.conversation import Conversation
                trace = Conversation(display=False)
                try:
                    result = PlanRunner(Plans(args.run_plans), trace=trace).run()
                finally: trace.close()
            elif args.import_data:
                database = Path(config['database']).resolve()
                log_root = next((p.parent for p in database.parents if p.name == 'Tasks'), database.parent)
                logger = configure_logging(log_root)
                import threading
                from RealDataAnalyse.excel import import_files
                result = import_files(**config, stop=threading.Event(), progress=lambda update: logger.info('%s', update))
            else:
                configure_logging(measured_workspace(config['project']))
                from RealDataAnalyse.runner import Runner
                result = Runner(**config).run()
            code = 0 if result.get('state', 'completed') == 'completed' else 1
        except LicenseError as exc:
            logger.warning('授权验证未通过：%s',exc)
            result, code = dict(state='error', message=str(exc)), 1
        except Exception as exc:
            import logging
            logging.getLogger('RealDataAnalyse').exception('自动化任务失败')
            result, code = dict(state='error', message=str(exc)), 1
        if args.result:
            Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        return code
    if frozen:
        from die_shift_studio.license_ui import require_activation
        app, authorization = require_activation(application='RealDataAnalyse')
        if authorization is None:
            return 1
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
