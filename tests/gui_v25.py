"""Real Qt row clicks, mouse & numeric replay, fold persistence and callout placement."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import shapely
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,BINDING
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.width_analysis import gate_rectangle,WidthAnalyzer
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
from matplotlib.backend_bases import MouseButton,MouseEvent

app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs')
w.resize(1410,900);w.show();errors=[];w.error=lambda text:errors.append(text)
def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not (w.worker or w.preview_workers or w.preview_pending or w.motion_worker or w.motion_pending or w.hardware_worker
                or w.transition.timer.isActive() or w.coverage_timer.isActive() or w._coverage_pending or w.viewport_timer.isActive()
                or w.mouse_die.timer.isActive() or w.mouse_die.idle.isActive()):break
        time.sleep(.004)
    else:raise AssertionError('V25 UI did not settle')
    assert not errors,errors

left=getattr(Qt,'MouseButton',Qt).LeftButton
def first():
    table=w.extreme_table
    point=table.visualRect(table.model().index(0,0)).center()
    QTest.mouseClick(table.viewport(),left,pos=point);wait()
    assert table.currentRow()==0 and w.manual_states is None and w.mouse_die.owner is None
    assert w.current_frame is None or (w.current_frame['nominal_global'] and not np.any(w.current_frame['states']))

def emit(kind,xy):
    w.canvas.callbacks.process(kind,MouseEvent(kind,w.canvas,*xy,button=MouseButton.RIGHT))

def check_callouts():
    w.canvas.draw();renderer=w.canvas.get_renderer()
    visible=[a for a in w.width_overlay.labels if a.get_visible()]
    assert visible,'expected room for a width label'
    boxes=[]
    for a in visible:
        assert a.get_fontsize()==w.view_settings.width_label_font_pt
        pix=a.get_bbox_patch().get_window_extent(renderer)
        assert w.ax.bbox.contains(pix.x0,pix.y0) and w.ax.bbox.contains(pix.x1,pix.y1)
        world=w.ax.transData.inverted().transform(pix.get_points()).ravel()
        assert not len(w.width_overlay.tree.query(shapely.box(*world),predicate='intersects')),world
        assert all(not pix.overlaps(previous) for previous in boxes)
        boxes.append(pix)
    return len(visible)

name='示例O_运动网络三线路通道';job=ROOT/'Jobs'/(name+'.dshift.json')
w.load_project(job);wait();w.run_evaluation();wait()
w.controls.setCurrentIndex(2);wait()
assert all(not b.isChecked() for b in w.setting_sections.values())
assert all(not w.setting_fields[key].isVisible() for key in w.setting_fields)
section=w.setting_sections['宽度分析 · 元素显示'];QTest.mouseClick(section,left)
size=w.setting_fields['width_label_font_pt'];assert size.isVisible()
size.setValue(11);wait();assert not w.preview_pending
w.controls.setCurrentIndex(0);w.width_toggle.setChecked(True);wait()
w.setting_fields['cache_max_entries'].setValue(10);wait()
seen=[];show=w.show_motion_frame
def observe(frame):
    seen.append(frame['states'].copy());show(frame)
w.show_motion_frame=observe
# M + right drag starts on row 0; no alternate row or Esc is needed.
start=w.ax.transData.transform(w.case['dies'][0].centroid.coords[0]);end=start+np.array([10.,3.])
with patch.object(W.QInputDialog,'getItem',return_value=('DIE 1',True)):
    w.mouse_die.choose()
assert w.extreme_table.currentRow()==-1
emit('button_press_event',start);emit('motion_notify_event',end);emit('button_release_event',end);wait()
target=w.manual_states.copy()
assert seen and any(np.any(z) and not np.array_equal(z,target) for z in seen)
np.testing.assert_array_equal(w.shown_states,target)
seen.clear();first();assert seen and w._prepared_motion['cached']
# Same M movement and return reuse the packet. Numeric input uses its own
# exact target; repeat both directions with every geometry computation blocked.
w.manual_die.setCurrentIndex(0);w.manual_fields['dx'].setValue(35);w.manual_fields['dy'].setValue(0);w.manual_fields['angle'].setValue(0)
w.apply_manual();wait();assert w.extreme_table.currentRow()==-1
assert w.current_frame['states'][0,0]==35 and any(0<x[0,0]<35 for x in seen)
check_callouts();first()
group=w.cache_group
def disk():
    return {p:(p.stat().st_size,p.stat().st_mtime_ns) for c in group.caches.values() for p in c.root.iterdir()}
baseline=disk();ordinary=len(group.recent);replays=len(group.replays)
with patch.object(PreviewEngine,'frame',side_effect=AssertionError('replayed cached pose or animation recomputed')):
    for i in range(4):
        seen.clear()
        if i%2:
            w.mouse_die.enter(0);w.mouse_die.submit(target.copy())
        else:
            # M reports the displayed pose back to the numeric boxes. Request
            # the exact same numeric triple again, not its rounded mouse pose.
            for key,value in [('dx',35),('dy',0),('angle',0)]:w.manual_fields[key].setValue(value)
            w.apply_manual()
        wait();assert w._prepared_motion['cached'] and seen
        seen.clear();first();assert w._prepared_motion['cached'] and seen
assert disk()==baseline and ordinary==len(group.recent) and replays==len(group.replays)
# A click during a new M request must cancel pointer intent and reject any
# worker that finishes after the first row has taken ownership of the view.
w.mouse_die.enter(0);w.mouse_die.submit(target*1.37);w.mouse_die.flush();first()
# Changing the label font, camera position or viewport changes layout only.
w.manual_fields['dx'].setValue(35);w.manual_fields['dy'].setValue(0);w.manual_fields['angle'].setValue(0)
w.apply_manual();wait()
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('view solved geometry')):
    w.set_view((0,0),460,300);w.viewport_changed();wait();n=check_callouts()
    size.setValue(16);wait();check_callouts()
    w.set_view((75,38),370,280);w.viewport_changed();wait();check_callouts()
    w.resize(1230,810);wait();check_callouts()
path=w.jobs_dir/'test.dshift.json';w.write_project(path);w.load_project(path);wait()
assert w.setting_sections['宽度分析 · 元素显示'].isChecked() and size.value()==16
w.load_project(job);wait()
assert not any(b.isChecked() for b in w.setting_sections.values()) and size.value()==8
w.close();app.processEvents();QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete);tmp.cleanup()
print(f'GUI_V25_PASS cached mouse/numeric/zero motions, no disk churn; folded Jobs; resized/panned callouts clear real rectangles ({n} visible).')
