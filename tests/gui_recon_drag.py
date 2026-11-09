"""Qt regression: a cut line can be dragged, re-dragged, clamped and reset."""
import os,sys,time,tempfile
from pathlib import Path
from matplotlib.backend_bases import MouseEvent
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
def event(kind,x,y,button=1,dblclick=False):
    px,py=w.ax.transData.transform((x,y))
    w.canvas.callbacks.process(kind,MouseEvent(kind,w.canvas,px,py,button=button,dblclick=dblclick))
def plane(piece):
    parts=piece['cut'].geoms if hasattr(piece['cut'],'geoms') else [piece['cut']]
    line=max(parts,key=lambda q:q.length);a=np.asarray(line.coords[0],float);b=np.asarray(line.coords[-1],float)
    d=b-a;n=np.array([-d[1],d[0]],float);n/=np.linalg.norm(n);mid=(a+b)/2.
    fixed=np.asarray(piece['immovable'].centroid.coords[0],float)
    if float(np.dot(n,fixed-mid))<0.:n=-n
    return n,mid
def drag(mid,n,step):
    end=np.asarray(mid,float)+np.asarray(n,float)*float(step)
    event('button_press_event',*mid)
    event('motion_notify_event',*end)
    event('button_release_event',*end);wait()
w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait();w.run_evaluation();wait()
w.view_settings=replace(w.view_settings,reconstruction_length_um=0.,animation_enabled=False)
w.reconstruction_toggle.setChecked(True);w.manual_states=np.array([[35.,0.,0.],[0.,0.,0.]]);w.request_preview();wait()
p0=w.current_frame['reconstruction']['pieces'][0];n,mid=plane(p0)
w.ax.set_xlim(mid[0]-100,mid[0]+100);w.ax.set_ylim(mid[1]-100,mid[1]+100);w.canvas.draw()
drag(mid,n,10.)
p1=w.current_frame['reconstruction']['pieces'][0]
assert p1['manual'] and abs(p1['cut_distance_um']-(p0['cut_distance_um']+10.))<1e-6,(p1['cut_distance_um'],p0['cut_distance_um'])
assert w.current_frame['reconstruction']['summary']['manual_cuts']==1 and len(w.view_settings.reconstruction_cuts)==1
print('drag ok',round(p1['cut_distance_um'],3))
n,mid=plane(p1);drag(mid,n,10.)
p2=w.current_frame['reconstruction']['pieces'][0]
assert p2['manual'] and abs(p2['cut_distance_um']-(p1['cut_distance_um']+10.))<1e-6,p2['cut_distance_um']
print('redrag ok',round(p2['cut_distance_um'],3))
from die_shift_studio.settings import ViewSettings
from dataclasses import asdict
import json
assert ViewSettings.from_dict(json.loads(json.dumps(asdict(w.view_settings)))).reconstruction_cuts==w.view_settings.reconstruction_cuts
win=p2['cut_window'];n,mid=plane(p2)
along=float(np.dot(np.asarray(mid,float)-np.asarray(win['pivot'],float),np.asarray(win['normal'],float)))
w.ax.set_xlim(mid[0]-2300,mid[0]+2300);w.ax.set_ylim(mid[1]-2300,mid[1]+2300);w.canvas.draw()
drag(mid,n,float(win['high'])+600.-along)
p3=w.current_frame['reconstruction']['pieces'][0]
assert p3['manual'] and p3['cut_distance_um']<=float(win['high'])-float(win['low'])+1e-3,(p3['cut_distance_um'],win)
print('clamp ok',round(p3['cut_distance_um'],3),'window',round(float(win['low']),3),round(float(win['high']),3))
n,mid=plane(p3)
event('button_press_event',*mid,button=3);event('button_release_event',*mid,button=3);wait()
assert w.navigation.zoom is None
assert not w.view_settings.reconstruction_cuts and not w.current_frame['reconstruction']['pieces'][0]['manual']
print('reset ok')
# Persist a real engineering project, reopen it, and verify the edited plane.
p=w.current_frame['reconstruction']['pieces'][0];n,mid=plane(p)
w.ax.set_xlim(mid[0]-100,mid[0]+100);w.ax.set_ylim(mid[1]-100,mid[1]+100);w.canvas.draw()
drag(mid,n,8.)
saved=w.view_settings.reconstruction_cuts
project=Path(tmp.name)/'saved-cuts.dshift.json';w.write_project(project)
w.load_project(project);wait()
assert w.view_settings.reconstruction_cuts==saved
w.run_evaluation();wait();w.manual_states=np.array([[35.,0.,0.],[0.,0.,0.]]);w.request_preview();wait()
p=w.current_frame['reconstruction']['pieces'][0]
assert p['manual']
n,mid=plane(p);w.ax.set_xlim(mid[0]-100,mid[0]+100);w.ax.set_ylim(mid[1]-100,mid[1]+100);w.canvas.draw()
event('button_press_event',*mid,dblclick=True);event('button_release_event',*mid);wait()
assert not w.view_settings.reconstruction_cuts
p=w.current_frame['reconstruction']['pieces'][0];n,mid=plane(p);drag(mid,n,8.)
assert w.view_settings.reconstruction_cuts
button=next(b for b in w.findChildren(W.QPushButton) if b.text()=='清除全部手动切口')
button.click();wait()
assert not w.view_settings.reconstruction_cuts
assert not any(p['manual'] for p in w.current_frame['reconstruction']['pieces'])
print('GUI_RECON_DRAG_PASS: drag, re-drag, clamp, right-click/double-click reset, save/reopen, clear all')

w.close();app.processEvents();tmp.cleanup()
