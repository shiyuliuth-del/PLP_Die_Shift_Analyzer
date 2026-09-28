"""Same-host baseline comparison: both overlays, full/zoomed view, large drawing."""
import os,sys,argparse,time,json,tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
a=argparse.ArgumentParser();a.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));a.add_argument('--output',required=True);args=a.parse_args();ROOT=Path(args.root).resolve();sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import GDSData
from die_shift_studio import __version__
app=W.QApplication([]);temp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(temp.name)/'Jobs');w.show();errors=[];w.error=lambda x:errors.append(str(x))
def wait():
 end=time.monotonic()+150
 while time.monotonic()<end:
  app.processEvents()
  if not(w.worker or w.preview_worker or w.preview_pending):break
  time.sleep(.002)
 assert not(w.worker or w.preview_worker or w.preview_pending);assert not errors,errors
 app.processEvents()
def view(center,size):
 w.set_view(center,size,size*.6)
 if hasattr(w,'viewport_changed'):w.viewport_changed()
 w.canvas.draw();app.processEvents()
def measure(states):
 draws=[];computes=[]
 for z in states:
  t=time.perf_counter();frame=w.engine.frame(z);computes.append((time.perf_counter()-t)*1000)
  w.current_frame=frame;w._in_animation=True;t=time.perf_counter();w.plot_layout(preserve=True);draws.append((time.perf_counter()-t)*1000);w._in_animation=False
 return dict(draw_ms=draws,draw_median_ms=float(np.median(draws[2:])),compute_ms=computes,compute_median_ms=float(np.median(computes[2:])),visible_paths=sum(len(a.get_paths()) for a in w.layer_artists.values() if a.get_visible()),visible_probability_artists=sum(a.get_visible() for a in w.probability_artists))
project=dict(schema_version=5,gds_path='/home/niubi/下载/GD55_9-21.gds',roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'},layer_order=['6/0','5/0','4/0','3/0'],parameters={})
w.load_gds(project['gds_path'],project=project);wait();w.run_evaluation();wait();w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait()
zlist=[np.tile([x,-x,x/3500],(3,1)) for x in [0,35,-35,20,-20,5,-5,10,-10,0]]
record=dict(version=__version__,process_cpu_affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,host_load_average=os.getloadavg() if hasattr(os,'getloadavg') else None,gd55_sha256=w.data.sha256,interfaces=len(w.engine.contact.records))
record['full']=measure(zlist)
record['full']['geometry_count']=w.data.count
center=w.engine.contact.records[0]['center'];view(center,500);record['zoom']=measure(zlist)
if __version__=='V10':
 # Preview is measured separately; neither its search nor its validation is hidden in FPS.
 w.probability_toggle.setChecked(False);w.space_toggle.setChecked(False);wait();w.deformation_toggle.setChecked(True);wait();t=time.perf_counter();f=w.engine.frame(zlist[1]);elapsed=(time.perf_counter()-t)*1000
 record['gd55_deformation']=dict(ms=elapsed,accepted=f['deformation']['accepted_count'],diagnostics=f['deformation']['diagnostics'],blocked=f['deformation']['blocked_count'])
 w.current_frame=f;w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw();w.grab().save(str(Path(args.output).parent.parent/'figures/software_V10_GD55.png'))
 w.deformation_toggle.setChecked(False);wait()
w.probability_toggle.setChecked(False);w.space_toggle.setChecked(False);wait()
# Huge but simple GDS-like geometry isolates offscreen draw cost.
a=np.array([[0,0],[20,0],[20,20],[0,20]],float);xx,yy=np.meshgrid(np.arange(400)*100.,np.arange(250)*100.);origins=np.column_stack((xx.ravel(),yy.ravel()));arrays=a[None,:,:]+origins[:,None,:]
rect=lambda x0,y0,x1,y1:np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
roles={'127/0':'封装边框','128/0':'芯片边框','5/0':'固定RDL'}
data=GDSData('synthetic_100000.gds','synthetic','TOP',['TOP'],.001,{'127/0':[rect(-100,-100,40100,25100)],'128/0':[rect(0,0,1000,1000)],'5/0':list(arrays)},roles)
w.set_data(data,dict(schema_version=5,roles=roles,layer_order=['5/0','128/0','127/0']));view([20000,12000],1200)
times=[]
for dx in range(8):
 t=time.perf_counter();w.set_view([20000+dx*100,12000],1200,720);w.plot_layout(preserve=True);times.append((time.perf_counter()-t)*1000)
record['large']=dict(total=100000,visible_paths=sum(len(x.get_paths()) for x in w.layer_artists.values() if x.get_visible()),pan_ms=times,pan_median_ms=float(np.median(times[2:])))
Path(args.output).write_text(json.dumps(record,ensure_ascii=False,indent=2,default=lambda x:x.item())+'\n');w.close();app.processEvents();temp.cleanup();print(json.dumps(record,ensure_ascii=False,indent=2,default=lambda x:x.item()))
