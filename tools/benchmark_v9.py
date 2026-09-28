"""Identical manual states for baseline/source V9 timing. Optional --root checkout."""
import os,sys,argparse,time,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ap=argparse.ArgumentParser();ap.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));ap.add_argument('--output',required=True);args=ap.parse_args();ROOT=Path(args.root).resolve();sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio import __version__
app=W.QApplication([]);w=MainWindow();w.show();errors=[];w.error=lambda a:errors.append(str(a))
def wait():
 end=time.monotonic()+60
 while time.monotonic()<end:
  app.processEvents()
  if not(w.worker or w.preview_worker or w.preview_pending or (hasattr(w,'transition') and w.transition.timer.isActive())):break
  time.sleep(.001)
 assert not errors,errors
 app.processEvents()
project=dict(schema_version=5,gds_path='/home/niubi/下载/GD55_9-21.gds',roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'},layer_order=['6/0','5/0','4/0','3/0'],parameters={})
w.load_gds(project['gds_path'],project=project);wait();start=time.perf_counter();w.run_evaluation();wait();evaluation_ms=(time.perf_counter()-start)*1000;w.space_toggle.setChecked(True);wait()
states=[np.array([[x,-x,np.deg2rad(t)],[-x,x,np.deg2rad(-t)],[x,x,np.deg2rad(t)]]) for x,t in [(35,1),(-35,-1),(35,-1),(-35,1),(0,1),(35,0),(-35,0),(0,-1)]]
w.manual_states=states[0];w.request_preview();wait()
render=[];compute=[]
for state in states:
 t=time.perf_counter();w.manual_states=state;w.request_preview();wait();render.append((time.perf_counter()-t)*1000)
 t=time.perf_counter();w.engine.frames.clear();w.engine.frame(state);compute.append((time.perf_counter()-t)*1000)
record=dict(evaluation_ms=evaluation_ms,contact_summary={key:len(w.result['contact_analysis'].get(key,[])) for key in ('issues','unresolved','probabilities')},scheme_statuses=[q['status'] for q in w.result['schemes']],host_load_average=os.getloadavg(),source_sha256=w.data.sha256,animation_configured_ms=getattr(getattr(w,'view_settings',None),'animation_duration_ms',None),version=__version__,interface_count=len(w.engine.contact.records),geometry_counts={k:len(v) for k,v in w.data.layers.items()},Qt_preview_ms=render,Qt_preview_median_ms=float(np.median(render)),geometry_ms=compute,geometry_median_ms=float(np.median(compute)),image_count=len(w.ax.images),patch_count=len(w.ax.patches))
if 'animation_enabled' in w.setting_fields:
 w.setting_fields['animation_enabled'].setChecked(True);wait();timing=[];visual=[]
 for state in states[:3]:
  t=time.perf_counter();w.manual_states=state;w.request_preview();wait();timing.append((time.perf_counter()-t)*1000);visual.append(w.transition.last_duration_ms)
 record['animation_total_ms']=timing;record['animation_visual_ms']=visual
 w.fit_plot();w.canvas.draw();w.grab().save(str(Path(args.output).parent.parent/'figures/software_V9_GD55.png'))
 w.tabs.setCurrentWidget(w.analysis_page);app.processEvents();w.grab().save(str(Path(args.output).parent.parent/'figures/software_V9_analysis.png'))
Path(args.output).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');w.close();app.processEvents();print(json.dumps(record,ensure_ascii=False,indent=2))
