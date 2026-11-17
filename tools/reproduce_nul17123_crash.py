"""Real event-loop drag stress; keep native fault output, never modify source input."""
import os,sys,time,json,tempfile,argparse,faulthandler
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');faulthandler.enable(all_threads=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,BINDING
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);parser.add_argument('--cycles',type=int,default=5);parser.add_argument('--round-trip',action='store_true');args=parser.parse_args()
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
job=json.loads(args.project.read_text());gds=Path(job['gds_path']);job['gds_path']=str(gds if gds.is_absolute() else (args.project.parent/gds).resolve());job['overlays']={};job.setdefault('settings',{})['animation_enabled']=False
path=folder/'test.dshift.json';path.write_text(json.dumps(job));w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1500,1000);w.show();errors=[];w.error=lambda value:(errors.append(str(value)),print('ERROR',value,flush=True))
def pump(seconds):
 end=time.monotonic()+seconds
 while time.monotonic()<end:app.processEvents();time.sleep(.005)
def wait(predicate,seconds=600):
 end=time.monotonic()+seconds
 while not predicate() and time.monotonic()<end:pump(.02)
 assert predicate(),w.runtime_view.toPlainText()[-4000:]
 assert not errors,errors
left=getattr(Qt,'MouseButton',Qt).LeftButton
none=getattr(Qt,'MouseButton',Qt).NoButton
mods=getattr(Qt,'KeyboardModifier',Qt).NoModifier
def pos(xy):
 x,y=w.ax.transData.transform(xy);scale=w.canvas.devicePixelRatioF()
 return QtCore.QPoint(round(x/scale),round(w.canvas.height()-y/scale))
try:
 w.load_project(path);wait(lambda:w.worker is None);w.run_evaluation();wait(lambda:w.worker is None and w.result is not None)
 w.reconstruction_toggle.setChecked(True);wait(lambda:bool(w.current_frame and w.current_frame.get('reconstruction')))
 w.set_view((8914,5497),400,350);w.plot_layout(preserve=True);w.canvas.draw();pump(.1)
 for cycle in range(args.cycles):
  picked=w.reconstruction_overlay.pick(8914,5497,80)
  assert picked is not None and picked[2] not in ('bridge','problem'),picked
  piece,xy,kind=picked;point=np.mean(xy,axis=0)
  print('PRESS',cycle,piece['index'],kind,point,flush=True)
  QTest.mousePress(w.canvas,left,pos=pos(point));pump(.03);assert w.cut_drag.state is not None
  plan=w.cut_drag.state['piece']['route_plan'];side=int(kind=='extra')
  endpoint=np.asarray(plan.route.coords[-1 if side else 0]);target=endpoint+((-1 if side==0 else 1)*plan.tangent(plan.length if side else 0)*20)
  for fraction in np.linspace(0,1,15):
   q=pos(point+(target-point)*fraction);global_=w.canvas.mapToGlobal(q)
   event=__import__('die_shift_studio.qt_compat',fromlist=['QtGui']).QtGui.QMouseEvent(getattr(QtCore.QEvent,'Type',QtCore.QEvent).MouseMove,QtCore.QPointF(q),QtCore.QPointF(global_),none,left,mods)
   app.sendEvent(w.canvas,event);pump(.06)
  print('DOT',cycle,w.cut_drag.state.get('part',{}).get('a'),w.cut_drag.state.get('part',{}).get('b'),flush=True)
  pump(.4);QTest.mouseRelease(w.canvas,left,pos=pos(target));pump(.5)
  assert not errors,errors
  if args.round_trip:
   wait(lambda:not w.preview_workers and not w.preview_pending)
   picked=w.reconstruction_overlay.pick(*endpoint,80)
   assert picked is not None and picked[2] in ('moved','extra'),picked
   piece,xy,kind=picked;start=xy.mean(axis=0)
   QTest.mousePress(w.canvas,left,pos=pos(start));pump(.03);assert w.cut_drag.state is not None
   plan=w.cut_drag.state['piece']['route_plan'];side=int(kind=='extra');offset=w.cut_drag.state['handle_offset']
   mark=max(0.,plan.fixed_start-60) if side else min(plan.length,plan.land_end+60)
   target=np.asarray(plan.route.interpolate(mark).coords[0])+offset
   for fraction in np.linspace(0,1,12):
    q=pos(start+(target-start)*fraction);global_=w.canvas.mapToGlobal(q)
    event=__import__('die_shift_studio.qt_compat',fromlist=['QtGui']).QtGui.QMouseEvent(getattr(QtCore.QEvent,'Type',QtCore.QEvent).MouseMove,QtCore.QPointF(q),QtCore.QPointF(global_),none,left,mods)
    app.sendEvent(w.canvas,event);pump(.06)
   QTest.mouseRelease(w.canvas,left,pos=pos(target));pump(.2)
   print('RETURN_LINE',cycle,flush=True)
 print('REAL_QT_POINT_STRESS_PASS',flush=True)
finally:
 w.close();wait(lambda:not w._tasks,120);app.processEvents();tmp.cleanup()
 print('REAL_QT_CLEAN_EXIT',flush=True)
