"""Pointer-centred wheel zoom and middle-button pan, following the reference UI."""
import math
import numpy as np
from matplotlib.backend_bases import MouseButton
from matplotlib.patches import Rectangle

def pointer_modifiers(event):
    native=getattr(event,'guiEvent',None)
    if native is not None and hasattr(native,'modifiers'):
        from .qt_compat import Qt
        flags=native.modifiers();keys=getattr(Qt,'KeyboardModifier',Qt)
        return {name for name,mask in [('ctrl',keys.ControlModifier),('shift',keys.ShiftModifier)] if flags & mask}
    modifiers=set(getattr(event,'modifiers',()) or ())
    modifiers.update(str(getattr(event,'key',None) or '').lower().split('+'))
    if 'control' in modifiers:modifiers.add('ctrl')
    return modifiers

def control_held(event):
    return 'ctrl' in pointer_modifiers(event)

class Navigation:
    def __init__(self,canvas,ax,on_context=None):
        self.canvas=canvas;self.ax=ax;self.on_context=on_context;self.drag=None;self.zoom=None;self.rubber=None;self.background=None
        self.ids=[canvas.mpl_connect(event,fn) for event,fn in
                  [('scroll_event',self.scroll),('button_press_event',self.press),
                   ('motion_notify_event',self.motion),('button_release_event',self.release),
                   ('figure_leave_event',self.release),('resize_event',lambda _:self.cancel_zoom())]]
    def busy(self):
        return bool(getattr(getattr(self.canvas,'toolbar',None),'mode',''))
    def history(self):
        toolbar=getattr(self.canvas,'toolbar',None)
        if toolbar:toolbar.push_current()
    def scroll(self,e):
        if getattr(self.canvas,'die_move_active',False) and not control_held(e):return
        if self.drag or self.zoom is not None or e.inaxes is not self.ax or e.xdata is None or e.ydata is None:return
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
        if e.inaxes is not self.ax or self.busy():return
        if e.button==MouseButton.MIDDLE:
            self.history();self.drag=(e.x,e.y,self.ax.get_xlim(),self.ax.get_ylim(),self.ax.transData.inverted().frozen())
        elif e.button==MouseButton.RIGHT and not getattr(self.canvas,'die_move_active',False):
            self.cancel_zoom()
            self.zoom=(np.array([e.x,e.y],float),self.ax.transData.inverted().frozen())

    def cancel_zoom(self):
        self.zoom=None
        if self.rubber is not None:
            self.rubber.remove();self.rubber=None
            if self.background is not None:
                self.canvas.restore_region(self.background);self.canvas.blit(self.ax.bbox)
        self.background=None

    def motion(self,e):
        if self.zoom is not None and e.x is not None and e.y is not None:
            start,inv=self.zoom
            end=np.clip([e.x,e.y],self.ax.bbox.min,self.ax.bbox.max)
            if self.rubber is None and np.linalg.norm(end-start)>=4:
                self.background=self.canvas.copy_from_bbox(self.ax.bbox)
                self.rubber=Rectangle((0,0),0,0,animated=True,facecolor=(.35,.58,.75,.12),edgecolor='#466b89',ls='--',lw=1.2,zorder=32)
                self.ax.add_patch(self.rubber)
            if self.rubber is not None:
                a,b=inv.transform([start,end]);self.rubber.set_bounds(*np.minimum(a,b),*np.abs(b-a))
                self.canvas.restore_region(self.background);self.ax.draw_artist(self.rubber);self.canvas.blit(self.ax.bbox)
            return
        if self.drag is None or e.x is None or e.y is None:return
        x,y,xlim,ylim,inv=self.drag;delta=inv.transform((e.x,e.y))-inv.transform((x,y))
        self.ax.set_xlim(np.array(xlim)-delta[0]);self.ax.set_ylim(np.array(ylim)-delta[1])
        if not getattr(self.canvas,'_managed_viewport',False):self.canvas.draw_idle()
    def release(self,e):
        if self.zoom is not None:
            pixel,inv=self.zoom
            if (getattr(e,'name','')=='figure_leave_event' or e.button!=MouseButton.RIGHT or e.x is None or e.y is None
                    or getattr(self.canvas,'die_move_active',False)):
                self.cancel_zoom();return
            end=np.clip([e.x,e.y],self.ax.bbox.min,self.ax.bbox.max)
            dragged=self.rubber is not None
            self.cancel_zoom();a,b=inv.transform([pixel,end])
            if np.linalg.norm(end-pixel)<4:
                if not dragged and self.on_context and e.inaxes is self.ax:self.on_context(e,b)
                return
            center=(a+b)/2
            ratio=self.ax.bbox.width/max(1.,self.ax.bbox.height)
            size=np.abs(b-a);w=max(size[0],size[1]*ratio);size=np.array([w,w/ratio])
            self.history()
            self.ax.set_xlim(center[0]+np.array([-.5,.5])*size[0]);self.ax.set_ylim(center[1]+np.array([-.5,.5])*size[1]);self.history()
            if not getattr(self.canvas,'_managed_viewport',False):self.canvas.draw_idle()
            return
        if self.drag is not None:self.history()
        self.drag=None
