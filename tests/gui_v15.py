"""V15 Qt settings, combined safe-range chart, cached coverage switch and math report."""
import os,sys,time,json,tempfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.report import report_document
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1540,960);w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):break
        time.sleep(.003)
    else:raise AssertionError('GUI V15 timeout')
    assert not errors,errors
w.load_project(ROOT/'Jobs/示例G_小偏移与三段追溯.dshift.json');wait();w.run_evaluation();wait()
assert not hasattr(w,'safe_range_table')
assert len(w.safe_range_figure.axes)==1 and len(w.safe_range_plot_data)==6
for i,row in enumerate(w.result['safe_ranges']['rows']):
    assert row['certified']
    for j in range(3):
        item=w.safe_range_plot_data[i*3+j];assert item['minimum']==row['minimum_display'][j] and item['maximum']==row['maximum_display'][j]
        assert item['label'] in [t.get_text() for t in w.safe_range_figure.axes[0].texts]
assert w.scheme_table.horizontalHeaderItem(2).text()=='U'
safe=w.result['safe_ranges']
with patch('die_shift_studio.safe_ranges.safe_ranges',side_effect=AssertionError('Unnecessary same-geometry recompute')):
    w.target_sigma.setCurrentText('3');wait()
assert w.result['safe_ranges'] is safe
w.tabs.setCurrentWidget(w.analysis_page);wait();w.grab().save(str(ROOT/'figures/software_V15_analysis.png'))
for key,value in [('deformation_iterations',8),('deformation_local_steps',7),('deformation_growth',1.7),('deformation_route_candidates',48),('safe_box_precision',.005),('safe_box_starts',4)]:
    w.setting_fields[key].setValue(value);wait();assert getattr(w.view_settings,key)==value
w.setting_fields['deformation_route_enabled'].setChecked(False);wait();assert not w.setting_fields['deformation_general_bands'].isEnabled()
w.setting_fields['deformation_route_enabled'].setChecked(True);wait();assert w.setting_fields['deformation_general_bands'].isEnabled()
with patch.object(W.QInputDialog,'getText',return_value=('V15参数与安全范围',True)):w.project_path=None;w.save_project()
q=json.loads(w.project_path.read_text());assert q['settings']['deformation_iterations']==8 and q['settings']['safe_box_precision']==.005
# Formula resources must all decode; no textual fraction fallback.
html,images=report_document(w.result);assert '连续无异常接触范围' in html and '三方案空间判据' in html and images
w.tabs.setCurrentWidget(w.detail);wait();w.grab().save(str(ROOT/'figures/software_V15_report.png'))
w.controls.setCurrentIndex(2);w.setting_fields['deformation_iterations'].setFocus();wait()
# Ensure the scroll area exposes the modified controls.
for ancestor in [w.setting_fields['deformation_iterations'].parentWidget(),w.controls.widget(2)]:
    if hasattr(ancestor,'ensureWidgetVisible'):ancestor.ensureWidgetVisible(w.setting_fields['deformation_iterations'])
w.grab().save(str(ROOT/'figures/software_V15_settings.png'))
w.close();app.processEvents();wait();tmp.cleanup()
out=dict(status='PASS',safe_box_combined_chart=True,coverage_switch_reuses_safe_certificate=True,iteration_controls=True,settings_saved=True,report_math_resources=len(images),errors=errors)
(ROOT/'results/gui_V15.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print('GUI_V15_PASS',out)
