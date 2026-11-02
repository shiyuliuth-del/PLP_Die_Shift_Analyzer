"""Qt regression: independent DIE motion, channel overlay, cached transitions."""
import os,sys,time,json,tempfile,math
from dataclasses import replace
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
# Landing air-gap follows the moving via and its vertical fixed-RDL neighbour.
w.load_project(ROOT/'Jobs/示例N_三线路窄通道宽度.dshift.json');wait();w.run_evaluation();wait();w.width_toggle.setChecked(True);wait()
assert w.current_frame['width_analysis']['complete'];w.preview_tabs.setCurrentIndex(w.preview_tabs.count()-1)
poses=[np.array([[x,0.,0.],[0.,0.,0.]]) for x in (35.,-35.)];seen=[];dims=[];original=w.show_motion_frame
def record(f):
 seen.append(f['states'].copy());original(f)
 dims.append([l.get_text() for l,a in zip(w.width_overlay.labels,w.width_overlay.arrows) if a.get_visible()])
w.show_motion_frame=record
for z in poses:w.manual_states=z;w.request_preview();wait()
for z in poses:w.manual_states=z;w.request_preview();wait()
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Completed pose air-gap recalculated')):
 for z in poses:w.manual_states=z;w.request_preview();wait()
assert len(seen)>1 and w._prepared_motion['cached']
# The CAD dimensions must be live during the animation, not only at its end.
drawn=[d for d in dims if d]
assert drawn and any(text.replace('.','',1).isdigit() for d in drawn for text in d)
assert len(drawn)<len(dims) or len(dims)==1
w.show_motion_frame=original
w.manual_states=poses[0];w.request_preview();wait()
rows=w.current_frame['width_analysis']['rows'];target=next(q for q in rows if q['key']=='4/0' and q['index']==0)
assert target['status']=='不足' and target['gap_um']==0.,target
assert target['target']['layer']=='5/0' and target['target']['net']!=target['net']
assert w.width_overlay.lands.get_visible() and w.width_overlay.arrows[0].get_visible()
assert any(q['status']=='不足' for q in w.width_rows)
w.visible_boxes['4/0'].setChecked(False);wait();assert not any(q['key']=='4/0' for q in w.width_overlay.rows)
w.visible_boxes['4/0'].setChecked(True);wait()
row=next(i for i,q in enumerate(w.width_rows) if q['key']=='4/0' and q['index']==0)
w.width_table.setCurrentCell(row,0);wait();w.grab().save(str(ROOT/'figures/software_V21_width_risk.png'))
w.tabs.setCurrentWidget(w.detail);wait();assert '落盘 air-gap' in w.detail.toPlainText();assert '落盘外扩量' in w.detail.toPlainText()
w.grab().save(str(ROOT/'figures/software_V21_width_report.png'))
w.tabs.setCurrentIndex(0);wait()
# Switching the analysis object re-runs against the other vias' original footprints.
w.view_settings=replace(w.view_settings,width_mode='landing',width_search_radius_um=400.,width_gap_threshold_um=60.)
w.manual_states=poses[1];w.request_preview();wait()
landing=w.current_frame['width_analysis']
assert landing['mode']=='landing' and landing['target_label']=='最近异网原始落盘',landing['mode_label']
assert all(q['target'] is None or q['target']['kind']=='via' for q in landing['rows'])
assert all(q['target'] is None or q['target']['layer']==q['key'] for q in landing['rows'])
assert '其他落盘区原始位置' in w.width_summary.text()
assert w.width_table.horizontalHeaderItem(6).text()=='最近异网原始落盘'
w.grab().save(str(ROOT/'figures/software_V21_landing_mode.png'))
w.view_settings=replace(w.view_settings,width_mode='vertical',width_search_radius_um=100.,width_gap_threshold_um=0.)
w.manual_states=poses[0];w.request_preview();wait()
assert w.current_frame['width_analysis']['mode']=='vertical'
w.tabs.setCurrentIndex(0);w.manual_states=None;w.active_preview=None;w.request_preview();wait();assert not np.any(w.current_frame['states'])
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Zero cache lost')):
 w.manual_states=poses[0];w.request_preview();wait();w.manual_states=None;w.request_preview();wait()
