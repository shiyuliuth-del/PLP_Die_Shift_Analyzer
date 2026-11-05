"""Actual controls, event styles, saved Jobs, and cached nominal return."""
import json,os,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import gdstk
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,BINDING
from die_shift_studio.width_overlay import low_risk
from die_shift_studio.width_analysis import WidthAnalyzer
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();root=Path(tmp.name);w=MainWindow(jobs_dir=root/'Jobs')
w.resize(1400,900);w.show();errors=[];w.error=lambda exc:errors.append(str(exc))
left=getattr(Qt,'MouseButton',Qt).LeftButton

def wait():
    until=time.monotonic()+180
    while time.monotonic()<until:
        app.processEvents()
        if not any((w.width_yield.worker,w.width_yield.timer.isActive(),w.worker,w.preview_workers,w.preview_pending,w.motion_worker,w.motion_pending,
                    w.hardware_worker,w.transition.timer.isActive(),w.viewport_timer.isActive(),
                    w.coverage_timer.isActive(),w._coverage_pending,w.mouse_die.timer.isActive(),w.mouse_die.idle.isActive())):break
        time.sleep(.005)
    else:raise AssertionError('preview timeout')
    assert not errors,errors
    assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-1600:]

def load(name,source,overrides):
    record=json.loads((ROOT/'Jobs'/(source+'.dshift.json')).read_text())
    record['gds_path']=str(ROOT/'examples'/(source+'.gds'));record.pop('sha256',None)
    record['parameters'].update(overrides);record.setdefault('settings',{}).update(animation_enabled=False)
    p=root/(name+'.dshift.json');p.write_text(json.dumps(record));w.load_project(p);wait();w.run_evaluation();wait()

params=dict(per_die_enabled=True,die_bounds=[[35,0,0],[0,0,0]],line_width_um=9,line_space_um=13,target_nsigma=2)
load('width','示例O_运动网络三线路通道',params)
assert w.grid_mode.currentText()=='重点组合' and '共 ' in w.page_summary.text() and '第 1/' in w.page_summary.text()
w.manual_die.setCurrentIndex(0);w.manual_fields['dx'].setValue(35);QTest.mouseClick(w.manual_button,left);wait()
assert np.array_equal(w.shown_states[:,0],[35,0])
data=w.current_frame['width_analysis'];raw=data['counts'].copy()
rare=[r for r in w.width_rows if low_risk(r)]
assert rare and all(r['event_probability']['upper']<=r['event_probability']['threshold'] for r in rare)
assert any(c[3]<.5 for c in w.width_overlay.boxes.get_edgecolors())
with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('display-only control rebuilt geometry')):
    w.setting_fields['show_low_width'].setChecked(False);wait()
    assert not any(map(low_risk,w.width_rows)) and w.current_frame['width_analysis']['counts']==raw
    w.setting_fields['width_label_decimals'].setValue(4);wait();w.setting_fields['show_low_width'].setChecked(True);wait()
    assert any(map(low_risk,w.width_rows))
    w.controls.setCurrentIndex(2);w.setting_sections['宽度分析 · 元素显示'].setChecked(True);app.processEvents()
    size=w.setting_fields['width_label_font_pt'];digits=w.setting_fields['width_label_decimals']
    assert size.isVisible() and digits.isVisible() and size.pos().x()<digits.pos().x()
    w.controls.setCurrentIndex(0);app.processEvents()
    assert all(a.get_text().split('预期收窄:')[-1].split('微米')[0][-5]=='.' for a in w.width_overlay.labels if a.get_visible())
