"""CLI and Python client for the opt-in local measured-output API (stdlib)."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from .local_ipc import call


def wait(endpoint,task_id,timeout=3600,poll=.1):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        response=call(endpoint,'task.get',{'task_id':task_id})
        if response['status']=='error':return response
        task=response['result']
        if task['status'] in ('completed','error','cancelled'):return task
        time.sleep(poll)
    raise TimeoutError('Task wait timeout; task has not been cancelled: '+task_id)


def start(app,project,endpoint,timeout=120):
    app=Path(app).resolve();project=Path(project).resolve();endpoint=Path(endpoint).resolve()
    if not app.is_file() or not project.is_file():raise FileNotFoundError('App and project must exist')
    try:
        existing=call(endpoint,'ping',timeout=2)
        if existing.get('status')=='completed':return dict(status='completed',result={'already_running':True,'endpoint':str(endpoint)})
    except (OSError,ValueError,EOFError,TimeoutError):pass
    command=([sys.executable,str(app)] if app.suffix.lower() in ('.py','.pyw') else [str(app)])
    command+=['--project',str(project),'--measured-output','--ipc-endpoint',str(endpoint)]
    process=subprocess.Popen(command,cwd=app.parent,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if process.poll() is not None:raise RuntimeError(f'Application exited with {process.returncode}; see logs/runtime.log or startup-error.log')
        try:
            reply=call(endpoint,'ping',timeout=2)
            if reply.get('status')=='completed':return dict(status='completed',result={'pid':process.pid,'endpoint':str(endpoint)})
        except (OSError,ValueError,EOFError,TimeoutError):pass
        time.sleep(.2)
    raise TimeoutError(f'Application pid {process.pid} still preparing; check endpoint and runtime.log')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--endpoint',required=True)
    parser.add_argument('--wait',action='store_true');parser.add_argument('--timeout',type=float,default=3600)
    parser.add_argument('command');parser.add_argument('--args',default='{}');parser.add_argument('--args-file')
    args=parser.parse_args(argv)
    try:
        params=json.loads(Path(args.args_file).read_text(encoding='utf8') if args.args_file else args.args)
        reply=start(params['app'],params['project'],args.endpoint) if args.command=='app.start' else call(args.endpoint,args.command,params)
        if args.wait and reply.get('task_id'):reply=wait(args.endpoint,reply['task_id'],args.timeout)
        print(json.dumps(reply,ensure_ascii=False,allow_nan=False))
        return 0 if reply.get('status') in ('accepted','completed') else 1
    except Exception as exc:
        print(json.dumps(dict(status='error',error={'type':type(exc).__name__,'message':str(exc)}),ensure_ascii=False));return 1

if __name__=='__main__':raise SystemExit(main())