assert not np.any(w.current_frame['states']);w.width_toggle.setChecked(False);wait();assert not w.width_overlay.lands.get_visible()
# Reconstruction preview: the cut is taken across the route, the movable part
# follows its die, and the fissure is covered by the two cut faces' four corners.
w.load_project(ROOT/'Jobs/示例N_三线路窄通道宽度.dshift.json');wait();w.run_evaluation();wait()
w.view_settings=replace(w.view_settings,width_mode='vertical',width_search_radius_um=100.,width_gap_threshold_um=0.)
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
assert '移动后的固定RDL铜' in w.width_summary.text(),w.width_summary.text()
w.preview_tabs.setCurrentWidget(w.reconstruction_page)
w.reconstruction_table.setCurrentCell(0,0);wait()
assert w.reconstruction_overlay.movable.get_visible() and w.reconstruction_overlay.moved.get_visible()
w.grab().save(str(ROOT/'figures/software_V21_reconstruction.png'))
w.tabs.setCurrentWidget(w.detail);wait()
text=w.detail.toPlainText()
assert '当前姿态的固定RDL重构预演' in text and '自动划分是软件猜测' in text
w.grab().save(str(ROOT/'figures/software_V21_reconstruction_report.png'))
w.tabs.setCurrentIndex(0);wait()
# A stepped trace: the cut lands inside the pad, so a real seam opens and is
# covered by the polygon of the two cut faces' four corners.
w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');wait();w.run_evaluation();wait()
w.view_settings=replace(w.view_settings,width_mode='vertical',width_search_radius_um=100.,width_gap_threshold_um=0.)
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
w.grab().save(str(ROOT/'figures/software_V21_reconstruction_bridge.png'))
# On this example the reconstruction exposes a short that the original geometry hides.
collide=np.array([[35.,0.,0.],[-35.,0.,0.]]);w.manual_states=collide;w.request_preview();wait()
rows=[q for q in w.width_rows if q['key']=='4/0' and q['index'] in (0,1)]
assert len(rows)==2 and all(q['status']=='不足' and q['gap_um']==0. for q in rows),rows
assert w.current_frame['reconstruction']['summary']['pieces']==2
w.grab().save(str(ROOT/'figures/software_V21_reconstruction_collision.png'))
w.reconstruction_toggle.setChecked(False);wait()
assert w.current_frame.get('reconstruction') is None
assert not w.width_rows  # 25 um is safe, so nothing needs dimensioning any more
rows=[q for q in w.current_frame['width_analysis']['rows'] if q['key']=='4/0' and q['index'] in (0,1)]
assert len(rows)==2 and all(q['status']=='充足' and abs(q['gap_um']-25.)<1e-6 for q in rows),rows
assert '移动后的固定RDL铜' not in w.width_summary.text(),w.width_summary.text()
w.width_toggle.setChecked(False);wait()
result=dict(status='PASS',extreme_and_m_key_pad_motion=True,independent_die_stacks=True,width_rule_violation=1,width_gap_um=0.0,width_target_net_foreign=True,landing_mode='其他落盘区原始位置',reconstruction_pieces=1,reconstruction_gap_um=35.0,reconstruction_seam_um=32.0,reconstruction_cover_area_um2=1120.0,reconstruction_collision=0.0,reconstruction_orphan_area_um2=20700.0,animation_frames_seen=len(seen),cached_replay=True,zero_cache=True,layer_visibility=True,table_focus=True,report_formulas=True)
(ROOT/'results/gui_V21.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
w.close();app.processEvents();tmp.cleanup();print('GUI_V21_PASS',result)