w.target_sigma.setCurrentText('6');wait()
assert np.array_equal(w.shown_states[:,0],[35,0]) and not any(map(low_risk,w.width_rows))
QTest.mouseClick(w.restore_button,left);wait()
assert w.extreme_table.currentRow()==0 and not w.shown_states.any() and w.active_preview is None
w.mouse_die.enter(0);w.mouse_die.submit(np.array([[8.,1.,.01],[0.,0.,0.]]));wait()
QTest.mouseClick(w.restore_button,left);wait()
assert w.mouse_die.owner is None and w.manual_states is None and not w.shown_states.any()
QTest.mouseClick(w.restore_button,left);wait();assert not w.shown_states.any()
w.setting_fields['show_low_width'].setChecked(False);w.setting_fields['show_low_contact'].setChecked(False)
saved=root/'display.dshift.json';w.write_project(saved);w.load_project(saved);wait()
assert not w.setting_fields['show_low_contact'].isChecked() and not w.setting_fields['show_low_width'].isChecked()
assert w.setting_fields['width_label_decimals'].value()==4

lib=gdstk.Library();cell=lib.new_cell('TOP')
for layer,b in [(1,(-1500,-1500,1500,1500)),(2,(-1000,-1000,1000,1000)),
                (10,(-5,-5,5,5)),(40,(-20,-10,20,10)),(40,(50,-10,70,10)),(40,(-34,-10,-26,10))]:
    cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
gds=root/'contact.gds';lib.write_gds(gds)
record=dict(schema_version=5,gds_path=str(gds),top_cell='TOP',roles={'1/0':'封装边框','2/0':'芯片边框','10/0':'运动过孔','40/0':'固定RDL'},
        layer_order=['10/0','40/0'],parameters=dict(single_bound_x_um=35,single_bound_y_um=0,bound_angle_deg=0,
        annulus_um=10,line_space_um=5,target_nsigma=2),settings=dict(animation_enabled=False))
job=root/'contact.dshift.json';job.write_text(json.dumps(record));w.load_project(job);wait();w.run_evaluation();wait()
assert any(r.get('low_probability') for r in w.filtered_issues)
w.manual_fields['dx'].setValue(34);QTest.mouseClick(w.manual_button,left);wait()
assert w.filtered_issues and all(r['kind']=='已发生接触' and r['low_probability'] for r in w.filtered_issues)
assert w.issue_artists[0].get_paths() and w.issue_artists[0].get_facecolors()[0,3]<w.view_settings.intrusion_alpha
assert w.issue_artists[1].get_edgecolors()[0,3]<.5
entries=w.current_frame['contact']['issues'];w.setting_fields['show_low_contact'].setChecked(False);wait()
assert not w.filtered_issues and not w.issue_artists[1].get_visible() and not w.issue_artists[0].get_visible()
assert entries and w.current_frame['contact']['issues']==entries
w.setting_fields['show_low_contact'].setChecked(True);w.target_sigma.setCurrentText('6');wait()
assert w.filtered_issues and all(not r.get('low_probability') for r in w.filtered_issues)
assert w.issue_artists[1].get_edgecolors()[0,3]==1
# Both single-target events are below the budget although their union fails.
lib=gdstk.Library();cell=lib.new_cell('TOP')
for layer,b in [(1,(-1500,-1500,1500,1500)),(2,(-1000,-1000,1000,1000)),(10,(-5,-5,5,5)),
                (40,(-20,-10,20,10)),(40,(48,-10,70,10)),(40,(-70,-10,-48,10))]:
    cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
paired=root/'paired.gds';lib.write_gds(paired);record['gds_path']=str(paired);job.write_text(json.dumps(record))
w.load_project(job);wait();w.run_evaluation();wait()
assert w.result['issues'] and len(w.filtered_issues)==2 and all(q['low_probability'] for q in w.filtered_issues)
w.setting_fields['show_low_contact'].setChecked(False);wait()
assert not w.filtered_issues and w.result['issues'] and not w.issue_artists[1].get_visible()
w.close();app.processEvents();tmp.cleanup()
print('GUI_V27_PASS focused pages, compact controls, manual/M restore, Job migration, 2/6 sigma independent contact/width styles and retained binary outcomes')
