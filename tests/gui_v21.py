"""Qt regression: independent DIE motion, channel overlay, cached transitions."""
import os,sys,time,json,tempfile,math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore
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
# This ownership regression deliberately selects a contact-free pose.
w.problem_filter.setChecked(False);wait();w.extreme_table.setCurrentCell(1,0);wait()
for i in range(2):check_pad(i)
with patch.object(W.QInputDialog,'getItem',return_value=('DIE 2',True)):
 w.mouse_die.key(SimpleNamespace(key='m'))
assert w.mouse_die.owner==1
start=w.manual_states.copy();px,py=w.ax.transData.transform((1000,0));dx,dy=(24.,-13.);ex,ey=w.ax.transData.transform((1000+dx,dy))
w.mouse_die.press(SimpleNamespace(inaxes=w.ax,button=3,x=px,y=py));w.mouse_die.move(SimpleNamespace(x=ex,y=ey));w.mouse_die.release(SimpleNamespace(button=3));wait()
np.testing.assert_allclose(w.shown_states[1,:2],start[1,:2]+[dx,dy],atol=1e-8);check_pad(1)
w.mouse_die.scroll(SimpleNamespace(inaxes=w.ax,key=None,step=1));wait();check_pad(1)
assert np.isclose(w.shown_states[1,2],start[1,2]+math.radians(.1));w.mouse_die.exit()
# The portable mixed-width project saves the analysis checkbox on.
w.load_project(ROOT/'Jobs/示例O_运动网络三线路通道.dshift.json');wait();w.run_evaluation();wait()
assert w.width_toggle.isChecked() and not w.width_sufficient_toggle.isChecked()
w.tabs.setCurrentIndex(0);w.preview_tabs.setCurrentWidget(w.width_table.parentWidget());wait()
poses=[np.array([[x,0.,0.],[0.,0.,0.]]) for x in (35.,-35.)];seen=[];labels=[];original=w.show_motion_frame
def record(f):
 seen.append(f['states'].copy());original(f)
 labels.extend(a.get_text() for a in w.width_overlay.labels if a.get_visible())
w.show_motion_frame=record
for z in poses:w.manual_states=z;w.request_preview();wait()
for z in poses:w.manual_states=z;w.request_preview();wait()
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Completed channel pose recalculated')):
 for z in poses:w.manual_states=z;w.request_preview();wait()
assert len(seen)>1 and w._prepared_motion['cached'] and any('原宽:' in t and ',必过:' in t and ',现宽:' in t and '\n预期收窄:' in t for t in labels)
w.show_motion_frame=original
w.manual_states=poses[0];w.request_preview();wait()
def central(r):return r['pair']==('运动过孔孔盘 4/0 #1','运动过孔孔盘 4/0 #2')
target=next(r for r in w.width_rows if central(r))
assert (target['status'],target['original'],target['required'],target['current'])==('宽度不足',3,3,2)
assert w.width_overlay.boxes.get_visible() and w.width_overlay.outlines.get_visible()
w.visible_boxes['4/0'].setChecked(False);wait();assert not any(central(r) for r in w.width_overlay.rows)
w.visible_boxes['4/0'].setChecked(True);wait();row=next(i for i,q in enumerate(w.width_rows) if central(q))
w.width_table.setCurrentIndex(w.width_table.model().index(row,0));wait()
w.tabs.setCurrentWidget(w.detail);wait();assert '固定 RDL 通道容量' in w.detail.toPlainText()
w.tabs.setCurrentIndex(0);w.manual_states=np.array([[3.,0,0],[0.,0,0]]);w.request_preview();wait()
assert any(central(r) and r['status']=='需局部收窄' for r in w.width_rows)
w.manual_states=None;w.active_preview=None;w.request_preview();wait()
assert not any(r['status']=='宽度足够' for r in w.width_rows)
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Display filter recalculated channels')):
 w.width_sufficient_toggle.setChecked(True);wait()
 assert any(central(r) and r['status']=='宽度足够' for r in w.width_rows)
 path=w.jobs_dir/'V23.dshift.json';w.write_project(path);w.write_project(path)
 w.width_toggle.setChecked(False);w.width_sufficient_toggle.setChecked(False);wait()
