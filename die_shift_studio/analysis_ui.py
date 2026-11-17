"""Lazy result-page work; pose previews never start this controller."""
from .qt_compat import QtCore
from .core import Parameters,build_case
from .settings import ViewSettings


class ResultAnalysis(QtCore.QObject):
    def __init__(self,w):super().__init__(w);self.w=w;self.worker=None;self.pending=None;self.next_result=None
    def activate(self):
        w=self.w
        if w._closing or w.remote_locked or getattr(w,'requested_measured',False) or w.tabs.currentWidget() is not w.analysis_page:return
        r=w.result
        if r is None:w.statusBar().showMessage('请先进行方案评估');return
        if r.get('analysis_complete',bool(r.get('model_yield'))):
            if getattr(self,'drawn',None) is not r:w.render_analysis_result(r);self.drawn=r
            w.render_histograms();return
        if self.worker:
            self.next_result=r
            if self.pending is not r:self.worker.cancelled.set()
            return
        self.pending=r;data=w.data;group=w.cache_group;geometry=w.geometry_cache
        p=Parameters.from_dict(r['parameters']);roles=dict(r['roles']);order=r.get('layer_order',w.ordered_layers())
        cfg=ViewSettings.from_dict(r.get('analysis_settings',{}))
        from .gui import Worker
        def work(progress):
            from .stack import Connections
            from .contact import ContactModel
            from .fast_preview import GeometryCache
            from .core import evaluate
            cache=geometry or GeometryCache(data);connections=Connections(data,roles,order,cache.geometries)
            case=build_case(data,roles,connections=connections)
            model=ContactModel(data,case,roles,connections,cache,parameters=p)
            return evaluate(data,roles,p,progress=progress,connections=connections,contact_model=model,settings=cfg,safe_range_cache=group.values,include_analysis=True,validate_extremes=False)
        worker=Worker(work,w);self.worker=worker;w.watch_worker(worker,'结果分析')
        def accept(values):
            if worker.cancelled.is_set() or w._closing or w.result is not r or w.data is not data:return
            from .result_analysis import attach
            attach(r,values)
            if w.tabs.currentWidget() is w.analysis_page:w.render_analysis_result(r);self.drawn=r;w.render_histograms()
        def finish():
            self.worker=None;self.pending=None;worker.deleteLater()
            again=self.next_result;self.next_result=None
            if not w._closing and again is w.result:self.activate()
        worker.done.connect(accept);worker.failed.connect(w.error);worker.finished.connect(finish);worker.start()


    def cancel(self):
        self.next_result=None
        if self.worker:self.worker.cancelled.set()
