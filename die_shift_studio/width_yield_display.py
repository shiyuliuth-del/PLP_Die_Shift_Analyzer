"""Background whole-layout calculation and lightweight reclassification of ignored lines."""
from dataclasses import asdict,replace
from decimal import ROUND_FLOOR,ROUND_CEILING
from .qt_compat import QtWidgets as W,QtCore,QtGui,ALIGN_CENTER
from .session_cache import logical_fingerprint
from .width_yield import measure_width_yield,policy_yields,policy_texts,percentage_text

POLICIES=('全修正良率','不修正良率')


class YieldLabel(W.QLabel):
    """Keep both numbers on the toolbar row without changing the plot height."""
    def __init__(self,parent):
        super().__init__(parent)
        self.parts=None;self.pixel_size=12;self.width_active=False;self.width_text="";self.extra_text="";self.width_tip="";self.extra_tip=""
        self.setAlignment(ALIGN_CENTER);self.setContentsMargins(4,0,4,0)
        self.setStyleSheet('font-size:12px');self.ensurePolished()
        self.setMinimumWidth(0)

    def minimumSizeHint(self):
        return QtCore.QSize(0,self.height())

    def set_values(self,prefix,labels,suffix):
        self.parts=(prefix,labels,suffix);self.reflow()

    def set_width_active(self,enabled):
        self.width_active=enabled;self.reflow()

    def set_extra(self,text,tip=''):
        self.extra_text=text;self.extra_tip=tip;self.reflow()

    def setText(self,text):
        self.parts=None;self.width_text=text;self.reflow()

    def setToolTip(self,text):
        self.width_tip=text;super().setToolTip('\n\n'.join(t for t in (self.width_tip,self.extra_tip) if t))

    def resizeEvent(self,event):
        super().resizeEvent(event);self.reflow()

    def reflow(self):
        prefix,labels,suffix=self.parts or (self.width_text,[],"")
        width=max(1,self.width()-self.contentsMargins().right()-self.contentsMargins().left())
        font=QtGui.QFont(self.font());font.setPixelSize(12)
        metrics=QtGui.QFontMetrics(font)
        base=prefix+'  ·  '.join(labels)+suffix
        whole='  ·  '.join(t for t in (base if self.width_active else '',self.extra_text) if t)
        self.setVisible(self.width_active or bool(self.extra_text))
        super().setToolTip('\n\n'.join(t for t in (self.width_tip if self.width_active else '',self.extra_tip) if t))
        text=whole
        size=12
        while size>8 and max((metrics.horizontalAdvance(line) for line in text.splitlines()),default=0)>width:
            size-=1;font.setPixelSize(size);metrics=QtGui.QFontMetrics(font)
        if size!=self.pixel_size:self.pixel_size=size;self.setStyleSheet(f'font-size:{size}px')
        if metrics.horizontalAdvance(text)>width:
            text=metrics.elidedText(text,getattr(QtCore.Qt,'TextElideMode',QtCore.Qt).ElideRight,width)
        super().setText(text)

    def clear(self):
        self.parts=None;self.width_text="";self.reflow()


