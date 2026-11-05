"""Background whole-layout calculation and lightweight reclassification of ignored lines."""
from dataclasses import asdict,replace
from decimal import ROUND_FLOOR,ROUND_CEILING
from .qt_compat import QtWidgets as W,QtCore,QtGui,ALIGN_RIGHT,ALIGN_VCENTER
from .session_cache import logical_fingerprint
from .width_yield import measure_width_yield,policy_yields,policy_texts,percentage_text

POLICIES=('原图','修黄','修红黄')


class YieldLabel(W.QLabel):
    """Keep three complete numbers readable without resizing the camera on an update."""
    def __init__(self,parent):
        super().__init__(parent)
        self.parts=None;self.pixel_size=12
        self.setAlignment(ALIGN_RIGHT|ALIGN_VCENTER);self.setContentsMargins(0,0,18,0)
        self.setStyleSheet('font-size:12px');self.ensurePolished()
        self.setFixedHeight(3*self.fontMetrics().lineSpacing()+2)

    def minimumSizeHint(self):
        return QtCore.QSize(0,self.height())

    def set_values(self,prefix,labels,suffix):
        self.parts=(prefix,labels,suffix);self.reflow()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.reflow()

    def reflow(self):
        if self.parts is None:return
        prefix,labels,suffix=self.parts
        width=max(1,self.width()-self.contentsMargins().right())
        font=QtGui.QFont(self.font());font.setPixelSize(12)
        metrics=QtGui.QFontMetrics(font)
        whole=prefix+'  ·  '.join(labels)+suffix
        text=whole if metrics.horizontalAdvance(whole)<=width else '\n'.join(labels)+suffix
        size=12
        while size>8 and max(metrics.horizontalAdvance(line) for line in text.splitlines())>width:
            size-=1;font.setPixelSize(size);metrics=QtGui.QFontMetrics(font)
        if size!=self.pixel_size:self.pixel_size=size;self.setStyleSheet(f'font-size:{size}px')
        super().setText(text)

    def clear(self):
        self.parts=None;super().clear()


