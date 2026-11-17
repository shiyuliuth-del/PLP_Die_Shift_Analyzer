"""Actual GD55 port/branch controls: Qt dot roundtrip and nominal trunk witness."""
import argparse,json,os,sys,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'tests')]
import numpy as np,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.gui import MainWindow,W
from qt_shutdown import close_window
parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);args=parser.parse_args()
job=normalize_project(json.loads(args.project.read_text()));path=Path(job['gds_path']);path=path if path.is_absolute() else args.project.parent/path
# Private fixture may retain an old machine's source path.
if not path.exists():path=args.project.parent/Path(job['gds_path']).name
d=read_gds(path,job['top_cell']);d,job=restore_project_geometry(d,job);c=build_case(d,job['roles'],layer_order=job['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(job['parameters']);cfg=ViewSettings.from_dict(job['settings']);e=PreviewEngine(cache,c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,cfg);r=e.reconstructor
refs=[min(r.vias_by_target,key=lambda ref:cache.layers[ref[0]]['geometry'][ref[1]].distance(shapely.Point(pt))) for pt in ((2981,-966),(-1907,1340))]
r.vias_by_target={ref:r.vias_by_target[ref] for ref in refs};frame=e.frame(np.zeros((len(c['dies']),3)))
app=W.QApplication([]);w=MainWindow();w.show();errors=[];w.error=lambda text:errors.append(str(text));job['overlays']={}
w.set_data(d,job);w.engine=e;w.case=c;w.geometry_cache=cache;w.current_frame=frame;w.shown_states=frame['states'];w.view_settings=cfg
w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
w.constraint_feedback.request=lambda *_:None;w.reconstruction_yield.refresh=lambda *_:None;w.request_preview=lambda *_,**__:None
try:
 rows=frame['reconstruction']['pieces'];branch=next(q for q in rows if (q['layer'],q['index'])==refs[1])
 assert branch['moved_cut'].bounds[2]<-1980,branch['moved_cut'].wkt
 for q in branch.get('graph_members',[branch]):
  assert q['nominal_regions']['C'].covers(q['moving_nominal']) if 'moving_nominal' in q else True
 target=next(q for q in rows if (q['layer'],q['index'])==refs[0]);plan=target['route_plan']
 assert plan.length>100 and not target.get('graph_members')
 w.ax.set_xlim(2850,3120);w.ax.set_ylim(-1200,-850);w.plot_layout(preserve=True);w.canvas.draw();app.processEvents()
 def event(name,point):
  x,y=w.ax.transData.transform(point);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button=1))
 q=target;start=np.asarray(q['moved_cut'].centroid.coords[0]);end=np.asarray(plan.route.coords[0])
 event('button_press_event',start);assert w.cut_drag.state is not None
 event('motion_notify_event',end);w.cut_drag._tick();assert w.cut_drag.state['part']['near'].geom_type=='Point'
 event('button_release_event',end);w.reconstruction_overlay.stamp=None;w.reconstruction_overlay.draw();w.canvas.draw()
 q=next(q for q in w.current_frame['reconstruction']['pieces'] if (q['layer'],q['index'])==refs[0])
 handle=w.reconstruction_overlay.handles[(w.reconstruction_overlay._key(q),'moved')]
 start=np.asarray(handle.centroid.coords[0]);offset=start-np.asarray(q['moved_cut'].coords[0])
 event('button_press_event',start);assert w.cut_drag.state is not None
 destination=np.asarray(plan.route.interpolate(plan.land_end+30).coords[0])+offset
 event('motion_notify_event',destination);w.cut_drag._tick()
 assert w.cut_drag.state['part']['near'].geom_type=='LineString',w.cut_drag.state['part']
 assert w.cut_drag.state['part']['a']>plan.land_end+10
 event('button_release_event',destination)
 assert not errors,errors
 print(json.dumps(dict(status='PASS',source_sha256=d.sha256,refs=refs,scope='real Qt zero pose line-to-point-to-line; common moving trunk; actual copper connectivity'),ensure_ascii=False))
finally:
 w.engine=None;e.close();close_window(app,w)
