"""Native mouse-modifier selection, scroll-preserving reset, binary coverage and filtered rows."""
import json,math,os,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import Qt,QtCore,BINDING
from die_shift_studio.extreme_filter import ProblemIndex,contact_factors
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest

tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);app=W.QApplication([]);w=MainWindow(jobs_dir=root/'Jobs')
w.resize(1400,900);w.show();errors=[];w.error=lambda text:errors.append(str(text))
keys=getattr(Qt,'KeyboardModifier',Qt);left=getattr(Qt,'MouseButton',Qt).LeftButton

def wait():
    until=time.monotonic()+180
    while time.monotonic()<until:
        app.processEvents()
        if not any((w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.filter_job,w.hardware_worker,w.coverage_timer.isActive(),
                    w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):
            app.processEvents();break
        time.sleep(.005)
    else:raise AssertionError('GUI timeout')
    assert not errors,errors
    assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-2000:]

def load(name,params=None):
    q=json.loads((ROOT/'examples'/(name+'.json')).read_text());q['parameters'].update(params or {})
    q['gds_path']=str(ROOT/'examples'/q['gds_path']);q.setdefault('settings',{}).update(animation_enabled=False)
    q['overlays']={};path=root/'actual.dshift.json';path.write_text(json.dumps(q));w.load_project(path);wait()
    w.run_evaluation();wait()

def point(xy):
    x,y=w.ax.transData.transform(xy)
    return QtCore.QPoint(round(x/w.canvas.device_pixel_ratio),round(w.canvas.height()-y/w.canvas.device_pixel_ratio))

def click(xy,mod=keys.NoModifier):QTest.mouseClick(w.canvas,left,mod,point(xy));app.processEvents()
def drag(a,b,mod):
    QTest.mousePress(w.canvas,left,mod,point(a));QTest.mouseMove(w.canvas,point(b));QTest.mouseRelease(w.canvas,left,mod,point(b));app.processEvents()

load('示例D_芯片外扇出');assert w.problem_filter.isChecked()
w.set_view([0,1000],300,200);w.viewport_changed(force=True);wait();w.canvas.draw()
click([0,1000]);one=w.selection.selected.copy();assert one and one[0][0]=='3/0',one
click([0,1000],keys.ShiftModifier);two=w.selection.selected.copy();assert len(two)==2 and two[0]==one[0]
click([110,1060],keys.ShiftModifier);assert w.selection.selected==two
click([0,1000],keys.ControlModifier);assert w.selection.selected==two[1:]
drag([-45,960],[45,1040],keys.ShiftModifier);picked=set(w.selection.selected);assert len(picked)>1
drag([45,1040],[-45,960],keys.ControlModifier);assert set(w.selection.selected)<picked
click([110,1060]);assert not w.selection.selected
click([0,1000]);QTest.keyClick(w.canvas,getattr(Qt,'Key',Qt).Key_Escape);assert not w.selection.selected
w.grid_mode.setCurrentIndex(0);wait()
idx=ProblemIndex(w.extremes,contact_factors(w.result['extreme_factors']))
assert [i for i,_ in w.page_items]==[idx.at(j) for j in range(min(256,idx.total))]
w.problem_filter.setChecked(False);wait()
table=w.extreme_table;assert table.rowCount()>35
table.setCurrentCell(32,0);wait();bar=table.verticalScrollBar();bar.setValue(bar.maximum());app.processEvents()
y=bar.value();assert y>0
QTest.mouseClick(w.restore_button,left);wait()
assert table.currentRow()==0 and bar.value()==y and w.active_preview is None,(bar.value(),y)
w.setting_fields['probability_show_ranges'].setChecked(True)
w.target_sigma.setCurrentText('∞');wait()
w.probability_toggle.setChecked(True);wait()
assert w.probability_data['binary'] and w.probability_data['samples']==0
assert not w.setting_fields['probability_show_ranges'].isEnabled() and w.setting_fields['probability_show_ranges'].isChecked()
w.manual_die.setCurrentIndex(0);w.manual_fields['dx'].setValue(18);w.manual_fields['angle'].setValue(.8)
QTest.mouseClick(w.manual_button,left);wait()
a=next(a for a in w.probability_artists if a._owner==0)
v=np.array(a._pivot)+[57.,-43.]
from die_shift_studio.preview import rigid_transform
expected=rigid_transform(v[None,:],np.array(a._pivot),w.shown_states[0])[0]
np.testing.assert_allclose(w.ax.transData.inverted().transform(a.get_transform().transform(v)),expected)
w.visible_boxes[a._via_layer].setChecked(False);wait();assert not a.get_visible()
w.write_project(root/'saved.dshift.json');saved=json.loads((root/'saved.dshift.json').read_text())
assert not saved['extreme_view']['filter_problems'] and saved['settings']['probability_show_ranges']
w.target_sigma.setCurrentText('3');wait();assert w.setting_fields['probability_show_ranges'].isEnabled()
load('示例O_运动网络三线路通道',dict(target_nsigma=0,per_die_enabled=True,die_bounds=[[35,0,.7],[0,10,0]]))
assert w.problem_filter.isChecked()
w.width_toggle.setChecked(True);wait();w.grid_mode.setCurrentIndex(0);wait()
summary=w.current_frame['width_summary'];ix=ProblemIndex(w.extremes,summary['red_factors'])
assert [i for i,_ in w.page_items]==[ix.at(j) for j in range(ix.total)] and ix.total
assert w.width_overlay.outlines.get_visible() and not w.width_overlay.boxes.get_visible()
assert all(not a.get_visible() for a in w.width_overlay.labels)
table.setCurrentCell(1,0);wait();assert w.width_overlay.boxes.get_visible()
with patch('die_shift_studio.width_summary.summarize',side_effect=AssertionError('cache miss')):
    QTest.mouseClick(w.restore_button,left);wait();assert not w.width_overlay.boxes.get_visible()
w.close();app.processEvents();tmp.cleanup()
print('GUI_V28_PASS native Shift/Ctrl picking, filtering, unchanged scroll position, exact infinity footprints and nominal outlines')
