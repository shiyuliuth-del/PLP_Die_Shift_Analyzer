"""Exercise the actual Q shortcut, edited Job restore and display-only layer controls."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import BINDING,QtCore,Qt
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
from die_shift_studio.shape_properties import ShapeProperties
from die_shift_studio.width_analysis import WidthAnalyzer
from die_shift_studio.gds_shapes import shape_nodes
app=W.QApplication([])
tmp=tempfile.TemporaryDirectory()
w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1360,850);w.show()
errors=[];w.error=lambda msg:errors.append(str(msg))


def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),
                    w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive(),w.mouse_die.timer.isActive(),w.mouse_die.idle.isActive(),w.width_yield.worker,w.width_yield.timer.isActive())):break
        time.sleep(.005)
    else:raise AssertionError('V24 GUI timeout')
    assert not errors,errors


name='示例P_单点PATH与相邻孔盘'
w.load_project(ROOT/'Jobs'/(name+'.dshift.json'));wait()
assert any('77/0' in s and '零宽 PATH' in s for s in w.runtime_view.toPlainText().splitlines())
assert '77/0' not in w.visible_boxes
w.run_evaluation();wait()
before=w.current_frame['width_analysis'];numeric=w.result['schemes']
old_id=w.data.geometry_id;sig=w.engine.signature
assert before['edge_violations'] and any(r['locked'] for r in before['rows'])
w.manual_states=np.array([[3.,0,0]]);w.request_preview();wait()
w.preview_tabs.setCurrentWidget(w.width_table.parentWidget());wait()
old_counts=w.current_frame['width_analysis']['counts'].copy()
old_rows=list(w.width_rows);hist=w.histogram_data['magnitude_counts'].copy()
assert w.layer_artists['5/0'].get_visible() and 0<w.layer_artists['5/0'].get_alpha()<w.style_for('5/0')['alpha']
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('visibility solved geometry again')):
    w.visible_boxes['5/0'].setChecked(False);wait()
    assert w.current_frame['width_analysis']['counts']==old_counts and w.width_rows==old_rows
    assert not w.layer_artists['5/0'].get_visible() and not w.width_overlay.boxes.get_visible()
    w.visible_boxes['4/0'].setChecked(False);wait()
    np.testing.assert_array_equal(w.histogram_data['magnitude_counts'],hist)
    assert len(w.connector_segments)==0 and w.result['schemes']==numeric
    w.visible_boxes['5/0'].setChecked(True);w.visible_boxes['4/0'].setChecked(True);wait()
    mode=w.setting_fields['width_display'].boxes
    for k,v in [('固定RDL','hide'),('边距','hide'),('标注','hide'),('固定过孔孔盘','dim')]:
        mode[k].setCurrentIndex(mode[k].findData(v))
    wait();assert not w.layer_artists['5/0'].get_visible() and not w.width_overlay.edges.get_visible()
    assert not any(a.get_visible() for a in w.width_overlay.labels)
    mode['固定RDL'].setCurrentIndex(mode['固定RDL'].findData('dim'));wait()
# Right click centres without changing scale; a right drag fits the whole box.
from matplotlib.backend_bases import MouseButton,MouseEvent
def pointer(name,world):
    xy=w.ax.transData.transform(world)
    event=MouseEvent(name,w.canvas,*xy,button=MouseButton.RIGHT)
    w.canvas.callbacks.process(name,event)

w.fit_plot();wait();span=np.ptp(w.ax.get_xlim());original_limits=(w.ax.get_xlim(),w.ax.get_ylim())
px,py=w.ax.transData.transform((40.,20.));scale=w.canvas.devicePixelRatioF()
QTest.mouseClick(w.canvas,getattr(Qt,'MouseButton',Qt).RightButton,
                 pos=QtCore.QPoint(round(px/scale),round(w.canvas.height()-py/scale)));wait()
np.testing.assert_array_equal((w.ax.get_xlim(),w.ax.get_ylim()),original_limits)
menu=W.QApplication.activePopupWidget()
if menu is not None:menu.close();wait()
assert abs(np.ptp(w.ax.get_xlim())-span)<2
pointer('button_press_event',(70.,55.));pointer('motion_notify_event',(-50.,-10.))
assert w.navigation.rubber is not None
pointer('button_release_event',(-50.,-10.));wait()
assert w.navigation.rubber is None and np.ptp(w.ax.get_xlim())<span
new_limits=(w.ax.get_xlim(),w.ax.get_ylim())
assert new_limits[0][0]<=-49 and new_limits[0][1]>=69 and new_limits[1][0]<=-9 and new_limits[1][1]>=54
w.canvas.die_move_active=True
pointer('button_press_event',(0.,0.));pointer('button_release_event',(0.,0.))
assert (w.ax.get_xlim(),w.ax.get_ylim())==new_limits;w.canvas.die_move_active=False

# Default return animation plays real intermediate geometry; repeating it and
# toggling the presentation checkbox must not repeat the width computation.
w.manual_states=None;w.active_preview=None;w.request_preview();wait()
seen=[];original_frame=w.show_motion_frame
def record(frame):
    seen.append(frame['states'].copy());original_frame(frame)
w.show_motion_frame=record
# Exercise the return animation even if all extreme poses are problem-free.
w.problem_filter.setChecked(False);wait();w.extreme_table.setCurrentCell(1,0);wait()
start=w.current_frame['states'].copy();assert np.any(start)
seen.clear();w.extreme_table.setCurrentCell(0,0);wait()
assert seen and any(np.any(z) and not np.array_equal(z,start) for z in seen)
assert w.current_frame['nominal_global'] and not np.any(w.current_frame['states'])
w.extreme_table.setCurrentCell(1,0);wait();seen.clear()
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('cached zero animation recomputed')):
    w.extreme_table.setCurrentCell(0,0);wait()
assert w._prepared_motion['cached'] and seen
back=w.setting_fields['animation_return_zero'];back.setChecked(False);wait()
w.extreme_table.setCurrentCell(1,0);wait();seen.clear();w.extreme_table.setCurrentCell(0,0);wait()
assert not seen and not np.any(w.current_frame['states'])
# Plain layout without overlays has the same replay contract, ending blank.
back.setChecked(True);w.width_toggle.setChecked(False);wait()
for attempt in range(2):
    w.manual_states=np.array([[8.,0,0]]);w.request_preview();wait()
    seen.clear();w.manual_states=None;w.request_preview();wait()
    assert seen and w.current_frame is None
    if attempt:assert w._prepared_motion['cached']
# With adaptive copper enabled, intermediate frames stay binary and the
# endpoint restores the nominal global probability decision.
w.width_toggle.setChecked(True);w.deformation_toggle.setChecked(True);wait()
strategy=w.setting_fields['animation_strategy'];strategy.setCurrentIndex(strategy.findData('stream'));wait()
w.manual_states=np.array([[3.,0,0]]);w.request_preview();wait();seen.clear()
w.manual_states=None;w.active_preview=None;w.request_preview();wait()
assert seen and any(np.any(z) for z in seen)
assert w.current_frame['nominal_global'] and not np.any(w.current_frame['states'])
assert w.current_frame.get('global_deformation') is not None
w.deformation_toggle.setChecked(False);strategy.setCurrentIndex(strategy.findData('prepared'));wait()
w.show_motion_frame=original_frame;w.width_toggle.setChecked(True);wait()
# The true P throat is 52.6 um. At 19 um spacing a single 15 um line
# needs 53 um; exercise highlighting at this genuine deficit.
w.fields['line_space_um'].setValue(19);wait()
w.manual_states=np.array([[3.,0,0]]);w.request_preview();wait()
assert w.width_overlay.boxes.get_zorder()>w.width_overlay.outlines.get_zorder()
assert all(a.get_zorder()<w.width_overlay.boxes.get_zorder() for a in w.width_overlay.inset.values())
assert any(a.get_visible() for a in w.width_overlay.inset.values())
label_mode=mode['标注'];label_mode.setCurrentIndex(label_mode.findData('show'));wait()
visible_labels=[a for a in w.width_overlay.labels if a.get_visible()]
assert visible_labels and all(a.get_text().startswith('原宽:') and '\n预期收窄:' in a.get_text() for a in visible_labels)
if os.environ.get('DSA_WIDTH_SCREENSHOT'):w.canvas.grab().save(os.environ['DSA_WIDTH_SCREENSHOT'])
label_mode.setCurrentIndex(label_mode.findData('hide'));wait()
w.selection.selected=[('4/0',0),('5/0',0)];w.selection.draw()
edited=[]
def interact():
    dlg=app.activeModalWidget()
    if not isinstance(dlg,ShapeProperties):
        errors.append('Q 未打开属性窗口');return
    try:
        assert dlg.group.count()==2
        for g in range(dlg.group.count()):
            dlg.group.setCurrentIndex(g)
            if dlg.ref==('4/0',0):break
        assert dlg.ref==('4/0',0) and '单点 PATH 圆' in dlg.group.currentText()
        assert dlg.model.rowCount()==1 and dlg.preview.rowCount()>4
        dlg.table.setCurrentIndex(dlg.model.index(0,0));dlg.remove_node();dlg.confirm()
        assert dlg.isVisible() and dlg.message.text() and w.data.geometry_id==old_id
        dlg.restore();dlg.table.setCurrentIndex(dlg.model.index(0,0));dlg.insert_node()
        assert dlg.model.rowCount()==2
        dlg.table.setCurrentIndex(dlg.model.index(1,0));dlg.remove_node()
        dlg.model.setData(dlg.model.index(0,0),'-83')
        dlg.group.setCurrentIndex((g+1)%dlg.group.count());dlg.group.setCurrentIndex(g)
        assert float(dlg.model.rows[0][0])==-83
        # Save a screenshot only when explicitly requested by the validation runner.
        if os.environ.get('DSA_PROPERTY_SCREENSHOT'):dlg.grab().save(os.environ['DSA_PROPERTY_SCREENSHOT'])
        dlg.confirm();edited.append(True)
    except BaseException as exc:errors.append(repr(exc));dlg.reject()
w.activateWindow();w.canvas.setFocus();app.processEvents()
QtCore.QTimer.singleShot(40,interact)
QTest.keyClick(w.canvas,getattr(Qt,'Key',Qt).Key_Q);wait()
assert edited and w.data.geometry_id!=old_id and w.engine.signature!=sig
assert w.result['geometry_revision']==w.data.edit_digest
assert shape_nodes(w.data,'4/0',0)[1].tolist()==[[-83.,0.]]
assert w.current_frame['states'][0,0]==3.
back.setChecked(False);wait()
path=w.jobs_dir/'edited.dshift.json';w.write_project(path)
job=json.loads(path.read_text());assert len(job['node_edits'])==1 and job['settings']['width_display']['标注']=='hide'
w.load_project(path);wait();assert w.data.node_edits==job['node_edits']
assert w.view_settings.width_display['边距']=='hide' and not w.view_settings.animation_return_zero
w.load_project(ROOT/'Jobs'/(name+'.dshift.json'));wait()
assert not w.data.node_edits and shape_nodes(w.data,'4/0',0)[1].tolist()==[[-85.,0.]]
assert w.view_settings.width_display['标注']=='show' and w.view_settings.animation_return_zero
w.close();app.processEvents();QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete);tmp.cleanup()
print('GUI_V24_PASS: Q keyboard, source and preview nodes, atomic validation, saved edits, fresh evaluation, independent visibility, clipped styles, cached origin transitions and right-button navigation.')
