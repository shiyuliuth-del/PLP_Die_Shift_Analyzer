"""Canvas-only F/M shortcuts and coalesced, asynchronous direct DIE motion."""
import math
import numpy as np
from .qt_compat import QtCore,Qt,QtWidgets as W

class MouseDieMode:
    def __init__(self,window):
        self.w=window;self.owner=None;self.drag=None;self.pending=False;self.full_pending=False
        self.idle=QtCore.QTimer(window);self.idle.setSingleShot(True);self.idle.setInterval(180);self.idle.timeout.connect(self.refine)
        self.timer=QtCore.QTimer(window);self.timer.setInterval(16);self.timer.timeout.connect(self.flush)
        for name,fn in [('key_press_event',self.key),('button_press_event',self.press),
                        ('motion_notify_event',self.move),('button_release_event',self.release),('scroll_event',self.scroll)]:
            window.canvas.mpl_connect(name,fn)
        window.canvas.setFocusPolicy(getattr(Qt,'FocusPolicy',Qt).StrongFocus)
    def key(self,event):
        if event.key=='f':self.w.stop_animation();self.w.fit_plot();self.w.plot_layout(preserve=True)
        elif event.key=='m':self.choose()
    def choose(self):
        w=self.w
        if not w.case:return
        names=[f'DIE {i+1}' for i in range(len(w.case['dies']))]
        value,ok=W.QInputDialog.getItem(w,'交互偏移','选择芯片；右键拖动平移，滚轮每格旋转 0.1°，Ctrl+滚轮缩放，ESC退出：',names,max(0,self.owner or 0),False)
        if ok:self.enter(names.index(value))
        w.canvas.setFocus()
    def enter(self,owner):
        w=self.w;self.cancel_pending();w.stop_animation();self.owner=owner;self.drag=None
        w.preview_ticket+=1;w.preview_pending=None
        if w.preview_worker is not None:w.preview_worker.cancelled.set()
        w.canvas.die_move_active=True
        states=np.zeros((len(w.case['dies']),3)) if w.shown_states is None else w.shown_states.copy()
        w.manual_states=states;w.active_preview=None;w.manual_die.setCurrentIndex(owner)
        w.extreme_table.blockSignals(True);w.extreme_table.clearSelection();w.extreme_table.blockSignals(False)
        self.update_text();w.render_scene();w.statusBar().showMessage(f'DIE {owner+1} 交互偏移：右键平移；滚轮上/下逆时针/顺时针旋转；Ctrl+滚轮缩放；ESC退出')
    def exit(self):
        active=self.owner is not None;self.owner=None;self.drag=None;self.w.canvas.die_move_active=False
        self.update_text();self.w.render_scene()
        return active
    def cancel_pending(self):
        self.pending=False;self.full_pending=False;self.timer.stop();self.idle.stop();self.drag=None
    def press(self,event):
        w=self.w
        if event.inaxes is w.ax:w.canvas.setFocus()
        if self.owner is not None and event.button==3 and event.inaxes is w.ax:
            self.drag=(np.array([event.x,event.y]),w.manual_states.copy(),w.ax.transData.inverted().frozen())
    def move(self,event):
        if self.drag is None or self.owner is None or event.x is None or event.y is None:return
        pixel,states,inv=self.drag;pose=states.copy()
        pose[self.owner,:2]+=inv.transform((event.x,event.y))-inv.transform(pixel)
        # A wheel rotation during the drag must not be lost at the next move.
        pose[self.owner,2]=self.w.manual_states[self.owner,2]
        self.submit(pose)
    def release(self,event):
        if event.button==3 and self.owner is not None and self.drag is not None:
            self.drag=None;self.idle.start();self.flush()
    def scroll(self,event):
        from .navigation import control_held
        if control_held(event):return
        if self.owner is None or event.inaxes is not self.w.ax:return
        pose=self.w.manual_states.copy();pose[self.owner,2]+=math.radians(.1)*float(event.step)
        self.submit(pose)
    def submit(self,states):
        self.w.manual_states=states;self.w.active_preview=None;self.pending=True;self.full_pending=False;self.idle.start()
        if self.w.preview_worker is not None and getattr(self.w.preview_worker,'interactive_refinement',False):self.w.preview_worker.cancelled.set()
        if not self.timer.isActive():self.timer.start()
    def refine(self):
        self.full_pending=True
        if not self.timer.isActive():self.timer.start()
    def flush(self):
        w=self.w
        if not self.pending and not self.full_pending:self.timer.stop();return
        # Do not cancel a frame for every pointer event: that starves completion.
        # At most one worker and one latest requested pose are retained.
        if w.preview_worker is not None or w.motion_worker is not None or w.worker is not None:return
        settle=not self.pending and self.full_pending
        self.pending=False
        if settle:self.full_pending=False
        w.request_preview(direct=True,settle=settle)
    def update_text(self):
        w=self.w
        if not hasattr(self,'text'):
            self.text=W.QLabel(w.canvas);self.text.setStyleSheet('color:#3b5773;background:rgba(255,255,255,215);font-size:11px;padding:1px 3px;')
            self.text.setAttribute(getattr(Qt,'WidgetAttribute',Qt).WA_TransparentForMouseEvents)
        self.text.setVisible(self.owner is not None)
        if self.owner is not None:
            z=w.shown_states[self.owner] if w.shown_states is not None else np.zeros(3)
            self.text.setText(f'DIE {self.owner+1}   dx = {z[0]:.3f} μm   dy = {z[1]:.3f} μm   O = {math.degrees(z[2]):.4f}°')
            for name,value in zip(('dx','dy','angle'),(z[0],z[1],math.degrees(z[2]))):
                control=w.manual_fields[name];control.blockSignals(True);control.setValue(float(value));control.blockSignals(False)
            self.text.adjustSize();self.text.move(12,max(0,w.canvas.height()-self.text.height()-2));self.text.raise_()
