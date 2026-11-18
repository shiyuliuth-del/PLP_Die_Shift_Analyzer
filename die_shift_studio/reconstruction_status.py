"""Latest-request-only reconstruction checks and full-input yield, off the GUI."""
from dataclasses import asdict,replace
from .qt_compat import QtCore
from .session_cache import logical_fingerprint


class ReconstructionYieldDisplay:
    def __init__(self,w):
        self.w=w;self.worker=None;self.pending=None;self.key=None;self.bank=None;self.evidence=None;self.plan_pool={}
        from .session_cache import EphemeralCache
        from .resources import resource_profile
        self.geometry_pool=EphemeralCache(budget=64*1024**2,max_entries=4096)
        self.factor_pool=EphemeralCache(budget=min(128*1024**2,int(resource_profile()['available_memory_mb']*1024**2*.06)),max_entries=512)
        self.timer=QtCore.QTimer(w);self.timer.setSingleShot(True);self.timer.setInterval(220);self.timer.timeout.connect(self.start)
    def reset(self):
        self.key=None;self.bank=None;self.evidence=None;self.pending=None;self.timer.stop()
        if self.worker:self.worker.cancelled.set()
        self.w.width_yield.label.set_extra('')
        if self.w.result is not None:self.w.result.pop('reconstruction_yield',None)
    def refresh(self,settings=None):
        w=self.w
        if w.loading or w._closing or getattr(w,'remote_locked',False) or getattr(w,'reconstruction_pending',False):return
        if w.result is None or getattr(w,'_restoring_project',False):self.reset();return
        if not w.reconstruction_toggle.isChecked() or w.data is None:self.reset();return
        try:p=replace(w.params(),target_nsigma=0)
        except ValueError:self.reset();return
        cfg=settings or w.view_settings
        token=('reconstruction-analysis-continuity',logical_fingerprint((w.data.geometry_id,w.data.top_name,w.roles(),w.ordered_layers(),asdict(p),cfg.reconstruction_key(),cfg.reconstruction_grid_nodes if cfg.evaluation_scope=='all' else 0)))
        key=(id(w.cache_group),token)
        if key==self.key:self.render();return
        self.reset();self.key=key
        self.pending=(key,w.data,p,cfg,w.roles(),w.ordered_layers(),w.geometry_cache,w.cache_group)
        w.width_yield.label.set_extra('重构良率 · 计算中…','按完整输入偏移分布检查最终固定RDL；结果未完成。')
        self.timer.start()
    def start(self):
        if self.worker or self.pending is None or self.w._closing:return
        w=self.w
        # Foreground preview owns route construction; background analyses reuse
        # its completed plan instead of building the same style a second time.
        if w.preview_workers or w.preview_pending or (getattr(w,'cut_drag',None) and w.cut_drag.state is not None):
            self.timer.start(120);return
        from .scheduling import admission,job_budget
        admitted=admission(len(self.w._tasks),256)
        if not admitted['start']:self.timer.start(250);return
        key,data,p,cfg,roles,order,cache,group=self.pending;self.pending=None
        completed=getattr(getattr(self.w,'engine',None),'reconstructor',None)
        if completed is not None and completed._plans is not None and not self.w.preview_workers:
            self.plan_pool.update(clone_plan_pool(completed.plan_pool))
        while len(self.plan_pool)>max(64,4*len(getattr(completed,'plan_pool',{}))):self.plan_pool.pop(next(iter(self.plan_pool)))
        from .gui import Worker
        def work(progress):
            def calculate():
                from .core import build_case
                from .stack import Connections
                from .fast_preview import GeometryCache
                from .contact import ContactModel
                from .reconstruction import Reconstructor
                from .reconstruction_yield import measure_reconstruction_yield
                geom=cache or GeometryCache(data);con=Connections(data,roles,order,geom.geometries)
                case=build_case(data,roles,connections=con);model=ContactModel(data,case,roles,con,geom,parameters=p)
                engine=Reconstructor(model,p,cfg,plan_pool=self.plan_pool,geometry_pool=self.geometry_pool)
                try:
                    from .statistics import ExtremeCatalog
                    from .reconstruction_events import scan
                    from .reconstruction_probability import attach
                    evidence_key=('reconstruction-extreme-events-continuity',logical_fingerprint((data.geometry_id,data.top_name,roles,order,asdict(p),cfg.reconstruction_key())))
                    if cfg.evaluation_scope!='all':
                        from .evaluation_scope import focused_catalog,focused_reconstruction
                        grid,indices=focused_catalog(model,p,scope=cfg.evaluation_scope)
                        evidence,bank=focused_reconstruction(engine,grid,indices,progress)
                        return {'yield':bank,'evidence':evidence}
                    evidence=group.values.get_or_compute(evidence_key,lambda:attach(scan(engine,ExtremeCatalog(case,p),lambda n,t:progress(round(n*.8),t),source_cache=group.values),engine))
                    worker.publish({'evidence':evidence})
                    bank=measure_reconstruction_yield(engine,p,cfg,lambda n,t:progress(80+round(n*.2),t),self.factor_pool,
                        lambda value:worker.publish({'yield':value,'evidence':evidence}))
                    return {'yield':bank,'evidence':evidence}
                finally:engine.close()
            with job_budget(admitted['threads'],admitted['processes']):return group.values.get_or_compute(key[1],calculate)
        worker=Worker(work,self.w);self.worker=worker;self.w.watch_worker(worker,'重构良率')
        def accept(value):
            if value is None or self.key!=key or self.w._closing or worker.cancelled.is_set():return
            if isinstance(value,tuple):kind,bank=value;value={kind:bank}
            if 'evidence' in value and self.evidence is not value['evidence']:
                self.evidence=value['evidence'];self.w._problem_tag=None
                self.w.filter_changed(pending_ok=True)
                if self.w.cut_drag.state is None:
                    self.w.reconstruction_overlay.draw();self.w.render_scene()
            if 'yield' in value:self.bank=value['yield'];self.render()
        def failed(text):
            if self.key==key and not worker.cancelled.is_set():self.w.width_yield.label.set_extra('重构良率 · 无法计算',text.split('Traceback')[0])
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        worker.partial.connect(lambda _:accept(worker.take_partial()));worker.done.connect(accept);worker.failed.connect(failed);worker.finished.connect(finish);worker.start()
    def render(self):
        if self.bank is None:return
        from .width_yield import percentage_text
        b=self.bank;decimals=self.w.view_settings.width_yield_decimals
        text=({'risk':'极端组合良率 ','focus':'重点组合良率 '}.get(b.get('method'),'重构良率 '))+('≤' if b.get('partial') else '≈')+percentage_text(b['value'],b['failure'],decimals)+('（检查中）' if b.get('partial') else '')
        tip=f"{b['scope']}\n本范围{b['total']:,}个姿态；局部{b['local_checks']:,}次、异网{b['pair_checks']:,}次。\n{b['limitation']}\n0/∞沿用最近有限概率尺度：{b['scale_nsigma']:g}σ。"
        self.w.width_yield.label.set_extra(text,tip)
        if self.w.result is not None:self.w.result['reconstruction_yield']=b


