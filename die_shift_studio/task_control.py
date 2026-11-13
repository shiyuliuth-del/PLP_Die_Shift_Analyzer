"""Cooperative cancel, with an immutable last-visible-state checkpoint."""
import numpy as np
from .qt_compat import QtCore,QtWidgets as W


class TaskControl(QtCore.QObject):
    def __init__(self,w,header):
        super().__init__(w);self.w=w;self.snapshot=None;self.report_cancelled=False
        self.button=W.QPushButton('取消');self.button.setMaximumHeight(23)
        policy=self.button.sizePolicy();policy.setRetainSizeWhenHidden(True);self.button.setSizePolicy(policy)
        header.addWidget(self.button);self.button.hide();self.button.clicked.connect(self.cancel)
        w.progress.installEventFilter(self)

    def eventFilter(self,obj,event):
        types=getattr(QtCore.QEvent,'Type',QtCore.QEvent)
        if event.type() in (types.Show,types.ShowToParent):self.button.show()
        elif event.type() in (types.Hide,types.HideToParent):self.button.hide()
        return False

    def checkpoint(self):
        w=self.w
        if w._tasks:return
        self.snapshot=(w.data,w.current_frame,w.result,
                       None if w.shown_states is None else w.shown_states.copy(),
                       w.probability_data,w.space_data,w.footprint_data,w.repair_data,w.engine,w.case,w.connections)
        self.report_cancelled=False

    def cancel(self):
        w=self.w;self.report_cancelled=True
        w.stop_animation();w.preview_ticket+=1;w.motion_generation+=1
        w.preview_pending=None;w.motion_pending=None;w._prepared_motion=None;w._prepared_package=None
        w._coverage_pending=False;w.coverage_timer.stop();w._queued_node_edit=None
        if getattr(w,'cut_drag',None):w.cut_drag.cancel()
        for controller in (w.width_yield,w.reconstruction_yield,w.constraint_feedback):
            controller.timer.stop();controller.pending=None
            if hasattr(controller,'ticket'):controller.ticket+=1
        from .gui import Worker
        for worker in w.findChildren(Worker):
            worker.cancelled.set()
        if self.snapshot and self.snapshot[0] is w.data:
            _,frame,result,shown,probability,space,footprints,repair,engine,case,connections=self.snapshot
            w.current_frame=frame;w.result=result;w.shown_states=shown;w.engine=engine;w.case=case;w.connections=connections
            w.probability_data=probability;w.space_data=space;w.footprint_data=footprints;w.repair_data=repair
            w.manual_states=shown.copy() if shown is not None and np.any(shown) else None
            w.active_preview=None;w._interpolated_states=None
            w.extreme_table.blockSignals(True);w.extreme_table.setCurrentCell(-1 if w.manual_states is not None else 0,0);w.extreme_table.blockSignals(False)
            w.plot_layout(preserve=True);w.update_histograms()
            if shown is not None:w.update_state_table(shown)
        self.button.setEnabled(False)
        w.log_bus.emit('已请求取消计算，保留计算前已显示结果；原生几何调用将在安全返回点退出。')
        w.statusBar().showMessage('正在取消…保留上次完成结果')

    def finished(self):
        if not self.w._tasks:self.button.setEnabled(True)
