"""Engineering rulers are view annotations, never fabrication geometry."""
import numpy as np
from matplotlib.patches import FancyArrowPatch
from .qt_compat import QtGui,QtWidgets as W,Qt


from .measurement_data import normalize


class Rulers:
    def __init__(self,w):
        self.w=w;self.rows=[];self.artists=[];self.active=False;self.start=None;self.current=None;self.pixel=None
        shortcut=QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut
        self.shortcuts=[]
        for key,fn in [('K',self.enter),('Shift+K',self.clear),('Ctrl+K',self.clear)]:
            action=shortcut(QtGui.QKeySequence(key),w.canvas);action.setContext(getattr(Qt,'ShortcutContext',Qt).WidgetWithChildrenShortcut);action.activated.connect(fn);self.shortcuts.append(action)
        for name,fn in [('button_press_event',self.press),('motion_notify_event',self.move),('button_release_event',self.release)]:w.canvas.mpl_connect(name,fn)
    def enter(self):
        self.w.cut_drag.cancel();self.w.exit_region_mode();self.w.mouse_die.exit()
        if self.w.toolbar.mode:
            (self.w.toolbar.pan if 'pan' in str(self.w.toolbar.mode).lower() else self.w.toolbar.zoom)()
        self.active=True;self.w.canvas.ruler_active=True
        self.w.statusBar().showMessage('测量：左键拖动或依次点击两个端点；Esc退出，Shift+K / Ctrl+K清空')
    def exit(self):
        was=self.active;self.active=False;self.w.canvas.ruler_active=False;self.start=self.current=None;self.draw();return was
    def clear(self):self.rows=[];self.start=self.current=None;self.draw()
    def restore(self,rows):self.exit();self.rows=normalize(rows);self.draw()
    def point(self,e):return np.array([e.xdata,e.ydata]) if e.inaxes is self.w.ax and e.xdata is not None and e.ydata is not None else None
    def press(self,e):
        if not self.active or e.button!=1:return
        point=self.point(e)
        if point is None:return
        if self.start is not None:
            self.finish(point);return
        self.start=point;self.current=point;self.pixel=np.array([e.x,e.y]);self.draw()
    def move(self,e):
        if self.active and self.start is not None and self.point(e) is not None:self.current=self.point(e);self.draw()
    def release(self,e):
        if self.active and e.button==1 and self.start is not None and self.point(e) is not None and np.linalg.norm(np.array([e.x,e.y])-self.pixel)>3:self.finish(self.point(e))
    def finish(self,point):
        if np.linalg.norm(point-self.start)>1e-12:self.rows.append([self.start.tolist(),point.tolist()])
        self.start=self.current=None;self.draw()
    def draw(self,redraw=True):
        for artist in self.artists:
            try:artist.remove()
            except (ValueError,NotImplementedError):pass
        self.artists=[]
        rows=[*self.rows,*([[self.start,self.current]] if self.start is not None and self.current is not None else [])]
        for a,b in rows:
            a,b=np.asarray(a),np.asarray(b);length=float(np.linalg.norm(b-a))
            line=FancyArrowPatch(a,b,arrowstyle='<->',color='black',lw=.9,mutation_scale=9,shrinkA=0,shrinkB=0,zorder=30)
            self.w.ax.add_patch(line);self.artists.append(line)
            self.artists.append(self.w.ax.annotate(f'{length:.3f} μm',(a+b)/2,xytext=(0,6),textcoords='offset points',ha='center',va='bottom',color='black',fontsize=8,zorder=31,clip_on=True))
        if redraw:
            if hasattr(self.w,'scene_renderer'):self.w.scene_renderer.external_draw(None)
            self.w.canvas.draw_idle()
