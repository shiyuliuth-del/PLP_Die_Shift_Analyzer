"""Qt regression: a cut line can be dragged, re-dragged, clamped and reset."""
import os,sys,time,tempfile
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1500,1000);w.show()
errs=[];w.error=lambda e:errs.append(str(e))
def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_pending,w.motion_worker,w.coverage_timer.isActive(),w.viewport_timer.isActive(),w.transition.timer.isActive())):break
        time.sleep(.004)
    else:raise AssertionError('timeout')
    assert not errs,errs
def ev(x,y,button=1,pixels=(700,420)):
    return SimpleNamespace(button=button,dblclick=False,inaxes=w.ax,xdata=float(x),ydata=float(y),x=pixels[0],y=pixels[1])
def plane(piece):
    parts=piece['cut'].geoms if hasattr(piece['cut'],'geoms') else [piece['cut']]
    line=max(parts,key=lambda q:q.length);a=np.asarray(line.coords[0],float);b=np.asarray(line.coords[-1],float)
    d=b-a;n=np.array([-d[1],d[0]],float);n/=np.linalg.norm(n);mid=(a+b)/2.
    fixed=np.asarray(piece['immovable'].centroid.coords[0],float)
    if float(np.dot(n,fixed-mid))<0.:n=-n
    return n,mid
def drag(mid,n,step):
    end=np.asarray(mid,float)+np.asarray(n,float)*float(step)
    w.cut_drag.press(ev(mid[0],mid[1]))
    w.cut_drag.move(ev(end[0],end[1],pixels=(730,440)))
    w.cut_drag.release(ev(end[0],end[1],pixels=(730,440)));wait()
w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait();w.run_evaluation();wait()
w.view_settings=replace(w.view_settings,reconstruction_length_um=0.)
w.reconstruction_toggle.setChecked(True);w.manual_states=np.array([[35.,0.,0.],[0.,0.,0.]]);w.request_preview();wait()
p0=w.current_frame['reconstruction']['pieces'][0];n,mid=plane(p0)
drag(mid,n,10.)
p1=w.current_frame['reconstruction']['pieces'][0]
assert p1['manual'] and abs(p1['cut_distance_um']-(p0['cut_distance_um']+10.))<1e-6,(p1['cut_distance_um'],p0['cut_distance_um'])
assert w.current_frame['reconstruction']['summary']['manual_cuts']==1 and len(w.view_settings.reconstruction_cuts)==1
print('drag ok',round(p1['cut_distance_um'],3))
n,mid=plane(p1);drag(mid,n,10.)
p2=w.current_frame['reconstruction']['pieces'][0]
assert p2['manual'] and abs(p2['cut_distance_um']-(p1['cut_distance_um']+10.))<1e-6,p2['cut_distance_um']
print('redrag ok',round(p2['cut_distance_um'],3))
win=p2['cut_window'];n,mid=plane(p2)
along=float(np.dot(np.asarray(mid,float)-np.asarray(win['pivot'],float),np.asarray(win['normal'],float)))
drag(mid,n,float(win['high'])+600.-along)
p3=w.current_frame['reconstruction']['pieces'][0]
assert p3['manual'] and p3['cut_distance_um']<=float(win['high'])-float(win['low'])+1e-3,(p3['cut_distance_um'],win)
print('clamp ok',round(p3['cut_distance_um'],3),'window',round(float(win['low']),3),round(float(win['high']),3))
n,mid=plane(p3)
w.cut_drag.press(ev(mid[0],mid[1],button=3));wait()
assert not w.view_settings.reconstruction_cuts and not w.current_frame['reconstruction']['pieces'][0]['manual']
print('reset ok')
print('GUI_RECON_DRAG_PASS: drag, re-drag, clamp at the copper end, right-click reset')
