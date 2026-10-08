"""Pointer-centred wheel zoom and middle-button pan, following the reference UI."""
import math
import numpy as np
from matplotlib.backend_bases import MouseButton

def control_held(event):
    native=getattr(event,'guiEvent',None)
    if native is not None and hasattr(native,'modifiers'):
        from .qt_compat import Qt
        return bool(native.modifiers() & getattr(Qt,'KeyboardModifier',Qt).ControlModifier)
    modifiers=getattr(event,'modifiers',()) or ()
    return bool({'ctrl','control'}.intersection(modifiers)) or any(v in ('ctrl','control') for v in str(event.key or '').lower().split('+'))

class Navigation:
    def __init__(self,canvas,ax):
        self.canvas=canvas;self.ax=ax;self.drag=None
        self.ids=[canvas.mpl_connect(event,fn) for event,fn in
                  [('scroll_event',self.scroll),('button_press_event',self.press),
                   ('motion_notify_event',self.motion),('button_release_event',self.release),
                   ('figure_leave_event',self.release)]]
    def busy(self):
        return bool(getattr(getattr(self.canvas,'toolbar',None),'mode',''))
    def history(self):
        toolbar=getattr(self.canvas,'toolbar',None)
        if toolbar:toolbar.push_current()
    def scroll(self,e):
        if getattr(self.canvas,'die_move_active',False) and not control_held(e):return
        if self.drag or e.inaxes is not self.ax or e.xdata is None or e.ydata is None:return
        step=float(e.step)
        if not math.isfinite(step) or not step:return
        factor=1.2**(-max(-8,min(8,step)));limits=[]
        for lim,anchor in [(self.ax.get_xlim(),e.xdata),(self.ax.get_ylim(),e.ydata)]:
            new=tuple(anchor+(v-anchor)*factor for v in lim)
            if not all(map(math.isfinite,new)) or abs(new[1]-new[0])<=32*math.ulp(max(1,*map(abs,new))):return
            limits.append(new)
        self.history();self.ax.set_xlim(limits[0]);self.ax.set_ylim(limits[1]);self.history()
        if not getattr(self.canvas,'_managed_viewport',False):self.canvas.draw_idle()
    def press(self,e):
        if e.inaxes is self.ax and e.button==MouseButton.MIDDLE and not self.busy():
            self.history();self.drag=(e.x,e.y,self.ax.get_xlim(),self.ax.get_ylim(),self.ax.transData.inverted().frozen())
    def motion(self,e):
        if self.drag is None or e.x is None or e.y is None:return
        x,y,xlim,ylim,inv=self.drag;delta=inv.transform((e.x,e.y))-inv.transform((x,y))
        self.ax.set_xlim(np.array(xlim)-delta[0]);self.ax.set_ylim(np.array(ylim)-delta[1])
        if not getattr(self.canvas,'_managed_viewport',False):self.canvas.draw_idle()
    def release(self,e):
        if self.drag is not None:self.history()
        self.drag=None