class WidthYieldDisplay:
    def __init__(self,window):
        self.w=window;self.key=None;self.pending=None;self.worker=None;self.bank=None;self.rendered=None
        self.label=YieldLabel(window.toolbar)
        sizes=getattr(W.QSizePolicy,'Policy',W.QSizePolicy)
        self.label.setSizePolicy(sizes.Expanding,sizes.Fixed)
        toolbar=window.toolbar;loc=toolbar.locLabel
        self.original_policy=loc.sizePolicy()
        coordinate_action=next(a for a in toolbar.actions() if toolbar.widgetForAction(a) is loc)
        self.action=toolbar.insertWidget(coordinate_action,self.label);self.action.setVisible(False)
        self.timer=QtCore.QTimer(window);self.timer.setSingleShot(True);self.timer.setInterval(35)
        self.timer.timeout.connect(self.start)

    def reset(self):
        self.timer.stop();self.key=None;self.bank=None;self.pending=None;self.rendered=None
        if self.worker:self.worker.cancelled.set()
        self.label.clear();self.label.setToolTip('')
        if self.w.result is not None and 'width_policy_yield' in self.w.result:
            self.w.result.pop('width_policy_yield');self.w.report_dirty=True

    def refresh(self):
        w=self.w
        if w.loading or w._closing:return
        enabled=w.width_toggle.isChecked()
        if self.action.isVisible()!=enabled:
            sizes=getattr(W.QSizePolicy,'Policy',W.QSizePolicy)
            if enabled:
                w.toolbar.locLabel.setSizePolicy(sizes.Preferred,sizes.Preferred)
                w.toolbar.locLabel.setMinimumWidth(w.toolbar.locLabel.fontMetrics().horizontalAdvance('x=−0000.00 y=−0000.00'))
            else:
                w.toolbar.locLabel.setSizePolicy(self.original_policy)
                w.toolbar.locLabel.setMinimumWidth(0)
            self.action.setVisible(enabled)
        if not enabled or not w.data:
            self.reset()
            if enabled:self.label.setText('宽度良率 · 等待图纸')
            return
        try:p=w.params()
        except ValueError as exc:
            self.reset();self.label.setText('宽度良率 · 工艺未就绪');self.label.setToolTip(str(exc));return
        # All three labels integrate the same bounded distribution for finite
        # k, 0 or infinity when the saved reference scale agrees.
        p=replace(p,target_nsigma=0)
        cfg=w.view_settings;roles=w.roles();order=w.ordered_layers()
        sampling=(cfg.yield_method,cfg.yield_box_budget) if cfg.yield_method=='partition' else (cfg.yield_method,cfg.yield_sample_exponent,cfg.random_seed)
        token=('width-yield-v33',logical_fingerprint((w.data.geometry_id,w.data.top_name,roles,order,asdict(p),
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
            self.bank=bank;self.render()
        def finish():
            self.worker=None;worker.deleteLater()
            if self.pending and not self.w._closing:self.timer.start()
        def failed(text):
            if self.key==key and not worker.cancelled.is_set():
                self.label.setText('宽度良率 · 无法计算');self.label.setToolTip(text.split('Traceback')[0].strip())
        worker.partial.connect(lambda _:accept(worker.take_partial()));worker.done.connect(accept)
        worker.failed.connect(failed);worker.finished.connect(finish);worker.start()

    def render(self):
        if self.bank is None:return
        ignored=self.w.ignored_width_lines
        decimals=self.w.view_settings.width_yield_decimals
        stamp=(id(self.bank),ignored,decimals)
        if self.rendered==stamp and (self.w.result is None or 'width_policy_yield' in self.w.result):return
        self.rendered=stamp
        q=policy_yields(self.bank,ignored);v=q['values']
        partial=q['samples']<q['total']
        method={'exact':'全输入几何判定','mc':'独立联合蒙特卡洛','cubature':'确定性联合求积'}[q['method']]
        labels=[name+' '+text for name,text in zip(POLICIES,policy_texts(q,decimals))]
        prefix='宽度良率（99%下限）  ' if any('≥' in t for t in labels) else '宽度良率  '
        self.label.set_values(prefix,labels,' …' if partial else '')
        tip=['原图：红、黄或被忽略线路出现问题即失效。','修黄：红色或被忽略线路出现问题即失效。',
             '修红黄：被忽略线路出现问题即失效。',f'{method} · {q["samples"]}/{q["total"]} 个整包姿态，按原始 RDL 和全部输入范围计算。']
        if q['intervals']:
            text=[percentage_text(lo,fhi,decimals,ROUND_FLOOR)+' – '+percentage_text(hi,flo,decimals,ROUND_CEILING)
                  for (lo,hi),(flo,fhi) in zip(q['intervals'],q['failure_intervals'])]
            tip.append('固定忽略集合下三项联合 99% 置信区间：'+'，'.join(text))
        elif q['method']=='cubature':tip.append('数值积分估计，精度可通过增加积分预算复核。')
        tip.append('出现尚未分辨的0/100%尾部时，未获证明的数值统一用≥显示固定样本数下的99%置信下限。确定性积分未分辨尾部时显示待细化。')
        tip.append('已按全输入几何界限证明，或第三项无忽略线路的修复假设成立时，显示精确值。')
        tip.append(f'概率尺度 {self.bank["scale_nsigma"]:g}σ；0/∞ 沿用最近有限级别。')
        self.label.setToolTip('\n'.join(tip))
        if self.w.result is not None:
            self.w.result['width_policy_yield']=dict(values=v.tolist(),failure_probability=q['failure_probability'].tolist(),proven=q['proven'],failure_intervals=q['failure_intervals'],decimals=decimals,intervals=q['intervals'],method=q['method'],
                samples=q['samples'],total=q['total'],scale_nsigma=self.bank['scale_nsigma'],
                parameters=asdict(self.w.params()),ignored=[list(r) for r in sorted(ignored)])
            self.w.report_dirty=True
