"""Latest-request-only reconstruction checks and full-input yield, off the GUI."""
from dataclasses import asdict,replace
from .qt_compat import QtCore
from .session_cache import logical_fingerprint


class ReconstructionYieldDisplay:
    def __init__(self,w):
        self.w=w;self.worker=None;self.pending=None;self.key=None;self.bank=None;self.plan_pool={}
        self.timer=QtCore.QTimer(w);self.timer.setSingleShot(True);self.timer.setInterval(220);self.timer.timeout.connect(self.start)
    def reset(self):
        self.key=None;self.bank=None;self.pending=None;self.timer.stop()
        if self.worker:self.worker.cancelled.set()
        self.w.width_yield.label.set_extra('')
        if self.w.result is not None:self.w.result.pop('reconstruction_yield',None)
    def refresh(self,settings=None):
        w=self.w
        if w.loading or w._closing:return
        if not w.reconstruction_toggle.isChecked() or w.data is None:self.reset();return
        try:p=replace(w.params(),target_nsigma=0)
        except ValueError:self.reset();return
        cfg=settings or w.view_settings
        token=('reconstruction-yield-v39',logical_fingerprint((w.data.geometry_id,w.data.top_name,w.roles(),w.ordered_layers(),asdict(p),cfg.reconstruction_key(),cfg.reconstruction_grid_nodes)))
        key=(id(w.cache_group),token)
        if key==self.key:self.render();return
        self.reset();self.key=key
        self.pending=(key,w.data,p,cfg,w.roles(),w.ordered_layers(),w.geometry_cache,w.cache_group)
        w.width_yield.label.set_extra('重构良率 · 计算中…','按完整输入偏移分布检查最终固定RDL；结果未完成。')
        self.timer.start()
    def start(self):
        if self.worker or self.pending is None or self.w._closing:return
        from .scheduling import admission,job_budget
        admitted=admission(len(self.w._tasks),256)
        if not admitted['start']:self.timer.start(250);return
        key,data,p,cfg,roles,order,cache,group=self.pending;self.pending=None
        completed=getattr(getattr(self.w,'engine',None),'reconstructor',None)
        if completed is not None and completed._plans is not None and not self.w.preview_workers:
            import copy
            self.plan_pool.update(copy.deepcopy(completed.plan_pool))
        while len(self.plan_pool)>3:self.plan_pool.pop(next(iter(self.plan_pool)))
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
                engine=Reconstructor(model,p,cfg,plan_pool=self.plan_pool)
                try:return measure_reconstruction_yield(engine,p,cfg,progress)
                finally:engine.close()
            with job_budget(admitted['threads'],admitted['processes']):return group.values.get_or_compute(key[1],calculate)
        worker=Worker(work,self.w);self.worker=worker;self.w.watch_worker(worker,'重构良率')
        def accept(bank):
            if self.key==key and not self.w._closing:self.bank=bank;self.render()
        def failed(text):
            if self.key==key and not worker.cancelled.is_set():self.w.width_yield.label.set_extra('重构良率 · 无法计算',text.split('Traceback')[0])
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        worker.done.connect(accept);worker.failed.connect(failed);worker.finished.connect(finish);worker.start()
    def render(self):
        if self.bank is None:return
        from .width_yield import percentage_text
        b=self.bank;decimals=self.w.view_settings.width_yield_decimals
        text='重构良率 ≈'+percentage_text(b['value'],b['failure'],decimals)
        tip=f"{b['scope']}\n每轴{b['grid_nodes']}节点，完整联合网格{b['total']:,}个姿态；局部{b['local_checks']:,}次、异网{b['pair_checks']:,}次。\n{b['limitation']}\n0/∞沿用最近有限概率尺度：{b['scale_nsigma']:g}σ。"
        self.w.width_yield.label.set_extra(text,tip)
        if self.w.result is not None:self.w.result['reconstruction_yield']=b;self.w.report_dirty=True


class ConstraintFeedback:
    def __init__(self,w):
        self.w=w;self.worker=None;self.pending=None;self.ticket=0;self.context=None
        self.timer=QtCore.QTimer(w);self.timer.setSingleShot(True);self.timer.setInterval(40);self.timer.timeout.connect(self.start)
    def cancel(self):
        self.ticket+=1;self.pending=None;self.timer.stop()
        if self.worker:self.worker.cancelled.set()
    def request(self,frame,settings):
        self.cancel();w=self.w
        self.pending=(self.ticket,frame,settings,w.data,w.params(),w.roles(),w.ordered_layers(),w.geometry_cache)
        self.timer.start()
    def start(self):
        if self.worker or self.pending is None or self.w._closing:return
        ticket,frame,cfg,data,p,roles,order,cache=self.pending;self.pending=None
        from .gui import Worker
        def work(progress):
            key=logical_fingerprint((data.geometry_id,roles,order,asdict(p),cfg.drc_tolerance_um))
            if self.context is None or self.context[0]!=key:
                from .core import build_case
                from .stack import Connections
                from .contact import ContactModel
                from .reconstruction_rules import FixedRules
                con=Connections(data,roles,order,cache.geometries);case=build_case(data,roles,connections=con)
                model=ContactModel(data,case,roles,con,cache,parameters=p)
                self.context=(key,FixedRules(model,p,cfg))
            return self.context[1].check(frame['reconstruction']['replacements'],frame['states'],progress)
        worker=Worker(work,self.w);self.worker=worker;self.w.watch_worker(worker,'拖动约束检查',quiet=True)
        def accept(violations):
            w=self.w
            if ticket!=self.ticket or w.current_frame is not frame or w._closing:return
            data=dict(frame['reconstruction'],violations=violations,constraints_complete=True)
            w.current_frame=dict(frame,reconstruction=data)
            w.reconstruction_overlay.draw();w.render_scene()
            w.statusBar().showMessage(f'重构约束：{len(violations)} 组违规' if violations else '当前固定RDL线宽、线距及过孔包围量检查通过')
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        worker.done.connect(accept);worker.finished.connect(finish);worker.start()
