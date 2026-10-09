"""Real Qt wheel routing, independent DRC, keyboard and direct mouse motion."""
import os,sys,time,json,tempfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PyQt5.QtTest import QTest
from matplotlib.backend_bases import MouseEvent,MouseButton,KeyEvent
from die_shift_studio.gui import MainWindow,W,QtCore,QtGui
from die_shift_studio.qt_compat import Qt
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda x:errors.append(str(x));metrics={}
def busy():return any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.transition.timer.isActive(),w.viewport_timer.isActive(),w.mouse_die.pending,w.mouse_die.full_pending,w.mouse_die.idle.isActive(),w.mouse_die.timer.isActive()))
def wait(timeout=120):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents()
        if not busy():break
        time.sleep(.002)
    assert not busy(),'worker timeout';assert not errors,errors;app.processEvents()
def mouse(name,pixel,button=None,step=0):
    event=MouseEvent(name,w.canvas,*pixel,button=button,step=step);w.canvas.callbacks.process(name,event)
def wheel(widget,delta=-120):
    event=QtGui.QWheelEvent(QtCore.QPointF(10,10),QtCore.QPointF(widget.mapToGlobal(QtCore.QPoint(10,10))),QtCore.QPoint(),QtCore.QPoint(0,delta),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    app.sendEvent(widget,event);app.processEvents()
def shot(name):w.canvas.draw();w.grab().save(str(ROOT/'figures'/('software_V14_'+name+'.png')))
w.load_project(ROOT/'Jobs/示例G_小偏移与三段追溯.dshift.json');wait()
assert not w.drc_toggle.isChecked()
assert w.drc_toggle.parentWidget().layout().itemAt(0).widget() is w.drc_toggle
# DRC before any assessment or deformation. Parameter edits recompute it.
w.deformation_toggle.setChecked(True);wait()
w.drc_toggle.setChecked(True);wait();assert w.result is None and 'drc' in w.current_frame
w.deformation_toggle.setChecked(False);wait()
assert w.violation_artist.get_visible()
w.fields['line_width_um'].setValue(50);wait();assert any(v['kind']=='线宽不足' and v['layer']=='5/0' for v in w.current_frame['drc'])
w.visible_boxes['3/0'].setChecked(False);app.processEvents();assert all(k!='3/0' or not a.get_visible() for k,a in w.layer_artists.items())
w.visible_boxes['3/0'].setChecked(True);w.drc_toggle.setChecked(False);assert not w.violation_artist.get_visible()
w.fields['line_width_um'].setValue(20);wait();w.run_evaluation();wait()
w.manual_states=np.array([[1.,1,0],[0,35,0]]);w.request_preview();wait();w.deformation_toggle.setChecked(True);wait()
assert w.current_frame['deformation']['accepted_count']==2
values=w.current_frame['deformation']['replacements']['5/0'];assert any(v['mode'] in ('three_part','axial') and not v['adaptation'].is_empty for v in values)
base,dark,outline=w.deformation_artists['5/0'];assert outline.get_visible() and outline.get_paths()
assert outline.get_facecolor().size==0;assert any(dashes is not None for _,dashes in outline.get_linestyle())
w.fit_plot();wait();shot('three_part')
# Progress lives in the top header and retains size while hidden.
assert w.progress.mapToGlobal(QtCore.QPoint()).y()<w.canvas.mapToGlobal(QtCore.QPoint()).y()
before=w.job_combo.mapToGlobal(QtCore.QPoint());w.progress.show();app.processEvents();after=w.job_combo.mapToGlobal(QtCore.QPoint())
assert before==after;w.progress.hide();app.processEvents();assert before==w.job_combo.mapToGlobal(QtCore.QPoint())
assert w.figure.subplotpars.left<=.045
# Wheel on tables, embedded selectors, scroll bars, and runtime text scrolls.
w.layer_table.setMinimumHeight(85);w.layer_table.setMaximumHeight(110);app.processEvents()
bar=w.layer_table.verticalScrollBar();assert bar.maximum()>0
order=w.ordered_layers();bar.setValue(0);wheel(w.layer_table.viewport());assert bar.value()>0 and w.ordered_layers()==order
bar.setValue(0);combo=w.role_boxes[w._layer_keys[0]];old=combo.currentText();wheel(combo);assert combo.currentText()==old and bar.value()>0
bar.setValue(0);wheel(bar);assert bar.value()>0 and w.ordered_layers()==order
w.runtime_toggle.setChecked(True)
w.runtime_view.appendPlainText('\n'.join('滚轮测试 '+str(i) for i in range(150)));app.processEvents();bar=w.runtime_view.verticalScrollBar();bar.setValue(0);wheel(w.runtime_view.viewport());assert bar.value()>0
w.runtime_toggle.setChecked(False);w.layer_table.setMinimumHeight(275);w.layer_table.setMaximumHeight(16777215);app.processEvents()
# F restores the original view, with normal input edits unaffected by shortcuts.
w.set_view([200,600],400,250);app.processEvents();w.canvas.setFocus();QTest.keyClick(w.canvas,Qt.Key_F);wait()
np.testing.assert_allclose((w.ax.get_xlim(),w.ax.get_ylim()),w._default_view)
# M opens the selector; right drag is in actual data coordinates, not pixels.
with patch.object(W.QInputDialog,'getItem',return_value=('DIE 2',True)) as chooser:
    QTest.keyClick(w.canvas,Qt.Key_M);app.processEvents();assert chooser.call_count==1
assert w.mouse_die.owner==1 and w.canvas.die_move_active
w.canvas.draw();start=w.ax.transData.transform((0,600));end=start+np.array([4.,6.]);inv=w.ax.transData.inverted().frozen()
old=w.manual_states.copy();mouse('button_press_event',start,MouseButton.RIGHT)
# Native MouseEvent positions are integer pixels; use the same reference.
expected=old.copy();expected[1,:2]+=inv.transform(end.astype(int))-inv.transform(start.astype(int))
for t in np.linspace(0,1,16):mouse('motion_notify_event',start+(end-start)*t,MouseButton.RIGHT)
mouse('button_release_event',end,MouseButton.RIGHT);wait()
np.testing.assert_allclose(w.shown_states,expected,atol=1e-8);np.testing.assert_allclose(w.current_frame['deformation']['states'],expected)
assert w.mouse_die.text.isVisible() and 'dx =' in w.mouse_die.text.text()
limits=w.ax.get_xlim();mouse('scroll_event',end,'up',1);wait();assert w.ax.get_xlim()==limits
assert abs(w.shown_states[1,2]-expected[1,2]-np.deg2rad(.1))<1e-10
mouse('scroll_event',end,'down',-1);wait();np.testing.assert_allclose(w.shown_states,expected,atol=1e-8)
# All overlays use the displayed pose, while the probability distribution stays configured.
w.drc_toggle.setChecked(True);w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait()
w.setting_fields['animation_enabled'].setChecked(True);wait();old=w.shown_states.copy();target=old.copy();target[1,:2]+=[2,-3]
published=[];plot=w.plot_layout
def record(*a,**kw):
    plot(*a,**kw)
    if w.current_frame:published.append(w.shown_states.copy())
w.plot_layout=record;started=time.perf_counter()
for t in np.linspace(0,1,80):w.mouse_die.submit(old*(1-t)+target*t)
wait();w.plot_layout=plot;np.testing.assert_allclose(w.shown_states,target)
assert w._prepared_motion.get('direct') and not w.transition.timer.isActive()
assert len(published)<10;metrics['coalesced_display_frames']=len(published);metrics['direct_pose_with_overlays_ms']=(time.perf_counter()-started)*1000
assert w.probability_data and w.space_data;shot('direct_motion')
w.canvas.setFocus();QTest.keyClick(w.canvas,Qt.Key_Escape);app.processEvents();assert w.mouse_die.owner is None and not w.mouse_die.text.isVisible()
limits=w.ax.get_xlim();mouse('scroll_event',end,'up',1);assert np.ptp(w.ax.get_xlim())<np.ptp(limits)
w.selection.selected=[('5/0',0)];w.selection.draw();QTest.keyClick(w.canvas,Qt.Key_Escape);app.processEvents();assert not w.selection.selected
# Leaving direct motion for a table selection discards the old pending pose.
w.mouse_die.enter(0);w.mouse_die.submit(w.manual_states+np.array([[.1,0,0],[0,0,0]]))
w.select_preview(0,0);wait();assert w.mouse_die.owner is None and w.manual_states is None
assert not w.mouse_die.pending and not w.mouse_die.full_pending
mouse('button_release_event',end,MouseButton.RIGHT);assert not w.mouse_die.idle.isActive()
w.manual_states=target;w.request_preview();wait()
# Best-effort geometry and pink marker toggle correspond to the actual final layout.
w.setting_fields['animation_enabled'].setChecked(False);w.setting_fields['deformation_min_angle_deg'].setValue(179);wait()
assert w.current_frame['deformation']['unresolved_count']>0 and not w.current_frame['deformation']['replacements']
assert w.unadapted_artist.get_visible()
assert w.violation_artist.get_visible() and w.violation_artist.get_paths();w.set_view([400,780],1200,700);wait();shot('global_drc')
w.drc_toggle.setChecked(False);assert not w.violation_artist.get_visible();w.drc_toggle.setChecked(True);wait();assert w.violation_artist.get_visible()
metrics['status']='PASS';(ROOT/'results/gui_V14_legacy.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
w.close();app.processEvents();tmp.cleanup();print(json.dumps(metrics,ensure_ascii=False,indent=2))