class ConstraintFeedback:
    def __init__(self,w):
        self.w=w;self.worker=None;self.pending=None;self.ticket=0;self.context=None
        self.timer=QtCore.QTimer(w);self.timer.setSingleShot(True);self.timer.setInterval(40);self.timer.timeout.connect(self.start)
    def cancel(self):
        self.ticket+=1;self.pending=None;self.timer.stop()
        if self.worker:self.worker.cancelled.set()
    def request(self,frame,settings):
        self.cancel();w=self.w
        self.pending=(self.ticket,frame,settings,w.data,w.params(),w.roles(),w.ordered_layers(),w.geometry_cache,getattr(w.engine,'reconstructor',None))
        self.timer.start()
    def start(self):
        if self.worker or self.pending is None or self.w._closing:return
        ticket,frame,cfg,data,p,roles,order,cache,current=self.pending;self.pending=None
        from .gui import Worker
        # The foreground may lease/reset w.engine before this timer runs.
        # Keep the completed source solver captured with the drag snapshot.
        pool=clone_plan_pool(current.plan_pool) if current is not None else {}
        changed=tuple(frame.get('_reconstruction_changed_refs',()))
        base=getattr(current,'_base_plans',None)
        local_pool=clone_plan_pool({'local':[q for q in base if (q['layer'],q['index']) in changed]}) if base is not None and changed else None
        def reconstruction(progress):
            key=logical_fingerprint((data.geometry_id,roles,order,asdict(p),cfg.drc_tolerance_um))
            if self.context is None or self.context[0]!=key:
                from .core import build_case
                from .stack import Connections
                from .contact import ContactModel
                from .reconstruction_rules import FixedRules
                con=Connections(data,roles,order,cache.geometries);case=build_case(data,roles,connections=con)
                model=ContactModel(data,case,roles,con,cache,parameters=p)
                self.context=(key,FixedRules(model,p,cfg))
            if cfg.reconstruction_boundary_mode!='perpendicular':
                from .reconstruction import Reconstructor
                rebuilt=Reconstructor(self.context[1].model,p,cfg,plan_pool=pool)
                try:
                    if not changed:return rebuilt.frame(frame['states'],progress=progress)
                    if local_pool is not None:
                        from .reconstruction_zones import expand
                        rebuilt.auto_following=set(current.auto_following);rebuilt.auto_ignored=dict(current.auto_ignored);rebuilt.safe_vias=dict(current.safe_vias)
                        rebuilt._envelopes=current._envelopes;rebuilt._plans=expand(rebuilt,local_pool['local'])
                    local=rebuilt.frame(frame['states'],targets=changed,diagnostics=False,progress=progress,_raw=True)
                    frame_data=frame['reconstruction'];refs=set(changed)
                    copper={k:[v for v in rows if not any((k,i) in refs for i in v['indices'])] for k,rows in frame_data['replacements'].items()}
                    for k,rows in local['replacements'].items():copper.setdefault(k,[]).extend(rows)
                    pieces=[dict(q) for q in frame_data['pieces'] if (q['layer'],q['index']) not in refs]+local['pieces']
                    from .topology_routing import resolve
                    copper,pieces,repaired,blocked=resolve(rebuilt,copper,pieces,frame['states'])
                    return dict(frame_data,pieces=pieces,replacements=copper,violations=self.context[1].check(copper,frame['states'],progress),
                                original_violations=self.context[1].nominal_check(),constraints_complete=True,repair_refs=repaired,hard_failures=blocked,electrically_valid=not bool(blocked))
                finally:rebuilt.cache.close()
            return self.context[1].check(frame['reconstruction']['replacements'],frame['states'],progress)
        def work(progress):
            value=reconstruction(lambda n,t:progress(round(n*.6),t))
            result=value if isinstance(value,dict) else dict(frame['reconstruction'],pieces=[dict(q) for q in frame['reconstruction']['pieces']],violations=value,constraints_complete=True)
            from .drc import GlobalDRC
            from .current_drc import locations
            if getattr(self,'drc_context',None) is not self.context:
                self.drc_context=self.context;self.global_drc=GlobalDRC(self.context[1].model,p)
            drc=self.global_drc.frame(frame['states'],result['replacements'],cfg,lambda n,t:progress(60+round(n*.4),t))
            return result,drc,locations(drc)
        worker=Worker(work,self.w);self.worker=worker;self.w.watch_worker(worker,'拖动约束检查',quiet=True)
        def accept(value):
            w=self.w
            if ticket!=self.ticket or w.current_frame is not frame or w._closing:return
            data,drc,locations=value
            if data.get('hard_failures'):
                from .topology_routing import failure_message
                w.log_bus.emit(failure_message(data['hard_failures']),'ERROR')
                from .reconstruction_diagnostic import display
                data=display(data,frame['rotation_centers'],frame['states'])
            violations=data['violations']
            from .reconstruction import decorate_problems
            decorate_problems(data)
            w.current_frame=dict(frame,reconstruction=data,drc=drc,drc_locations=locations,drc_pending=False)
            w.plot_layout(preserve=True)
            w.statusBar().showMessage(f'重构约束：{len(violations)} 组违规' if violations else '当前固定RDL线宽、线距及过孔包围量检查通过')
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        worker.done.connect(accept);worker.finished.connect(finish);worker.start()


def clone_plan_pool(pool):
    """Immutable geometries may be shared; mutable memo dictionaries may not."""
    from collections import OrderedDict
    clones={};out={}
    for key,entries in pool.items():
        copied=[]
        for entry in entries:
            q=dict(entry);plan=entry.get('plan')
            if plan is not None:
                if id(plan) not in clones:
                    clone=object.__new__(type(plan));clone.__dict__=dict(plan.__dict__)
                    clone.cache=dict(plan.cache);clone.pose_memo=OrderedDict(plan.pose_memo)
                    clones[id(plan)]=clone
                q['plan']=clones[id(plan)]
            copied.append(q)
        out[key]=copied
    return out
