"""Qt owner of the opt-in local measured-output API and bounded batch jobs."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from pathlib import Path
import json,os,threading,time,uuid
import numpy as np
from .qt_compat import QtCore,QtWidgets as W,Signal
from .measured_core import Options,Session,write_gds
from .resources import cancellation_scope,resource_profile,cancel_check


class MeasuredOutput(QtCore.QObject):
    incoming=Signal(object,object,object);finished=Signal(str,object,object);progressed=Signal(str,int,str)
    def __init__(self,w,layout):
        super().__init__(w);self.w=w;self.server=None;self.session=None;self.options=Options();self.jobs=OrderedDict()
        self.pending_enable=False;self.pool=None;self.generation=0;self.batch=None;self.current=None;self.render_engine=None;self.ready=False;self.last_error=None;self.transition=None;self.running={};self.stopping=False;self.requests=OrderedDict()
        self.incoming.connect(self.dispatch);self.finished.connect(self.complete);self.progressed.connect(self.progress)
        self.toggle=W.QCheckBox('实测输出');self.toggle.setStyleSheet('font-weight:bold; padding:4px 2px;');layout.addWidget(self.toggle)
        self.settings_header,self.settings_body,form=w.collapsible(layout,'实测输出参数',w.setting_sections);self.toggle.toggled.connect(self.toggled)
        self.directory=W.QLineEdit();self.directory.setPlaceholderText('输出目录，可通过output.configure设置');form.addRow('输出目录',self.directory)
        self.memory=W.QSpinBox();self.memory.setRange(0,1048576);self.memory.setSpecialValueText('自动');self.memory.setSingleStep(128);form.addRow('最大内存 / MB',self.memory)
        self.threads=W.QSpinBox();self.threads.setRange(0,256);self.threads.setSpecialValueText('自动');form.addRow('最大线程',self.threads)
        self.endpoint=W.QLabel();self.endpoint.setWordWrap(True);form.addRow(self.endpoint)
        self.timer=QtCore.QTimer(self);self.timer.setInterval(75);self.timer.timeout.connect(self.tick);self.timer.start()
        w.task_control.button.clicked.connect(lambda:self.stop() if self.active() else None)
    def configuration(self):return asdict(Options.from_dict(dict(output_directory=self.directory.text(),max_memory_mb=self.memory.value(),max_threads=self.threads.value())))
    def restore(self,values,force=False):
        # An active controller's chosen destination/limits apply across project
        # switches. Ordinary GUI opening still restores per-project preferences.
        if self.active() and not force:return
        self.options=Options.from_dict(values);self.directory.setText(self.options.output_directory);self.memory.setValue(self.options.max_memory_mb);self.threads.setValue(self.options.max_threads)
    def active(self):return self.server is not None
    def apply_lock(self):
        if hasattr(self.w,'manual_preview'):self.w.manual_preview.setEnabled(not self.active())
        locked=bool(self.w.remote_locked)
        for i in range(min(3,self.w.controls.count())):self.w.controls.widget(i).setEnabled(not locked)
        for widget in (self.w.run_button,self.w.job_combo,self.w.save_button,self.w.preview_tabs):widget.setEnabled(not locked)
        for name in ('reconstruction_toggle','width_toggle','show_issues','space_toggle','probability_toggle','footprint_toggle'):
            widget=getattr(self.w,name,None)
            if widget is not None:widget.setEnabled(not locked)
        for widget in (self.directory,self.memory,self.threads):widget.setEnabled(not self.active())
        self.toggle.setEnabled(not self.stopping and not self.pending_enable)
        if self.active() or self.stopping:self.w.task_control.button.setEnabled(not self.stopping)
    def toggled(self,on):
        if on:
            try:self.enable()
            except Exception as exc:
                self.toggle.blockSignals(True);self.toggle.setChecked(False);self.toggle.blockSignals(False)
                self.w.error(str(exc))
        else:self.stop(disable=True)
    def enable(self,endpoint=None):
        if self.active():return
        w=self.w
        if w.worker is not None or w.preview_workers or w._tasks:
            self.pending_enable=True;self.toggle.setEnabled(False)
            w.statusBar().showMessage('等待当前任务完成后开启实测输出');return
        self.pending_enable=False
        w.cut_drag.cancel();w.exit_region_mode()
        self.options=Options.from_dict(self.configuration())
        self.saved_overlays=getattr(self,'startup_overlays',None) or {name:w.overlay_widget(name).isChecked() for name in ('reconstruction','width','drc','space','probability')}
        # Saving is a prerequisite; no IPC endpoint exists if persistence fails.
        if w.data is not None:
            from .jobs import job_path
            path=w.project_path or job_path(w.jobs_dir,'实测自动保存_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
            w.write_project(path);w.refresh_jobs()
        w.stop_animation();w.cancel_previews();w.mouse_die.exit();w.exit_region_mode()
        self.startup_overlays=None
        from .local_ipc import Server
        home=Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local'/'state'))/'DieShiftAnalyze'/'ipc'
        requested=endpoint or getattr(w,'requested_ipc_endpoint',None)
        dispatch=lambda request,event,reply:self.incoming.emit(request,event,reply)
        if requested:self.server=Server(requested,dispatch)
        else:
            try:self.server=Server(home/'endpoint.json',dispatch)
            except RuntimeError:
                self.server=Server(home/('endpoint-'+str(os.getpid())+'-'+uuid.uuid4().hex[:12]+'.json'),dispatch)
        w.requested_measured=False # startup-only guard; ordinary editing resumes after disabling IPC
        for name in self.saved_overlays:
            widget=w.overlay_widget(name);widget.blockSignals(True);widget.setChecked(False);widget.blockSignals(False)
        w.width_yield.reset();w.reconstruction_yield.reset();w.constraint_feedback.cancel()
        self.endpoint.setText(str(self.server.endpoint));w.remote_locked=True;self.ready=False;self.last_error=None;self.current=None
        self.pool=ThreadPoolExecutor(max_workers=max(1,min(32,self.options.max_threads or resource_profile()['workers'])),thread_name_prefix='dsa-measured')
        self.transition={'job':self.new_job('session.freeze'),'phase':'freeze'} if w.data is not None else None;self.apply_lock()
        w.log_bus.emit('实测输出已开启；等待指令，不进行方案评估或良率计算。IPC：'+str(self.server.endpoint))
    def new_job(self,command):
        key=uuid.uuid4().hex;self.jobs[key]=dict(task_id=key,command=command,status='queued',progress=0,created=time.time())
        while len(self.jobs)>10000:
            protected=set(self.batch['inflight']) if self.batch else set()
            old=next((k for k,v in self.jobs.items() if k not in protected and v['status'] in ('completed','error','cancelled')),None)
            if old is None:break
            del self.jobs[old]
        return key
    def submit(self,command,fn,kind='',job=None):
        if not self.running and not self.w._tasks:self.w.task_control.checkpoint()
        key=job or self.new_job(command);event=threading.Event();gen=self.generation
        issue_path=(self.w.project_path.parent if self.w.project_path else self.w.jobs_dir)/'Cache'/'MeasuredDiagnostics.sqlite'
        self.running[key]=(event,gen,kind);self.jobs[key]['status']='running'
        def work():
            try:
                with cancellation_scope(event):
                    last=[0.]
                    def progress(n,text):
                        cancel_check();now=time.monotonic()
                        if now-last[0]>.1:last[0]=now;self.progressed.emit(key,int(n),str(text))
                    result=fn(progress);cancel_check()
                    from .measured_issues import write
                    if kind=='preview':
                        self.progressed.emit(key,99,'保存完整DRC问题目录')
                        product,engine=result;frame,snapshot,details=product;result=((frame,snapshot,write(issue_path,key,details)),engine)
                    elif isinstance(result,dict):
                        if 'drc_issues' in result:self.progressed.emit(key,99,'保存完整DRC问题目录')
                        result=write(issue_path,key,result)
                self.finished.emit(key,result,None)
            except Exception as exc:
                import traceback
                self.w.log_bus.emit(traceback.format_exc(),'ERROR')
                payload=getattr(exc,'frame',None)
                if payload is not None:
                    from .measured_issues import write
                    try:
                        with cancellation_scope(event):
                            cancel_check();self.progressed.emit(key,99,'保存完整DRC问题目录')
                            payload=dict(payload,ipc_details=write(issue_path,key,getattr(exc,'details',{})))
                    except Exception as storage_error:
                        payload=dict(payload,ipc_details=dict(drc_status='catalog_write_error',message=str(storage_error)))
                self.finished.emit(key,payload,(type(exc).__name__,str(exc)))
        executor=self.batch['executor'] if kind=='batch.pose' and self.batch else self.pool
        executor.submit(work);return dict(status='accepted',task_id=key)
    def progress(self,key,n,message):
        if key not in self.jobs:return
        self.jobs[key].update(progress=max(0,min(100,n)),message=message)
        self.w.progress.show();self.w.progress.setValue(max(0,min(100,n)));self.w.progress.setFormat(message+' %p%')
    def complete(self,key,result,error):
        event,gen,kind=self.running.pop(key,(None,-1,''));job=self.jobs.get(key)
        if job is None:return
        if gen!=self.generation or (event and event.is_set()):
            if kind=='prepare' and result is not None:result.close()
            if kind=='preview' and result is not None:result[1].close()
            job.update(status='cancelled',error={'type':'Cancelled','message':'已取消，未应用过期结果'})
        elif error:
            job.update(status='error',error=dict(type=error[0],message=error[1]));self.w.log_bus.emit('实测输出 '+key+'：'+error[1],'ERROR')
            if kind=='prepare':self.ready=False;self.last_error=job['error']
            if isinstance(result,dict) and result.get('diagnostic_only'):
                job['result']=result.get('ipc_details',{});self.show_diagnostic(result)
        else:
            if kind=='prepare':
                if self.session and self.session is not result:self.session.close()
                self.session=result;self.w.case=result.case;self.w.geometry_cache=result.cache;self.ready=True;self.last_error=None;result={'ready':True,'project':str(self.w.project_path)}
            elif kind=='preview':
                (frame,snapshot,details),engine=result;self.current=(frame,snapshot,details);w=self.w
                if self.render_engine:self.render_engine.close()
                self.render_engine=engine;w.engine=engine
                w.current_frame=frame;w.shown_states=frame['states'];w.manual_states=frame['states'];w.active_preview=None
                w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
                w.statusBar().showMessage('实测预览完成；按指令的实际旋转中心计算，工程参数保持原值。')
                w.reconstruction_yield.evidence=None;w.update_state_table(frame['states']);w.plot_layout(preserve=True);w.fill_issues();result=details
            if isinstance(result,dict) and result.get('diagnostic_only'):
                message='当前姿态存在无法重构的连接，已显示诊断断口，禁止制造输出'
                self.w.log_bus.emit(message,'ERROR');job.update(status='error',progress=100,result=result,error=dict(type='ReconstructionFailure',message=message))
            else:job.update(status='completed',progress=100,result=result,finished=time.time())
        if kind=='batch.pose' and self.batch:
            self.batch['inflight'].discard(key);self.batch['completed']+=1
            if job['status']!='completed':
                self.batch['failed']+=1
                if len(self.batch['failures'])<100:self.batch['failures'].append(key)
        if kind=='batch.end' and self.batch and not any(v[2]=='batch.pose' for v in self.running.values()):
            self.batch['executor'].shutdown(wait=False);self.batch['store'].close();self.batch=None
            if self.session:self.session.close() # release per-thread clones, keep shared nominal plans
        self.apply_lock()
    def show_diagnostic(self,frame):
        w=self.w;self.current=None;w.current_frame=frame;w.shown_states=frame['states'];w.manual_states=frame['states'];w.active_preview=None
        w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
        w.plot_layout(preserve=True);w.fill_issues()
    def fail_transition(self,message):
        if self.transition:
            if self.w.data is self.transition.get('previous_data') and 'previous_overlays' in self.transition:self.saved_overlays=self.transition['previous_overlays']
            self.last_error=dict(type='ProjectError',message=str(message))
            self.jobs[self.transition['job']].update(status='error',error=self.last_error);self.transition=None;self.ready=False
            self.w.statusBar().showMessage('实测准备失败：'+str(message).splitlines()[0]+'；关闭实测输出后可调整工程。')
    def tick(self):
        w=self.w
        if self.pending_enable and not (w.worker or w.preview_workers or w._tasks):
            self.pending_enable=False
            if not w._closing:self.toggled(True)
            self.apply_lock()
        if self.stopping and not self.running and not w._tasks:
            if self.batch:
                self.batch['executor'].shutdown(wait=False);self.batch['store'].close();self.batch=None
                if self.session:self.session.close()
            if self.session and not self.active():self.session.close();self.session=None
            self.stopping=False
            self.ready=self.session is not None
            if not self.active():
                if self.render_engine:self.render_engine.close();self.render_engine=None
                w.remote_locked=False
                w.current_frame=None;w.shown_states=None;w.manual_states=None;w.active_preview=None;w.engine=None
                for name,enabled in getattr(self,'saved_overlays',{}).items():
                    widget=w.overlay_widget(name);widget.blockSignals(True);widget.setChecked(enabled);widget.blockSignals(False)
                if not getattr(w,'_closing',False) and w.data is not None:
                    w.plot_layout(preserve=True);w.request_preview()
                    w.statusBar().showMessage('实测输出已关闭，恢复工程参数和零偏移预览。')
                self.apply_lock();w.task_control.button.setEnabled(True)
        if self.active():self.apply_lock()
        if self.transition and not w.worker and not w.preview_workers and not w._tasks:
            t=self.transition;self.transition=None
            if w.data is None:
                self.transition=t;self.fail_transition('工程未能打开');return
            try:
                data,roles,motions,p,settings,cache=w.data,w.roles(),w.motions(),w.params(),w.view_settings,w.geometry_cache
                existing_case=w.case
            except Exception as exc:
                self.transition=t;self.fail_transition(str(exc));return
            def prepare(progress):
                from .core import build_case
                from .fast_preview import GeometryCache
                progress(5,'建立实测连接快照')
                actual_cache=cache or GeometryCache(data)
                case=existing_case or build_case(data,roles,layer_order=t['order'])
                session=Session(data,case,roles,motions,p,settings,actual_cache)
                try:
                    if t.get('plan'):session.prepare(progress)
                    progress(100,'实测工程就绪，等待偏移指令');return session
                except Exception:session.close();raise
            t['order']=w.ordered_layers()
            self.submit('session.prepare' if t.get('plan') else 'session.freeze',prepare,'prepare',t['job'])
        if self.active() and not self.running and not w._tasks:
            w.progress.hide();w.progress.setFormat('%p%')
    def stop(self,disable=False):
        self.pending_enable=False;self.generation+=1;self.ready=False;self.current=None
        for event,_,_ in self.running.values():event.set()
        if self.transition:self.jobs[self.transition['job']]['status']='cancelled';self.transition=None
        self.w.task_control.cancel();self.stopping=True
        if disable:
            if self.server:self.server.close();self.server=None
            if self.pool:self.pool.shutdown(wait=False);self.pool=None
        self.apply_lock()
    def dispatch(self,request,event,reply):
        try:
            if request['id'] in self.requests and request['command'] not in ('info','ping','task.get','task.issues'):reply.update(self.requests[request['id']])
            else:reply.update(self.command(request['command'],request.get('args',{})))
        except Exception as exc:reply.update(status='error',error=dict(type=type(exc).__name__,message=str(exc)))
        finally:
            # Busy did not accept work. The same operation ID may retry later.
            if request['command'] not in ('info','ping','task.get','task.issues') and reply.get('error',{}).get('type')!='Busy':self.requests[request['id']]=dict(reply)
            while len(self.requests)>10000:self.requests.popitem(last=False)
            event.set()
    def command(self,command,args):
        w=self.w
        if command=='ping':return dict(status='completed',result=dict(protocol=1,ready=self.ready,enabled=self.active()))
        if command=='task.get':
            key=args.get('task_id')
            if key not in self.jobs:raise ValueError('任务不存在或历史已过期')
            return dict(status='completed',result=dict(self.jobs[key]))
        if command=='task.issues':
            job=self.jobs.get(args.get('task_id'),{});catalog=job.get('result',{}).get('issue_catalog')
            if not catalog:raise ValueError('该任务尚无完整问题目录；请先查询task.get')
            from .measured_issues import page
            return dict(status='completed',result=page(catalog,args.get('offset',0),args.get('limit',128)))
        if command=='info':
            return dict(status='completed',result=dict(ready=self.ready,last_error=self.last_error,preparing_task=self.transition['job'] if self.transition else next((key for key,v in self.running.items() if v[2]=='prepare'),None),project=str(w.project_path) if w.project_path else None,
                source=w.data.path if w.data else None,top_cell=w.data.top_name if w.data else None,
                die_count=len(w.case['dies']) if w.case else 0,parameters=__import__('die_shift_studio.core',fromlist=['clean_json']).clean_json(asdict(w.params())) if w.data else {},
                current_pose=[dict(die=i+1,dx_um=float(z[0]),dy_um=float(z[1]),angle_deg=float(np.rad2deg(z[2])),rotation_center_um=list(self.current[0]['rotation_centers'][i])) for i,z in enumerate(self.current[0]['states'])] if self.current else None,
                roles=w.roles() if w.data else {},board_bounds_um=list(w.case['board'].bounds) if w.case else None,
                dies=[dict(die=i+1,center_um=list(d.centroid.coords[0]),bounds_um=list(d.bounds)) for i,d in enumerate(w.case['dies'])] if w.case else [],
                options=asdict(self.options),running=list(self.running),batch=self.batch['id'] if self.batch else None,
                batch_owner=self.batch.get('client_id') if self.batch else None,
                instance_id=self.server.instance_id if self.server else None,
                session_identity=self.session.identity if self.ready and self.session else None))
        if not self.active():raise RuntimeError('实测输出已关闭，不接受新的遥控命令')
        if command=='compute.cancel':self.stop();return dict(status='completed',result={'cancellation_requested':True,'running':list(self.running)})
        if command=='app.shutdown':
            w._closing=True;self.stop(disable=True);QtCore.QTimer.singleShot(100,w.close);return dict(status='completed',result={'shutdown_requested':True})
        if self.stopping:raise RuntimeError('取消尚未完成，请等待running清空')
        if command in ('project.open','project.close'):
            if self.running or self.batch or self.transition:raise RuntimeError('请先取消或结束当前任务')
            if w.data and w.project_path:w.write_project(w.project_path)
            self.ready=False;self.last_error=None;self.current=None
            if self.render_engine:self.render_engine.close();self.render_engine=None
            if self.session:self.session.close();self.session=None
            if command=='project.close':
                w.drop_result();w.data=None;w.case=None;w.project_path=None;w.geometry_cache=None;w.connections=None
                w.layer_table.setRowCount(0);w.role_boxes={};w.visible_boxes={};w._layer_keys=[];w.gds_label.clear()
                w.top.blockSignals(True);w.top.clear();w.top.blockSignals(False)
                w.ax.clear();w.canvas.draw_idle();w.refresh_jobs();return dict(status='completed',result={'closed':True})
            path=Path(args['path']).expanduser().resolve()
            if not path.is_file():raise ValueError('工程文件不存在')
            from .project import normalize_project
            normalize_project(json.loads(path.read_text(encoding='utf8')))
            key=self.new_job(command);self.transition=dict(job=key,phase='load',previous_data=w.data,previous_overlays=dict(self.saved_overlays));w.load_project(path)
            return dict(status='accepted',task_id=key)
        if command=='output.configure':
            if self.running or self.batch:raise RuntimeError('任务执行期间不能修改输出配置')
            if set(args)-Options.__dataclass_fields__.keys():raise ValueError('未知输出选项')
            options=Options.from_dict({**asdict(self.options),**args})
            if options.output_directory and not Path(options.output_directory).is_dir():raise ValueError('输出目录不存在')
            self.options=options
            self.restore(asdict(self.options),force=True)
            if w.data and w.project_path:w.write_project(w.project_path)
            return dict(status='completed',result=asdict(self.options))
        if command=='session.prepare':
            if self.running or self.batch or self.transition:raise RuntimeError('已有任务正在运行')
            self.last_error=None;key=self.new_job(command);self.transition=dict(job=key,phase='freeze',plan=True);return dict(status='accepted',task_id=key)
        if not self.ready or self.session is None:raise RuntimeError('尚未打开有效实测工程，查询info或调用project.open/session.prepare')
        if command=='preview.submit':
            if self.running or self.batch:raise RuntimeError('已有任务正在执行')
            rows=args['dies'];session=self.session
            from .measured_core import pose_parameters
            pose_parameters(session.case,session.p,rows)
            def preview(progress):
                product=session.run(rows,progress,preview=False,diagnostic=True);_,p=pose_parameters(session.case,session.p,rows)
                model,reconstructor=session.model(p)
                from .fast_preview import PreviewEngine
                engine=PreviewEngine(session.cache,session.case,session.roles,session.motions,p,None,session.case['connections'],contact_model=model)
                engine.bound.acceleration=session.settings.acceleration
                engine.reconstructor=reconstructor;engine.signature='measured-'+uuid.uuid4().hex
                return product,engine
            return self.submit(command,preview,'preview')
        if command=='export.gds':
            if self.running or self.current is None:raise RuntimeError('没有完成的实测预览，或任务尚未完成')
            _,snapshot,details=self.current
            if snapshot is None or details.get('diagnostic_only'):raise ValueError('失败姿态仅用于诊断显示，不能输出制造图纸')
            if details['connection_failure_count'] and not args.get('allow_failed',False):raise ValueError('存在未连接动区，拒绝正常生产输出；可显式allow_failed仅输出诊断用实际铜')
            destination=self.destination(args.get('filename','measured.gds'),'.gds')
            return self.submit(command,lambda progress:dict(write_gds(snapshot,destination,self.session.data.dbu_um),**details),'export')
        if command=='batch.begin':
            if self.running or self.batch:raise RuntimeError('已有任务或批次')
            client_id=args.get('client_id')
            if client_id is not None and (not isinstance(client_id,str) or len(client_id)!=32 or any(c not in '0123456789abcdef' for c in client_id)):raise ValueError('client_id须为32位小写十六进制')
            from .oas_stream import Store
            from .oas_errors import choose_error_layer
            policy=args.get('output_policy','strict')
            if policy not in ('strict','omit_failed_regions'):raise ValueError('未知实测输出策略')
            error_layer=choose_error_layer(self.session.cache.layers)
            failure_layer=choose_error_layer([*self.session.cache.layers,f'{error_layer[0]}/{error_layer[1]}'])
            profile=resource_profile();memory=self.options.memory_budget_mb(profile)
            per_lane=max(96,int(self.session.cache.bytes*24/1024**2));lanes=max(1,min(self.options.max_threads or profile['workers'],profile['workers'],int(memory*.7)//per_lane))
            if per_lane>memory:raise MemoryError(f'此图纸估计单任务需{per_lane} MB，超过批次内存限额{memory} MB')
            destination=self.destination(args.get('filename','measured.oas'),'.oas')
            try:
                import psutil
                baseline=psutil.Process().memory_info().rss
            except (ImportError,OSError):baseline=0
            self.batch=dict(id=uuid.uuid4().hex,client_id=client_id,store=Store(destination.parent,self.session.data.dbu_um,max(1024**2,memory*1024**2//5),checkpoint_destination=destination.with_name(destination.stem+'.'+uuid.uuid4().hex[:12]+'.partial.oas'),error_layer=error_layer,failure_layer=failure_layer),lock=threading.Lock(),path=destination,baseline=baseline,
                            executor=ThreadPoolExecutor(max_workers=lanes,thread_name_prefix='dsa-batch'),pending=set(),inflight=set(),completed=0,failed=0,failures=[],ending=False,lanes=lanes,memory_mb=memory,semaphore=threading.Semaphore(lanes),output_policy=policy)
            return dict(status='completed',result=dict(batch_id=self.batch['id'],threads=lanes,max_memory_mb=memory,queue_limit=lanes*4,output_policy=policy,error_layer=dict(layer=error_layer[0],datatype=error_layer[1],name='ERROR'),failure_layer=dict(layer=failure_layer[0],datatype=failure_layer[1],name='FAIL')))
        if command=='batch.submit':
            batch=self.batch
            if batch is None or batch['ending']:raise RuntimeError('批次未开始或已结束接收')
            if args.get('batch_id')!=batch['id']:raise ValueError('批次标识不匹配，拒绝旧批次或其他控制端的请求')
            unfinished=batch['inflight']
            if len(unfinished)>=batch['lanes']*4:return dict(status='error',error=dict(type='Busy',message='批次队列已满',retry_after_ms=200))
            name=args['cell'];position=args['position_um'];rows=args['dies'];session=self.session
            from .measured_core import pose_parameters
            pose_parameters(session.case,session.p,rows)
            if (not isinstance(name,str) or not 1<=len(name)<=120 or name=='TopCell' or name.startswith('G_')
                or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name) or name in batch['pending']):raise ValueError('Cell名称无效、保留或重复')
            position=np.asarray(position,dtype=float)
            if position.shape!=(2,) or not np.isfinite(position).all() or np.max(abs(position))>1e9:raise ValueError('封装坐标须为有限XY，范围±1e9 μm')
            batch['pending'].add(name)
            def produce(progress):
                while not batch['semaphore'].acquire(timeout=.1):cancel_check()
                try:
                    from .resources import serial_scope
                    def bounded_progress(n,message):
                        progress(n,message)
                        if batch['baseline']:
                            import psutil
                            if psutil.Process().memory_info().rss-batch['baseline']>batch['memory_mb']*1024**2:
                                with batch['lock']:batch['store'].flush()
                                raise MemoryError('批次增量内存超过配置上限；已分段暂存，任务未输出，请降低并发或提高预算')
                    try:
                        with serial_scope():frame,snapshot,details=session.run(rows,bounded_progress,preview=False,output_policy=batch['output_policy'])
                    except Exception as exc:
                        if hasattr(exc,'details'):
                            from .measured_issues import place
                            exc.details=place(dict(exc.details,cell=name,position_um=position.tolist()),position)
                        raise
                    from .measured_issues import place
                    details=place(details,position)
                    if details['connection_failure_count'] and not (batch['output_policy']=='omit_failed_regions' and details.get('partial_output') and details.get('omitted_region_count',0)>0):raise ValueError('存在未处理的连接失败，拒绝输出')
                    cancel_check()
                    with batch['lock']:
                        markers=batch['store'].add(name,snapshot.layers,position,session.case['board'].bounds[:2],errors=details['drc_issues'],die_count=len(session.case['dies']),failure_outline=session.case['board'].wkb if details.get('connection_failure_count',0)>0 else None)
                        if batch['store'].count==1 or batch['store'].count%64==0:batch['store'].flush()
                    return dict(cell=name,checkpoint=str(batch['store'].checkpoint_destination),checkpoint_cells=batch['store'].checkpoint_count,**markers,**details)
                finally:batch['semaphore'].release()
            response=self.submit(command,produce,'batch.pose');batch['inflight'].add(response['task_id']);return response
        if command=='batch.end':
            batch=self.batch
            if batch is None or batch['ending']:raise RuntimeError('没有可结束的批次')
            if args.get('batch_id')!=batch['id']:raise ValueError('批次标识不匹配')
            batch['ending']=True
            def finish(progress):
                while batch['inflight']:cancel_check();time.sleep(.05)
                if batch['failed']:raise ValueError(f"批次有{batch['failed']}项失败/取消任务，保留旧输出文件。前100任务："+','.join(batch['failures']))
                with batch['lock']:return batch['store'].write(batch['path'],progress)
            return self.submit(command,finish,'batch.end')
        raise ValueError('未知命令：'+command)
    def destination(self,name,extension):
        directory=Path(self.options.output_directory).expanduser()
        if not self.options.output_directory or not directory.is_dir():raise ValueError('请先用output.configure设置已有输出目录')
        if not isinstance(name,str) or Path(name).name!=name or not name.lower().endswith(extension):raise ValueError('filename须为单个文件名，后缀'+extension)
        candidate=directory.resolve()/name
        if candidate.is_symlink():raise ValueError('输出文件是符号链接，请使用普通文件名')
        path=candidate.resolve()
        if self.w.data and path==Path(self.w.data.path).resolve():raise ValueError('不得覆盖输入源图纸')
        return path
