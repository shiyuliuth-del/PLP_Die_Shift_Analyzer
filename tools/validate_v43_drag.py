"""Actual nu17123 source, Qt/Matplotlib handle events near (7379,2933)."""
import argparse,json,os,sys,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.gui import MainWindow,W

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--project',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
 job=normalize_project(json.loads(args.project.read_text()));source=Path(job['gds_path']);source=source if source.is_absolute() else args.project.parent/source
 d=read_gds(source,job['top_cell']);d,job=restore_project_geometry(d,job);c=build_case(d,job['roles'],layer_order=job['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(job['parameters'])
 e=PreviewEngine(cache,c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings.from_dict(job['settings']));r=e.reconstructor;target=shapely.Point(7379,2933)
 refs=sorted(r.vias_by_target,key=lambda ref:cache.layers[ref[0]]['geometry'][ref[1]].distance(target))[:2]
 r.vias_by_target={ref:r.vias_by_target[ref] for ref in refs};frame=e.frame(np.zeros((len(c['dies']),3)))
 app=W.QApplication([]);w=MainWindow();w.show();errors=[];w.error=lambda text:errors.append(str(text))
 # This test isolates real handle events. Full assessment/Qt lifecycle has its
 # own GUI suite; only two nearby sources are replanned for the interaction.
 job['overlays']={};w.set_data(d,job);w.engine=e;w.case=c;w.geometry_cache=cache;w.current_frame=frame;w.shown_states=frame['states']
 w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
 w.constraint_feedback.request=lambda *_:None;w.reconstruction_yield.refresh=lambda *_:None;w.request_preview=lambda *_,**__:None
 w.ax.set_xlim(7000,7750);w.ax.set_ylim(2500,3250);w.plot_layout(preserve=True);w.canvas.draw();app.processEvents()
 try:
  options=[(q,q[k],kind) for q in frame['reconstruction']['pieces'] for k,kind in [('moved_cut','moved'),('extra_cut','extra')] if q.get(k) is not None and q[k].geom_type in ('LineString','MultiLineString')]
  q,cut,kind=min(options,key=lambda row:row[1].distance(target));xy=shapely.ops.nearest_points(cut,target)[0].coords[0]
  def event(name,point):
   x,y=w.ax.transData.transform(point);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button=1))
  measurements=[]
  for delta in (-2.,-5.,-10.):
   w.current_frame=frame;w.reconstruction_overlay.stamp=None;w.reconstruction_overlay.draw();w.canvas.draw()
   event('button_press_event',xy);assert w.cut_drag.state is not None,'real line not pickable'
   side=1 if w.cut_drag.state['kind']=='extra' else 0;initial=w.cut_drag.state['piece']['interval'][side]
   event('motion_notify_event',(xy[0],xy[1]+delta));w.cut_drag._tick()
   part=w.cut_drag.state['part'];assert part is not None
   value=part['b' if side else 'a'];assert abs(value-initial)>.5,('downward dead zone',delta,value,initial)
   assert all(x['geometry'].is_valid for rows in w.current_frame['reconstruction']['replacements'].values() for x in rows)
   measurements.append(dict(delta_y_um=delta,initial=initial,final=value,change=value-initial));w.cut_drag.cancel()
  assert not errors,errors
  result=dict(status='PASS',source_sha256=d.sha256,requested_xy=[7379,2933],picked_xy=list(xy),source_ref=[q['layer'],q['index']],samples=measurements)
  args.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))
 finally:
  w.current_frame=None;w.engine=None;e.close();w.close()
  end=time.monotonic()+30
  while w._tasks and time.monotonic()<end:app.processEvents();time.sleep(.01)
  app.processEvents()

if __name__=='__main__':main()
