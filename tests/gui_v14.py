"""Actual Qt controls, wheel modifiers, pivot persistence, marker units and contours."""
import os,sys,time,json,tempfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from matplotlib.backend_bases import MouseEvent
from PyQt5.QtTest import QTest
from die_shift_studio.gui import MainWindow,W,QtCore,QtGui
from die_shift_studio.qt_compat import Qt
from die_shift_studio.preview import rigid_transform
from die_shift_studio.statistics import statistical_state
from die_shift_studio.report import report_document
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda e:errors.append(str(e))
def busy():return any((w.width_yield.worker,w.width_yield.timer.isActive(),w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.transition.timer.isActive(),w.viewport_timer.isActive(),w.mouse_die.pending,w.mouse_die.full_pending,w.mouse_die.idle.isActive(),w.mouse_die.timer.isActive()))
def wait():
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        app.processEvents()
        if not busy():break
        time.sleep(.002)
    assert not busy(),'worker timeout';assert not errors,errors

def shot(name):
    w.canvas.draw();w.grab().save(str(ROOT/'figures'/('software_V14_'+name+'.png')))

w.load_project(ROOT/'Jobs/示例I_旋转中心与全范围概率.dshift.json');wait()
assert w.target_sigma.count()==14 and w.target_sigma.itemText(0)=='0' and not w.target_sigma.isEditable()
assert w.per_die_table.columnCount()==5 and w.per_die_table.horizontalHeaderItem(4).text()=='旋转中心'
assert w.per_die_table.cellWidget(0,4).value()[0]=='top_left'
assert w.per_die_table.cellWidget(1,4).value()==['custom',150.,-100.]
w.run_evaluation();wait();assert w.result
w.manual_states=np.array([[10.,15,.013],[-8,4,-.02]]);w.request_preview(direct=True);wait()
origins=w.rotation_centers();state=w.shown_states.copy()
for i,info in enumerate(w.case['infos']):
    point=rigid_transform([info['cx'],info['cy']],origins[i],state[i])
    np.testing.assert_allclose(w.die_labels[i].get_position(),point)
for issue in w.current_frame['contact']['issues']:
    np.testing.assert_allclose(w.issue_origin(issue),rigid_transform(issue['origin'],origins[issue['die']-1],state[issue['die']-1]))
# Dispatch through the real canvas callback chain: exactly one handler acts.
w.mouse_die.enter(0);w.canvas.draw();point=w.ax.transData.transform(w.case['dies'][0].centroid.coords[0]);lim=w.ax.get_xlim()
e=MouseEvent('scroll_event',w.canvas,*point,button='up',step=1,key=None);w.canvas.callbacks.process('scroll_event',e);wait()
assert w.ax.get_xlim()==lim;np.testing.assert_allclose(w.shown_states[0,2],state[0,2]+np.deg2rad(.1))
before=w.shown_states.copy();e=MouseEvent('scroll_event',w.canvas,*point,button='up',step=1,key='ctrl');w.canvas.callbacks.process('scroll_event',e);wait()
np.testing.assert_array_equal(w.shown_states,before);assert np.ptp(w.ax.get_xlim())<np.ptp(lim)
# A real QWheelEvent may carry Ctrl only in guiEvent, with no key event.
before=w.shown_states.copy();lim=w.ax.get_xlim();center=w.canvas.rect().center();pos=QtCore.QPointF(center)
wheel=QtGui.QWheelEvent(pos,QtCore.QPointF(w.canvas.mapToGlobal(center)),QtCore.QPoint(),QtCore.QPoint(0,120),Qt.NoButton,Qt.ControlModifier,Qt.NoScrollPhase,False)
app.sendEvent(w.canvas,wheel);wait();np.testing.assert_array_equal(w.shown_states,before);assert np.ptp(w.ax.get_xlim())<np.ptp(lim)
QTest.keyClick(w.canvas,Qt.Key_Escape);wait();assert w.mouse_die.owner is None
# Marker diameter in points stays fixed; in micrometres follows scale.
w.setting_fields['contact_marker_size'].setValue(12);wait()
a=w.marker_size('contact');x=w.ax.get_xlim();y=w.ax.get_ylim()
w.ax.set_xlim(np.mean(x)+(np.array(x)-np.mean(x))/2);w.ax.set_ylim(np.mean(y)+(np.array(y)-np.mean(y))/2);w.plot_layout(preserve=True);wait();assert w.marker_size('contact')==a
w.setting_fields['contact_marker_absolute'].setChecked(True);wait();a=w.marker_size('contact')
x=w.ax.get_xlim();y=w.ax.get_ylim();w.ax.set_xlim(np.mean(x)+(np.array(x)-np.mean(x))/2);w.ax.set_ylim(np.mean(y)+(np.array(y)-np.mean(y))/2);w.plot_layout(preserve=True);wait()
np.testing.assert_allclose(w.marker_size('contact'),4*a,rtol=.03)
for prefix in ('contact','unadapted'):
    color=w.setting_fields[prefix+'_marker_color'];color.setValue('#7954a6');color.valueChanged.emit();wait()
    assert getattr(w.view_settings,prefix+'_marker_color')=='#7954a6'
# All-support overlay + separately styled and layer-bound target envelopes.
w.fit_plot();w.plot_layout(preserve=True);w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait()
assert w.view_settings.probability_show_ranges
original_cloud=w.probability_data
w.setting_fields['probability_show_ranges'].setChecked(False);wait();w.setting_fields['probability_show_ranges'].setChecked(True);wait()
assert w.probability_data is original_cloud
contours=[a for a in w.probability_artists if hasattr(a,'_coverage_level')]
assert {a._coverage_level for a in contours}=={1,3,6}
w.visible_boxes['4/0'].setChecked(False);wait();assert not any(a.get_visible() for a in w.probability_artists if a._via_layer=='4/0')
w.visible_boxes['4/0'].setChecked(True);wait();shot('pivot_probability')
# Selected distribution persists on switching 3 -> 0 and is saved with project.
w.target_sigma.setCurrentText('3');wait();sigma=statistical_state(w.params().for_die(0),2)[1]
w.target_sigma.setCurrentText('0');wait();np.testing.assert_array_equal(statistical_state(w.params().for_die(0),2)[1],sigma)
assert w.result['target_failure']==0 and w.result['parameters']['reference_nsigma']==3
html,_=report_document(w.result);assert '旋转中心' in html and 'κ' in html
with patch.object(W.QInputDialog,'getText',return_value=('V14界面验证',True)):w.project_path=None;w.save_project()
path=w.project_path;saved=json.loads(path.read_text());assert saved['parameters']['reference_nsigma']==3 and saved['parameters']['die_pivots'][1]==['custom',150.,-100.]
w.load_project(path);wait();assert w.params().target_nsigma==0 and w.params().reference_nsigma==3
w.controls.setCurrentIndex(2);wait();shot('settings')
# common dropdown applies when independent amplitude switch is off.
w.per_die_toggle.setChecked(False);wait();w.pivot_editor.combo.setCurrentIndex(w.pivot_editor.combo.findData('bottom_right'));wait()
assert w.params().rotation_center=='bottom_right'
w.close();app.processEvents();wait();tmp.cleanup()
out=dict(status='PASS',ctrl_wheel_zooms_only=True,plain_wheel_rotates_only=True,pivot_controls_and_save=True,absolute_marker_scale=True,range_levels=[1,3,6],zero_retains_last_scale=True,no_errors=not errors)
(ROOT/'results/gui_V14.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
