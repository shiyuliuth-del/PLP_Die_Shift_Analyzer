import argparse,os,sys,time,json,tempfile
from pathlib import Path
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.reconstruction_edits import key
from shapely.ops import nearest_points
parser=argparse.ArgumentParser(description='Actual mouse check for nu17123 point at 8911,5514; private job stays external');parser.add_argument('project',type=Path);jobfile=parser.parse_args().project;job=normalize_project(json.loads(jobfile.read_text()));path=Path(job['gds_path']);path=path if path.is_absolute() else jobfile.parent/path
job['overlays']={};d=read_gds(path,job['top_cell']);d,job=restore_project_geometry(d,job);c=build_case(d,job['roles'],layer_order=job['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(job['parameters']);cfg=ViewSettings.from_dict(job['settings'])
e=PreviewEngine(cache,c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,cfg)
refs={('2/0',11),('2/0',34)}
def restrict():e.reconstructor.vias_by_target={k:v for k,v in e.reconstructor.vias_by_target.items() if k in refs}
restrict();z=np.zeros((len(c['dies']),3));f=e.frame(z)
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1400,1000);w.show();errors=[];w.error=lambda s:errors.append(str(s));w.set_data(d,job);w.case=c;w.geometry_cache=cache;w.engine=e;w.view_settings=cfg
w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
w.constraint_feedback.request=lambda *_:None;w.reconstruction_yield.refresh=lambda *_:None;w.invalidate_reconstruction_edit=lambda *_:None
w.ax.set_xlim(8500,9350);w.ax.set_ylim(5200,5800)
def install(frame):
 w.current_frame=frame;w.shown_states=z;w.plot_layout(preserve=True);w.canvas.draw();app.processEvents()
def rebuild(*_,**__):
 e.configure_reconstruction(True,w.view_settings);restrict();install(e.frame(z));w.engine=e
w.request_preview=rebuild;install(f)
def event(name,xy):
 x,y=w.ax.transData.transform(xy);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button=1))
point=shapely.Point(8911,5514)
try:
 qs=[q for q in w.current_frame['reconstruction']['pieces'] if q['index']==34 and not q['moved_cut'].is_empty]
 q=min(qs,key=lambda q:q['moved_cut'].distance(point));xy=nearest_points(q['moved_cut'],point)[0].coords[0]
 event('button_press_event',xy);assert w.cut_drag.state is not None
 selected=w.cut_drag.state['piece'];plan=selected['route_plan'];b=selected['interval'][1];endpoint=plan.route.coords[0]
 print('PICKED',plan.identity,'interval',selected['interval'],'companions',len(w.cut_drag.state['companions']),flush=True)
 event('motion_notify_event',endpoint);w.cut_drag._tick();assert w.cut_drag.state['part'] is not None
 print('ENTRY',w.cut_drag.state['part']['a'],w.cut_drag.state['part']['b'],flush=True)
 assert w.cut_drag.state['part']['a']==0.;event('button_release_event',endpoint)
 dots=[]
 for q in w.current_frame['reconstruction']['pieces']:
  if q['index']!=34:continue
  g=w.reconstruction_overlay.handles.get((w.reconstruction_overlay._key(q),'moved'))
  if g is not None and g.geom_type in ('Point','MultiPoint'):
   dots.extend((q,xy) for xy in shapely.get_coordinates(g))
 assert dots,'whole-pad mode has no selectable point'
 q,xy=min(dots,key=lambda row:np.linalg.norm(row[1]-endpoint));event('button_press_event',xy);assert w.cut_drag.state is not None
 state=w.cut_drag.state;assert state['piece']['index']==34
 identity=state['piece']['route_plan'].identity;own=state['piece']['route_plan'];offset=state['handle_offset'];old_other=state['piece']['interval'][1]
 results=[]
 for mark in (5.,10.,20.,40.,60.,80.):
  target=np.asarray(own.route.interpolate(mark).coords[0])+offset
  event('motion_notify_event',target);w.cut_drag._tick()
  assert state['piece']['route_plan'].identity==identity
  if state['part'] is not None:
   assert abs(state['part']['b']-old_other)<.001
   results.append((mark,state['part']['a'],state['part']['b']))
  assert w.current_frame['reconstruction']['electrically_valid']
 print('DRAG',results,flush=True);assert results and results[-1][1]>own.land_end
 event('button_release_event',target)
 assert not errors,errors
 print('NU17123_HANDLE_PASS',flush=True)
finally:
 w.current_frame=None;w.engine=None;e.close();w.close();app.processEvents();tmp.cleanup()
