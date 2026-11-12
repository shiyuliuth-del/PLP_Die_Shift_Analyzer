"""Real Qt/matplotlib events for unified overlays and both nominal cut handles."""
import os,sys,tempfile,time,json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import numpy as np
from matplotlib.backend_bases import MouseEvent
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1450,950);w.show();errors=[];w.error=lambda s:errors.append(str(s))
def wait():
 end=time.monotonic()+180
 while time.monotonic()<end:
  app.processEvents()
  if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_pending,w.motion_worker,w.coverage_timer.isActive(),w.viewport_timer.isActive(),w.transition.timer.isActive(),w.reconstruction_yield.worker,w.reconstruction_yield.pending,w.reconstruction_yield.timer.isActive(),w.constraint_feedback.worker,w.constraint_feedback.pending)):break
  time.sleep(.005)
 else:raise AssertionError('timeout')
 assert not errors,errors

def event(name,xy):
 px,py=w.ax.transData.transform(xy);ev=MouseEvent(name,w.canvas,px,py,button=1)
 w.canvas.callbacks.process(name,ev);app.processEvents()

def drag(q,which,mark,live=True):
 plan=q['route_plan'];line=q['extra_cut'] if which else q['moved_cut'];start=np.array(line.centroid.coords[0]);end=np.array(plan.route.interpolate(mark).coords[0])
 if not which:
  from die_shift_studio.preview import rigid_transform
  end=rigid_transform(end,w.current_frame['rotation_centers'][q['die']],w.current_frame['states'][q['die']])
 lo=np.minimum(start,end)-70;hi=np.maximum(start,end)+70;w.ax.set_xlim(lo[0],hi[0]);w.ax.set_ylim(lo[1],hi[1]);w.plot_layout(preserve=True);w.canvas.draw()
 event('button_press_event',start);assert w.cut_drag.state is not None,('miss',which,start)
 event('motion_notify_event',end)
 for _ in range(12):app.processEvents();time.sleep(.01)
 assert w.reconstruction_overlay.hover.get_visible()
 if live:assert w.current_frame is not w.cut_drag.state['frame'],'no live region feedback'
 else:assert w.current_frame is w.cut_drag.state['frame']
 event('button_release_event',end);wait()
 return next(p for p in w.current_frame['reconstruction']['pieces'] if (p['layer'],p['index'],p['die'])==(q['layer'],q['index'],q['die']))
try:
 assert not hasattr(w,'deformation_toggle')
 assert w.probability_toggle is w.footprint_toggle
 assert list(w.setting_sections).count('极端足迹')==1 and '缓存管理' not in w.setting_sections
 assert w.regions_dxf_button.text()=='动静区输出'
 assert not w.show_issues.isChecked() and not w.view_settings.show_low_contact
 w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait()
 w.view_settings=replace(w.view_settings,animation_enabled=False,reconstruction_live=True);w.load_view_settings()
 w.run_evaluation();wait();w.reconstruction_toggle.setChecked(True);wait()
 w.manual_states=np.array([[25.,10.,.01],[0,0,0]]);w.request_preview();wait()
 q=next(q for q in w.current_frame['reconstruction']['pieces'] if q['die']==0 and q['route_plan'] is not None)
 for (layer,kind),artist in w.reconstruction_overlay.artists.items():
  assert w.layer_artists[layer].get_zorder()<artist.get_zorder()<w.layer_artists[layer].get_zorder()+.01
 a,b=q['interval']
 before=w.view_settings.reconstruction_intervals
 pos=np.asarray(q['moved_cut'].centroid.coords[0]);w.ax.set_xlim(pos[0]-100,pos[0]+100);w.ax.set_ylim(pos[1]-100,pos[1]+100);w.plot_layout(preserve=True);w.canvas.draw()
 event('button_press_event',pos);assert w.cut_drag.state is not None
 w.escape_action();assert w.cut_drag.state is None and w.view_settings.reconstruction_intervals==before
 q1=drag(q,0,a+60)
 assert q1['interval'][1]==b and q1['interval'][0]>a+50,q1['interval']
 q2=drag(q1,1,b-50);assert q2['interval'][0]==q1['interval'][0] and q2['interval'][1]<b-40
 # A second edit before the first worker completes must retain the first end.
 with patch.object(w,'request_preview'):
  qfast=drag(q2,0,q2['interval'][0]+10)
  qfast2=drag(qfast,1,qfast['interval'][1]-10)
  assert qfast2['interval'][0]==qfast['interval'][0]
 w.request_preview();wait()
 q2=next(p for p in w.current_frame['reconstruction']['pieces'] if p['die']==0)
 # Connection style is live, saved and reflected in final geometry.
 combo=w.setting_fields['reconstruction_style']
 assert [combo.itemText(i) for i in range(combo.count())]==['直线','一阶','二阶','圆弧']
 for style in ('linear','c1','arc','c2'):
  combo.setCurrentIndex(combo.findData(style));wait()
  assert w.current_frame['reconstruction']['parameters']['connection_style']==style
 assert '重构良率' in w.width_yield.label.text()
 assert w.reconstruction_yield.bank and w.reconstruction_yield.bank['total']>0
 q2=next(p for p in w.current_frame['reconstruction']['pieces'] if p['die']==0)
 # Whole landing point pulses while stationary hover and while dragging.
 qpoint=drag(q2,0,0)
 assert qpoint['moved_cut'].geom_type=='Point'
 point=np.array(qpoint['moved_cut'].coords[0]);event('motion_notify_event',point)
 app.processEvents();assert w.reconstruction_overlay.pulse_timer.isActive()
 values=[]
 for _ in range(12):
  app.processEvents();time.sleep(.02);values.append(w.reconstruction_overlay.hover.get_markersize())
 assert max(values)-min(values)>.4
 q2=drag(qpoint,0,100)
 assert q2['interval'][0]>0
 # Live preference is applied immediately and does not alter the final result.
 w.setting_fields['reconstruction_live'].setChecked(False);app.processEvents()
 q3=drag(q2,0,q2['interval'][0]+20,live=False)
 intervals=w.view_settings.reconstruction_intervals
 target=Path(tmp.name)/'saved.dshift.json';w.write_project(target);w.load_project(target);wait()
 assert w.view_settings.reconstruction_intervals==intervals and not w.view_settings.reconstruction_live,(w.view_settings.reconstruction_intervals,intervals,w.view_settings.reconstruction_live)
 w.run_evaluation();wait();w.reconstruction_toggle.setChecked(True);wait()
 w.manual_states=np.array([[-25.,-10.,-.01],[0,0,0]]);w.request_preview();wait()
 current=next(p for p in w.current_frame['reconstruction']['pieces'] if p['die']==0)
 assert current['interval']==q3['interval']
 w.setting_fields['show_contact_markers'].setChecked(True);app.processEvents();assert w.show_issues.isChecked()
 w.show_issues.setChecked(False);app.processEvents();assert not w.view_settings.show_contact_markers
 w.fit_plot();w.plot_layout(preserve=True);w.canvas.draw()
 if os.environ.get('DSA_QA_SCREENSHOT'):w.grab().save(os.environ['DSA_QA_SCREENSHOT'])
 print('GUI_V39_PASS: four styles, saved options, animated point hover/drag, constraints, whole-input yield')
finally:
 w.close();app.processEvents();tmp.cleanup()
