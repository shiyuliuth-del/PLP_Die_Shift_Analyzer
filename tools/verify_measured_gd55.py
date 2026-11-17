"""Standalone stdlib client verification for GD55-1105. Never edits source GDS.

Example: python verify_measured_gd55.py --endpoint C:/work/ipc.json
  --project C:/work/gd55-1105.dshift.json --output C:/work/check --app C:/DSA/main.py
The caller's Python needs only the standard library. OAS/GDS readback is optional
when gdstk is installed; the application always validates output before commit.
"""
import argparse,json,math,os,subprocess,sys,time,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Client

def rpc(endpoint,command,args=None):
    info=json.loads(endpoint.read_text(encoding='utf8'));identity=uuid.uuid4().hex
    with Client(info['address'],family=info['family'],authkey=bytes.fromhex(info['token'])) as conn:
        conn.send_bytes(json.dumps(dict(id=identity,command=command,args=args or {}),allow_nan=False).encode())
        if not conn.poll(40):raise TimeoutError('IPC response timeout')
        response=json.loads(conn.recv_bytes(2*1024**2))
    assert response['id']==identity
    return response

def checked(endpoint,command,args=None):
    reply=rpc(endpoint,command,args)
    if reply['status']=='error':raise RuntimeError(json.dumps(reply,ensure_ascii=False))
    return reply

def wait(endpoint,reply,timeout=3600):
    if 'task_id' not in reply:return reply['result']
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        task=checked(endpoint,'task.get',{'task_id':reply['task_id']})['result']
        if task['status']=='completed':return task['result']
        if task['status'] in ('error','cancelled'):raise RuntimeError(json.dumps(task,ensure_ascii=False))
        time.sleep(.2)
    raise TimeoutError('Still running: '+reply['task_id'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--endpoint',required=True,type=Path)
    p.add_argument('--project',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--app',type=Path);p.add_argument('--keep-open',action='store_true');p.add_argument('--count',type=int,default=8)
    args=p.parse_args();endpoint=args.endpoint.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if not 1<=args.count<=100000:raise ValueError('count must be 1..100000')
    process=None;started=time.perf_counter();report=dict(project=str(args.project.resolve()),diagnostic_only=True,commands=[])
    try:
        if args.app:
            app=args.app.resolve();command=([sys.executable,str(app)] if app.suffix.lower()=='.py' else [str(app)])
            process=subprocess.Popen(command+['--project',str(args.project.resolve()),'--measured-output','--ipc-endpoint',str(endpoint)],cwd=app.parent,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            end=time.monotonic()+180
            while not endpoint.exists() and time.monotonic()<end:
                if process.poll() is not None:raise RuntimeError('Application exited before IPC became available')
                time.sleep(.2)
            if not endpoint.exists():raise TimeoutError('No endpoint; inspect application runtime log')
            end=time.monotonic()+3600
            while time.monotonic()<end:
                state=checked(endpoint,'info')['result']
                if state['ready']:break
                if state.get('last_error'):raise RuntimeError(json.dumps(state['last_error'],ensure_ascii=False))
                time.sleep(.3)
        else:wait(endpoint,checked(endpoint,'project.open',{'path':str(args.project.resolve())}))
        info=checked(endpoint,'info')['result'];assert info['ready'] and info['die_count']>0
        report['source']=info['source'];report['die_count']=info['die_count']
        checked(endpoint,'output.configure',dict(output_directory=str(output),max_threads=4,max_memory_mb=1024))
        zeros=[dict(die=d['die'],dx_um=0.,dy_um=0.,angle_deg=0.,rotation_center_um=d['center_um']) for d in info['dies']]
        preview=wait(endpoint,checked(endpoint,'preview.submit',{'dies':zeros}));report['preview']=preview
        # Single-pose GDS remains strict. Connection failures are real errors;
        # the retired allow_failed flag cannot bypass this gate.
        report['gds']=wait(endpoint,checked(endpoint,'export.gds',{'filename':'gd55_measured.gds'}))
        # Partial batch output requires explicit agreement from both endpoints.
        batch=checked(endpoint,'batch.begin',{'filename':'gd55_batch.oas','output_policy':'omit_failed_regions'})['result']
        if batch.get('output_policy')!='omit_failed_regions':
            raise RuntimeError('Server did not confirm the requested partial-output policy')
        report['batch']=batch
        bounds=info['board_bounds_um'];pitch=[bounds[2]-bounds[0]+100,bounds[3]-bounds[1]+100]
        columns=math.ceil(math.sqrt(args.count))
        def send(i):
            rows=[dict(d,dx_um=(-1 if i&1 else 1)*min(i,.5),dy_um=(-1 if i&2 else 1)*min(i,.5),angle_deg=(-1 if i&4 else 1)*min(i*.001,.005)) for d in zeros]
            params=dict(batch_id=batch['batch_id'],cell=f'Package_{i:06d}',position_um=[(i%columns)*pitch[0],(i//columns)*pitch[1]],dies=rows)
            while True:
                response=rpc(endpoint,'batch.submit',params)
                if response['status']!='error':return wait(endpoint,response)
                if response['error']['type']!='Busy':raise RuntimeError(str(response))
                time.sleep(response['error'].get('retry_after_ms',200)/1000)
        with ThreadPoolExecutor(max_workers=min(8,args.count)) as executor:report['poses']=list(executor.map(send,range(args.count)))
        report['oas']=wait(endpoint,checked(endpoint,'batch.end',{'batch_id':batch['batch_id']}));assert report['oas']['cells']==args.count
        try:
            import gdstk
            lib=gdstk.read_oas(report['oas']['path']);top=lib.top_level()
            assert len(top)==1 and top[0].name=='TopCell' and len(top[0].references)==args.count
            # Isolate the external checksum reader: some gdstk builds keep the
            # file open, which would otherwise lock the output on Windows.
            subprocess.run([sys.executable,'-c','import gdstk,sys; assert gdstk.oas_validate(sys.argv[1])[0]',report['oas']['path']],check=True)
            assert gdstk.read_gds(report['gds']['path']).top_level()
            report['independent_readback']='gdstk passed'
        except ImportError:report['independent_readback']='not run; caller has no gdstk'
        checked(endpoint,'compute.cancel');report['status']='PASS';report['seconds']=time.perf_counter()-started
    except Exception as exc:
        report.update(status='ERROR',error={'type':type(exc).__name__,'message':str(exc)})
        raise
    finally:
        (output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        if not args.keep_open:
            try:checked(endpoint,'app.shutdown')
            except (OSError,EOFError,RuntimeError):pass
            if process:
                code=process.wait(timeout=120)
                if code:
                    report.update(status='ERROR',error=dict(type='ApplicationExit',message=f'Application exited with {code}'))
                    (output/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
                    raise RuntimeError(report['error']['message'])
    print(json.dumps(dict(status=report['status'],report=str(output/'verification.json'),seconds=report['seconds']),ensure_ascii=False))

if __name__=='__main__':main()
