"""Local nul7123 mouse regression; pass the external engineering JSON path."""
import os,sys,time,tempfile,json,argparse
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np,shapely
from matplotlib.backend_bases import MouseEvent
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.fast_preview import PreviewEngine
from qt_shutdown import close_window
parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);f=parser.parse_args().project.resolve();app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);job=json.loads(f.read_text());job['gds_path']=str(f.parent/job['gds_path']);job['overlays']={};job['settings'].update(animation_enabled=False,reconstruction_auto=False,save_project_results=False);path=root/'case.dshift.json';path.write_text(json.dumps(job));w=MainWindow(jobs_dir=root/'Jobs');w.resize(1600,1000);w.show();errors=[];w.error=lambda x:errors.append(str(x))
def wait():
 end=time.monotonic()+180
 while time.monotonic()<end:
  app.processEvents();time.sleep(.01)
  if not w._tasks and not w.preview_pending:return
 raise AssertionError(w.runtime_view.toPlainText()[-2000:])
def event(name,xy):
 x,y=w.ax.transData.transform(xy);w.canvas.callbacks.process(name,MouseEvent(name,w.canvas,x,y,button=1));app.processEvents()
try:
 w.load_project(path);wait();w.run_evaluation();wait();w.reconstruction_yield.refresh=lambda *_:None
 e=w.engine;e.configure_reconstruction(True,w.view_settings);r=e.reconstructor;refs={min(r.vias_by_target,key=lambda ref:r.model.nodes[r.model.ids[ref]]['poly'].distance(shapely.Point(point))) for point in ((7253,3076),(6548,3747))};r.vias_by_target={ref:values for ref,values in r.vias_by_target.items() if ref in refs}
 frame=e.frame(np.zeros((len(w.case['dies']),3)));w.current_frame=frame;w.shown_states=frame['states'];w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
 for target in ((7253,3076),(6548,3747)):
  w.set_view(target,300,200);w.plot_layout(preserve=True);w.canvas.draw();picked=w.reconstruction_overlay.pick(*target,100);assert picked and picked[2] in ('moved','extra'),target
  q,xy,kind=picked;point=np.mean(xy,axis=0);event('button_press_event',point);assert w.cut_drag.state is not None,target
  state=w.cut_drag.state;plan=state['piece']['route_plan'];side=int(kind=='extra');old=state['piece']['interval'];destination=point+plan.tangent(old[side])*(15 if side==0 else -15)
  event('motion_notify_event',destination);w.cut_drag._tick();part=w.cut_drag.state['part'];assert part is not None and abs((part['b'] if side else part['a'])-old[side])>3,(target,old,part)
  event('button_release_event',destination);assert w.reconstruction_pending
 assert not errors,errors
 print('V50_NUL_MOUSE_PASS both specified zero-pose boundaries respond to actual Matplotlib/Qt press-move-release; pending manual compute',flush=True)
finally:close_window(app,w);tmp.cleanup()