w.load_project(path);wait();assert w.width_toggle.isChecked() and w.width_sufficient_toggle.isChecked()
assert any(central(r) for r in w.width_rows) and w.width_table.model().rowCount()==len(w.width_rows)
# Reconstruction preview: the cut is taken across the route, the movable part
# follows its die, and the fissure is covered by the two cut faces' four corners.
w.load_project(ROOT/'Jobs/示例N_三线路窄通道宽度.dshift.json');wait();w.run_evaluation();wait()
w.width_toggle.setChecked(True);w.reconstruction_toggle.setChecked(True);wait()
w.manual_states=poses[0];w.request_preview();wait()
recon=w.current_frame['reconstruction'];piece=recon['pieces'][0]
# This pad is smaller than the automatic cut distance, so it follows the die whole.
assert (recon['summary']['pieces'],recon['summary']['bridges'],recon['summary']['orphans'])==(1,0,1),recon['summary']
assert piece['layer']=='5/0' and piece['orphan'] and piece['gap_um'] is None,piece
assert abs(piece['movable_area_um2']-20700.)<.1,piece['movable_area_um2']
assert w.reconstruction_table.rowCount()==1
assert w.reconstruction_table.horizontalHeaderItem(0).text()=='固定RDL层'
summary=w.reconstruction_summary.text()
assert '自动划分' in summary and '不代表产线' in summary and '整块随动' in summary,summary
assert '贴合' in summary and '任何切线位置' in summary,summary
assert '2.5 个落盘直径' in summary and '90°' in summary,summary
assert not w.width_sufficient_toggle.isChecked() and all(r['required']==0 for r in w.current_frame['width_analysis']['rows'])
w.preview_tabs.setCurrentWidget(w.reconstruction_page)
w.reconstruction_table.setCurrentCell(0,0);wait()
assert w.reconstruction_overlay.movable.get_visible() and w.reconstruction_overlay.moved.get_visible()
w.tabs.setCurrentWidget(w.detail);wait()
text=w.detail.toPlainText()
assert '当前姿态的固定RDL重构预演' in text and '自动划分是软件猜测' in text
w.tabs.setCurrentIndex(0);wait()
# A stepped trace: the cut lands inside the pad, so a real seam opens and is
# covered by the polygon of the two cut faces' four corners.
w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait();w.run_evaluation();wait()
w.width_toggle.setChecked(True);w.reconstruction_toggle.setChecked(True);wait()
w.manual_states=np.array([[35.,0.,0.],[0.,0.,0.]]);w.request_preview();wait()
recon=w.current_frame['reconstruction'];piece=recon['pieces'][0]
assert (recon['summary']['pieces'],recon['summary']['bridges'])==(1,1),recon['summary']
assert abs(piece['gap_um']-35.)<1e-6 and abs(piece['bridge_length_um']-32.)<1e-3,piece
assert abs(piece['bridge_area_um2']-1120.)<.1,piece['bridge_area_um2']
assert abs(recon['summary']['cover_area_um2']-1120.)<.1,recon['summary']
assert '补线 1 处' in w.reconstruction_summary.text(),w.reconstruction_summary.text()
anchor,moved_anchor=piece['seam']
assert abs((moved_anchor[0]-anchor[0])-35.)<1e-6 and abs(moved_anchor[1]-anchor[1])<1e-6,piece['seam']
w.preview_tabs.setCurrentWidget(w.reconstruction_page)
w.reconstruction_table.setCurrentCell(0,0);wait()
assert w.reconstruction_overlay.bridges.get_visible() and w.reconstruction_overlay.cuts.get_visible()
# Both panels use the selected pose. Reconstruction can expose a copper short
# even on a design with no routed demand crossing a specific gap.
collide=np.array([[35.,0.,0.],[-35.,0.,0.]]);w.manual_states=collide;w.request_preview();wait()
before=w.current_frame['width_analysis']['counts'].copy()
assert w.current_frame['reconstruction']['summary']['pieces']==2
w.reconstruction_toggle.setChecked(False);wait()
assert w.current_frame.get('reconstruction') is None
assert before==w.current_frame['width_analysis']['counts']
w.width_toggle.setChecked(False);wait()
result=dict(status='PASS',extreme_and_m_key_pad_motion=True,independent_die_stacks=True,channel_counts=[3,3,2],three_risk_classes=True,
            project_roundtrip=True,legacy_defaults=True,reconstruction_gap_um=35.,reconstruction_seam_um=32.,
            reconstruction_cover_area_um2=1120.,animation_frames_seen=len(seen),cached_replay=True,layer_visibility=True,table_focus=True,report_formulas=True)
w.close();app.processEvents();QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete)
w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.error=lambda msg:errors.append(str(msg));w.show()
w.load_project(path);wait()
assert w.width_toggle.isChecked() and w.width_sufficient_toggle.isChecked() and w.width_table.model().rowCount()>0
w.load_project(ROOT/'Jobs/示例N_三线路窄通道宽度.dshift.json');wait()
assert not any((w.width_toggle.isChecked(),w.width_sufficient_toggle.isChecked(),w.reconstruction_toggle.isChecked()))
w.close();app.processEvents();QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete);tmp.cleanup();print('GUI_V23_CHANNEL_PASS',result)