class WidthYieldDisplay:
    def __init__(self,window):
        self.w=window;self.key=None;self.pending=None;self.worker=None;self.bank=None;self.rendered=None
        self.label=YieldLabel(window.canvas.parent())
        sizes=getattr(W.QSizePolicy,'Policy',W.QSizePolicy)
        self.label.setSizePolicy(sizes.Expanding,sizes.Fixed)
        self.label.hide()
        self.timer=QtCore.QTimer(window);self.timer.setSingleShot(True);self.timer.setInterval(35)
        self.timer.timeout.connect(self.start)

    def reset(self):
        self.timer.stop();self.key=None;self.bank=None;self.pending=None;self.rendered=None
        if self.worker:self.worker.cancelled.set()
        self.label.clear();self.label.setToolTip('');self.refresh_overlay()
        if self.w.result is not None and 'width_policy_yield' in self.w.result:
            self.w.result.pop('width_policy_yield');self.w.report_dirty=True

    def refresh(self):
        w=self.w
        if w.loading or w._closing:return
        if hasattr(w,'reconstruction_yield'):w.reconstruction_yield.refresh()
        enabled=w.width_toggle.isChecked()
        self.label.set_width_active(enabled)
        if not enabled or not w.data:
            self.reset()
            if enabled:self.label.setText('宽度良率 · 等待图纸')
            return
        try:p=w.params()
        except ValueError as exc:
            self.reset();self.label.setText('宽度良率 · 工艺未就绪');self.label.setToolTip(str(exc));return
        # Both labels integrate the same bounded distribution for finite
        # k, 0 or infinity when the saved reference scale agrees.
        p=replace(p,target_nsigma=0)
        cfg=w.view_settings;roles=w.roles();order=w.ordered_layers()
        sampling=(cfg.width_yield_method,cfg.width_grid_nodes) if cfg.width_yield_method=='grid' else (cfg.width_yield_method,cfg.width_yield_exponent,cfg.width_yield_seed)
        token=('width-yield-v36',logical_fingerprint((w.data.geometry_id,w.data.top_name,roles,order,asdict(p),
               cfg.drc_tolerance_um,sampling)))
        key=(id(w.cache_group),token)
        if self.key==key:
            self.render();return
        self.reset();self.key=key
        self.pending=(key,w.data,p,cfg,roles,order,w.geometry_cache,w.cache_group)
        self.label.setText('宽度良率 · 计算中…')
        self.timer.start()

    def start(self):
        if self.worker or self.pending is None or self.w._closing:return
        from .scheduling import admission,job_budget
        active=len(self.w.preview_workers)+int(self.w.worker is not None)+int(self.w.motion_worker is not None)
        admitted=admission(active,128)
        if not admitted['start']:
            self.timer.start(250);return
        key,data,p,cfg,roles,order,cache,group=self.pending;self.pending=None
        from .gui import Worker
        from .contact import ContactModel
        from .core import build_case
        from .fast_preview import GeometryCache
        from .stack import Connections
        def work(progress):
            def calculate():
                progress(0,'准备整包宽度良率')
                geom=cache if cache is not None else GeometryCache(data)
                con=Connections(data,roles,order,geom.geometries)
                case=build_case(data,roles,connections=con)
                model=ContactModel(data,case,roles,con,geom,parameters=p)
                return measure_width_yield(model,p,cfg,group,progress,worker.publish)
            with job_budget(admitted['threads'],admitted['processes']):
                return group.values.get_or_compute(key[1],calculate)
        worker=Worker(work,self.w);self.worker=worker;self.w.watch_worker(worker,'整包宽度良率')
        def accept(bank):
            if self.key!=key or self.w._closing or bank is None:return
            self.bank=bank;self.render();self.refresh_overlay()
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        def failed(text):
            if self.key==key and not worker.cancelled.is_set():
                self.label.setText('宽度良率 · 无法计算');self.label.setToolTip(text.split('Traceback')[0].strip())
        worker.partial.connect(lambda _:accept(worker.take_partial()));worker.done.connect(accept)
        worker.failed.connect(failed);worker.finished.connect(finish);worker.start()

    def refresh_overlay(self):
        w=self.w
        if not hasattr(w,'width_overlay') or not getattr(w,'current_frame',None):return
        w.width_overlay.stamp=None;w.width_overlay.summary_stamp=None
        if w.tabs.currentIndex()==0:
            w.width_overlay.draw();w.render_scene()
        else:w._layout_pending=True

    def render(self):
        if self.bank is None:return
        ignored=self.w.ignored_width_lines
        decimals=self.w.view_settings.width_yield_decimals
        stamp=(id(self.bank),ignored,decimals)
        if self.rendered==stamp and (self.w.result is None or 'width_policy_yield' in self.w.result):return
        self.rendered=stamp
        q=policy_yields(self.bank,ignored);v=q['values']
        partial=q['samples']<q['total']
        method={'exact':'全输入几何判定','mc':'独立联合蒙特卡洛','cubature':'确定性联合求积','importance':'全范围尾部增强加权积分','grid':'全情况有限网格穷举'}[q['method']]
        labels=[name+' '+text for name,text in zip(POLICIES,policy_texts(q,decimals))]
        prefix=''
        self.label.set_values(prefix,labels,' …' if partial else '')
        tip=['全修正良率：红色、黄色问题均修正，被忽略线路保持原图；忽略线路出现问题即失效。',
             '不修正良率：所有问题均不修正，任一红色或黄色问题（含忽略线路）出现即失效。',f'{method} · {q["samples"]}/{q["total"]} 个整包姿态，按原始 RDL 和全部输入范围计算。']
        if q['intervals']:
            text=[percentage_text(lo,fhi,decimals,ROUND_FLOOR)+' – '+percentage_text(hi,flo,decimals,ROUND_CEILING)
                  for (lo,hi),(flo,fhi) in zip(q['intervals'],q['failure_intervals'])]
            tip.append('固定忽略集合下两项联合 99% 置信区间：'+'，'.join(text))
        elif q['method']=='cubature':tip.append('数值积分估计，精度可通过增加积分预算复核。')
        if q['method']=='grid':
            tip.append(f"完整有限网格：每轴 {q['grid_nodes']} 节点；局部几何求解 {q['local_checks']:,} 次。区间质量按截断正态积分，不是等概率。连续空间误差由网格分辨率决定。")
        gain=q.get('gain_probability',0.)
        tip.append(f'未忽略线路修正的额外收益：{100*gain:.{decimals}e}%。')
        if not gain and ignored:
            tip.append('当前网格下相等；连续空间差异仍受分辨率限制。' if q['method']=='grid' else '当前积分尚未分辨两项差异，不代表所有风险线路均已忽略。')
        tip.append('增加忽略集合时全修正良率单调不增；共同或包含的失效事件可能使两项提前相等。')
        tip.append('出现尚未分辨的0/100%尾部时，未获证明的数值统一用≥显示固定样本数下的99%置信下限。确定性积分未分辨尾部时显示待细化。')
        tip.append('已按全输入几何界限证明，或第一项无忽略线路的修复假设成立时，显示精确值。')
        tip.append(f'概率尺度 {self.bank["scale_nsigma"]:g}σ；0/∞ 沿用最近有限级别。')
        self.label.setToolTip('\n'.join(tip))
        if self.w.result is not None:
            self.w.result['width_policy_yield']=dict(values=v.tolist(),failure_probability=q['failure_probability'].tolist(),proven=q['proven'],failure_intervals=q['failure_intervals'],decimals=decimals,intervals=q['intervals'],method=q['method'],
                samples=q['samples'],total=q['total'],scale_nsigma=self.bank['scale_nsigma'],
                parameters=asdict(self.w.params()),ignored=[list(r) for r in sorted(ignored)],
                gain_probability=q.get('gain_probability',0.),grid_nodes=q.get('grid_nodes'),local_checks=q.get('local_checks'),steps=q.get('steps'),
                risk_lines=[dict(layer=k,index=i,**v) for (k,i),v in self.bank.get('probability_lines',{}).items()],
                probability_sites=len(self.bank.get('probability_sites',{})))
            self.w.report_dirty=True
