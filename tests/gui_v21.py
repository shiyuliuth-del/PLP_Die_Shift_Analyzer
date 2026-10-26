"""Qt regression: independent DIE motion, channel overlay, cached transitions."""
import os,sys,time,json,tempfile,math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.width_analysis import WidthAnalyzer
from die_shift_studio.preview import rigid_transform
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1500,1000);w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
 end=time.monotonic()+180
 while time.monotonic()<end:
  app.processEvents()
  if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive(),w.mouse_die.timer.isActive(),w.mouse_die.idle.isActive())):break
  time.sleep(.004)
 else:raise AssertionError('V21 Qt timeout')
 assert not errors,errors

def check_pad(i):
 expected=rigid_transform(w.data.layers['4/0'][i],w.engine.bound.centers[i],w.shown_states[i])
 np.testing.assert_allclose(w.current_frame['layers']['4/0']['polygons'][i],expected,atol=1e-8)

w.load_project(ROOT/'Jobs/示例M_不同芯片底层与Pad随动.dshift.json');wait();w.run_evaluation();wait()
assert not w.width_toggle.isChecked()
w.extreme_table.setCurrentCell(1,0);wait()
for i in range(2):check_pad(i)
with patch.object(W.QInputDialog,'getItem',return_value=('DIE 2',True)):
 w.mouse_die.key(SimpleNamespace(key='m'))
assert w.mouse_die.owner==1
start=w.manual_states.copy();px,py=w.ax.transData.transform((1000,0));dx,dy=(24.,-13.);ex,ey=w.ax.transData.transform((1000+dx,dy))
w.mouse_die.press(SimpleNamespace(inaxes=w.ax,button=3,x=px,y=py));w.mouse_die.move(SimpleNamespace(x=ex,y=ey));w.mouse_die.release(SimpleNamespace(button=3));wait()
np.testing.assert_allclose(w.shown_states[1,:2],start[1,:2]+[dx,dy],atol=1e-8);check_pad(1)
w.mouse_die.scroll(SimpleNamespace(inaxes=w.ax,key=None,step=1));wait();check_pad(1)
assert np.isclose(w.shown_states[1,2],start[1,2]+math.radians(.1));w.mouse_die.exit()
w.grab().save(str(ROOT/'figures/software_V21_pad_motion.png'))
# Fixed RDL capacity changes with an independent moving Pad.
w.load_project(ROOT/'Jobs/示例N_三线路窄通道宽度.dshift.json');wait();w.run_evaluation();wait();w.width_toggle.setChecked(True);wait()
assert w.current_frame['width_analysis']['complete'];w.preview_tabs.setCurrentIndex(w.preview_tabs.count()-1)
poses=[np.array([[x,0.,0.],[0.,0.,0.]]) for x in (35.,-35.)];seen=[];original=w.show_motion_frame
w.show_motion_frame=lambda f:(seen.append(f['states'].copy()),original(f))
for z in poses:w.manual_states=z;w.request_preview();wait()
for z in poses:w.manual_states=z;w.request_preview();wait()
with patch.object(WidthAnalyzer,'_discover',side_effect=AssertionError('Completed pose width recalculated')):
 for z in poses:w.manual_states=z;w.request_preview();wait()
assert len(seen)>1 and w._prepared_motion['cached'];w.show_motion_frame=original
w.manual_states=poses[0];w.request_preview();wait()
rows=w.current_frame['width_analysis']['rows'];target=next(q for q in rows if q['plane']=='5' and set(q['pair'])=={'pad:4/0:0','pad:4/0:1'})
assert (target['strands'],target['capacity'],target['gap_um'])==(3,2,75.)
assert w.width_overlay.boxes.get_visible() and w.width_overlay.edges.get_visible()
assert any(q['status']=='不足' and q['plane']=='5' for q in w.width_rows)
w.visible_boxes['4/0'].setChecked(False);wait();assert not any(set(q['pair'])=={'pad:4/0:0','pad:4/0:1'} for q in w.width_overlay.rows)
w.visible_boxes['4/0'].setChecked(True);wait()
row=next(i for i,q in enumerate(w.width_rows) if q['plane']=='5' and set(q['pair'])=={'pad:4/0:0','pad:4/0:1'})
w.width_table.setCurrentCell(row,0);wait();w.grab().save(str(ROOT/'figures/software_V21_width_risk.png'))
w.tabs.setCurrentWidget(w.detail);wait();assert '当前姿态的局部宽度分析' in w.detail.toPlainText();assert 'Ncap' in w.detail.toPlainText()
w.grab().save(str(ROOT/'figures/software_V21_width_report.png'))
w.tabs.setCurrentIndex(0);w.manual_states=None;w.active_preview=None;w.request_preview();wait();assert not np.any(w.current_frame['states'])
with patch.object(WidthAnalyzer,'_discover',side_effect=AssertionError('Zero cache lost')):
 w.manual_states=poses[0];w.request_preview();wait();w.manual_states=None;w.request_preview();wait()
assert not np.any(w.current_frame['states']);w.width_toggle.setChecked(False);wait();assert not w.width_overlay.boxes.get_visible()
result=dict(status='PASS',extreme_and_m_key_pad_motion=True,independent_die_stacks=True,channel_requirement=3,channel_capacity_after_shift=2,channel_gap_um=75,animation_frames_seen=len(seen),cached_replay=True,zero_cache=True,layer_visibility=True,table_focus=True,report_formulas=True)
(ROOT/'results/gui_V21.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
w.close();app.processEvents();tmp.cleanup();print('GUI_V21_PASS',result)
