"""Real Qt interaction, Jobs, layer order, asynchronous previews and pixels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,json,time,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from matplotlib.backend_bases import MouseEvent,MouseButton
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W,QtCore,QtGui
from die_shift_studio.qt_compat import Qt
from PyQt5.QtTest import QTest
from die_shift_studio.core import read_gds
from die_shift_studio.report import report_document
from die_shift_studio.jobs import atomic_save
app=W.QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    window=MainWindow(jobs_dir=Path(tmp)/'Jobs');window.show();errors=[];window.error=lambda message:errors.append(str(message))
    def wait_idle():
        end=time.monotonic()+240
        while time.monotonic()<end:
            app.processEvents()
            if window.width_yield.worker is None and not window.width_yield.timer.isActive() and window.worker is None and window.preview_worker is None and window.preview_pending is None and not window.coverage_timer.isActive() and not window._coverage_pending and not window.viewport_timer.isActive() and not window.transition.timer.isActive() and not window.issue_navigation.isActive():break
            time.sleep(.003)
        assert window.worker is None and window.preview_worker is None and window.preview_pending is None
        assert not errors,errors
        app.processEvents()
    window.load_gds(ROOT/'examples/示例C_多芯片晶圆接口.gds');wait_idle()
    assert window.job_combo.itemText(0)=='' and window.project_path is None
    assert window.windowTitle().startswith('Die Shift Analyze')
    assert all(b.text()!='打开工程' for b in window.findChildren(W.QPushButton))
    assert not hasattr(window,'stage_boxes') and window.layer_table.columnCount()==4
    assert all(role=='不参与' for role in window.roles().values())
    for key,role in json.loads((ROOT/'examples/示例C_多芯片晶圆接口.json').read_text())['roles'].items():window.role_boxes[key].setCurrentText(role)
    wait_idle()
    assert window.roles()['30/0']=='运动过孔'
    assert window.ordered_layers()==list(window.data.layers)
    # Apply the example's explicit physical stack as a manual drag sequence.
    desired=json.loads((ROOT/'examples/示例C_多芯片晶圆接口.json').read_text())['layer_order'];header=window.layer_table.verticalHeader()
    for i,key in enumerate(desired):header.moveSection(header.visualIndex(window._layer_keys.index(key)),i)
    window.layer_moved()
    assert not window.visible_boxes['90/0'].isChecked()
    assert [window.controls.tabText(i) for i in range(window.controls.count())]==['偏移','工艺','设置']
    assert any('方案1' in w.text() and w.text().count('\n')==2 for w in window.findChildren(W.QLabel))
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())]==['结果图形','结果分析','报告']
    assert [window.preview_tabs.tabText(i) for i in range(2)]==['极端情况','信息']
    assert window.preview_tabs.tabText(2).startswith('型变校验')
    assert not hasattr(window,'preview_status') and window.layer_table.verticalHeader().isHidden()
    assert window.gds_label.text().endswith('示例C_多芯片晶圆接口.gds')
    # Drag starts in the count cell, not a row-number header. Preview reorders before release.
    header=window.layer_table.verticalHeader();before=window.ordered_layers();viewport=window.layer_table.viewport()
    x=window.layer_table.columnViewportPosition(2)+15
    row0=window._layer_keys.index('30/0');row1=window._layer_keys.index('31/0')
    y0=header.sectionViewportPosition(row0)+header.sectionSize(row0)//2;y1=header.sectionViewportPosition(row1)+header.sectionSize(row1)-3
    QTest.mousePress(viewport,Qt.LeftButton,pos=QtCore.QPoint(x,y0))
    for yy in range(y0+1,y1+1,3):
        local=QtCore.QPointF(x,yy);glob=QtCore.QPointF(viewport.mapToGlobal(QtCore.QPoint(x,yy)))
        event=QtGui.QMouseEvent(QtCore.QEvent.MouseMove,local,glob,Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
        W.QApplication.sendEvent(viewport,event);app.processEvents()
    assert window.ordered_layers()[3:5]==['31/0','30/0']
    QTest.mouseRelease(viewport,Qt.LeftButton,pos=QtCore.QPoint(x,y1));app.processEvents()
    with patch.object(W.QInputDialog,'getText',return_value=('测试工程',True)) as dialog:
        window.save_project();assert dialog.call_count==1
    saved=window.project_path;assert saved.parent==window.jobs_dir and saved.exists()
    assert json.loads(saved.read_text())['layer_order'][3:5]==['31/0','30/0']
    with patch.object(W.QInputDialog,'getText',side_effect=AssertionError('Existing jobs must overwrite')):window.save_project()
    window.job_combo.setCurrentIndex(0);assert window.project_path is None
    window.job_combo.setCurrentIndex(window.job_combo.findData(str(saved)));wait_idle();assert window.project_path==saved
    assert window.ordered_layers()[3:5]==['31/0','30/0']
    header=window.layer_table.verticalHeader();header.moveSection(3,4);window.layer_moved();app.processEvents()
    # External Jobs additions appear without reopening the program.
    other=window.jobs_dir/'另一工程.json';atomic_save(other,json.loads(saved.read_text()));app.processEvents();window.refresh_jobs()
    assert window.job_combo.count()==3
    assert not hasattr(window,'interdie') and 'transition_length_um' not in window.fields
    window.run_evaluation();wait_idle()
    assert window.result['issues'] and window.result['schemes'][2]['contact_status']=='存在接触超限'
    candidates=[s for s in window.result['schemes'] if s['status']=='初筛候选']
    if candidates:
        chosen=window.result['schemes'][window.result['recommendation']['scheme']-1]
        assert chosen['gamma']<=min(s['gamma'] for s in candidates)+1e-9
    else:assert window.result['recommendation']['scheme'] is None
    assert window.problem_filter.isChecked();window.problem_filter.setChecked(False);wait_idle()
    assert window.extreme_table.rowCount()==window.focus_extremes.total+1 and window.extremes.total==728
    window.grid_mode.setCurrentIndex(0);wait_idle();assert window.extreme_table.rowCount()==257
    assert all(window.extreme_table.item(0,j).text()=='' for j in range(window.extreme_table.columnCount()))
    assert not window.detail.toPlainText() # report is lazy
    # Rapid requests must show only the newest selection.
    for row in (1,2,3,7):window.extreme_table.setCurrentCell(row,0)
    wait_idle();assert window.active_preview==6
    np.testing.assert_allclose(window.shown_states,window.extremes[6]['states'])
    assert window.preview_applied and window.ax.get_legend() is not None and window.ax.get_legend().get_visible()
    artists=dict(window.layer_artists)
    assert all(label.get_visible() for label in window.die_labels)
    for i,label in enumerate(window.die_labels):
        info=window.case['infos'][i];np.testing.assert_allclose(label.get_position(),np.array([info['cx'],info['cy']])+window.shown_states[i,:2])
    assert window.layer_artists['20/0'].get_facecolors().size and window.layer_artists['20/0'].get_alpha()==1
    assert window.layer_artists['40/0'].get_alpha()==.70
    assert sum(window.histogram_data['magnitude_counts'])==len(window.connector_segments)==64
    assert sum(window.histogram_data['direction_counts'])+window.histogram_data['zero_count']==64
    for key in ('10/0','30/0'):
        assert np.array_equal(window.current_frame['layers'][key]['centers'].shape,window.geometry_cache.layers[key]['centers'].shape)
    original=window.connector_segments.copy();window.visible_boxes['30/0'].setChecked(False)
    assert len(window.connector_segments)==0 and sum(window.histogram_data['magnitude_counts'])==64
    assert window.layer_artists['30/0'] is artists['30/0'] and not window.layer_artists['30/0'].get_visible()
    window.visible_boxes['30/0'].setChecked(True);np.testing.assert_array_equal(window.connector_segments,original)
    window.canvas.draw();anchor=window.ax.transData.transform((0,0));span=np.ptp(window.ax.get_xlim())
    e=MouseEvent('scroll_event',window.canvas,*anchor,button='up',step=1);window.canvas.callbacks.process(e.name,e)
    assert np.ptp(window.ax.get_xlim())<span and not any(label.get_visible() for label in window.die_labels) and not window.legend.get_visible()
    e=MouseEvent('scroll_event',window.canvas,*anchor,button='down',step=-1);window.canvas.callbacks.process(e.name,e)
    assert all(label.get_visible() for label in window.die_labels) and window.legend.get_visible()
    before=window.ax.get_xlim()
    for name,xy in [('button_press_event',anchor),('motion_notify_event',anchor+[30,20]),('button_release_event',anchor+[30,20])]:
        e=MouseEvent(name,window.canvas,*xy,button=MouseButton.MIDDLE);window.canvas.callbacks.process(name,e)
    assert not np.allclose(window.ax.get_xlim(),before)
    # A single click, including a repeated click on an already-selected row, locates the point.
    window.extreme_table.setCurrentCell(0,0);wait_idle()
    window.preview_tabs.setCurrentIndex(0);window.issue_scheme.setCurrentIndex(2);app.processEvents()
    assert window.filtered_issues and all(q['source_net']!=q['foreign_net'] for q in window.filtered_issues)
    target=window.issue_table.visualItemRect(window.issue_table.item(0,0)).center()
    QTest.mouseClick(window.issue_table.viewport(),Qt.LeftButton,pos=target);wait_idle()
    expected=np.mean([window.anchor_position(a) for a in window.filtered_issues[0]['anchors']],axis=0)
    np.testing.assert_allclose([np.mean(window.ax.get_xlim()),np.mean(window.ax.get_ylim())],expected)
    window.ax.set_xlim(0,100);QTest.mouseClick(window.issue_table.viewport(),Qt.LeftButton,pos=target);wait_idle()
    assert abs(np.mean(window.ax.get_xlim())-expected[0])<1e-7
    window.canvas.draw();x,y=window.ax.transData.transform(expected);rgba=np.asarray(window.canvas.buffer_rgba());h,w=rgba.shape[:2]
    crop=rgba[max(0,h-int(y)-15):min(h,h-int(y)+16),max(0,int(x)-15):min(w,int(x)+16),:3]
    assert np.count_nonzero((crop[:,:,0]>150)&(crop[:,:,1]<130)&(crop[:,:,2]<140))>3
    window.grab().save(str(ROOT/'figures/software_V14_window.png'))
    # Manual state, probability overlay, control zones and layer order.
    snapshot=window.result
    window.manual_fields['dx'].setValue(120);window.manual_fields['dy'].setValue(10);window.manual_fields['angle'].setValue(1)
    window.manual_die.setCurrentIndex(0);window.apply_manual();wait_idle()
    np.testing.assert_allclose(window.shown_states[0],[120,10,np.deg2rad(1)])
    assert len(window.zone_artists)==2 and window.current_frame['contact']['regions']
    assert window.layer_artists['20/0'].get_zorder()<window.layer_artists['31/0'].get_zorder()<window.layer_artists['10/0'].get_zorder()
    window.probability_toggle.setChecked(True);wait_idle()
    assert window.probability_artists and all(abs(q['integrated_area_um2']/q['via_area_um2']-1)<.04 for q in window.probability_data['maps'])
    assert window.current_frame['contact']['issues']
    issue=next(q for q in window.current_frame['contact']['issues'] if q['scheme']==3)
    center=np.mean([issue['origin'],issue['point']],axis=0)
    window.ax.set_xlim(center[0]-250,center[0]+250);window.ax.set_ylim(center[1]-210,center[1]+210)
    window.preview_tabs.setCurrentIndex(0);app.processEvents();window.canvas.draw()
    window.grab().save(str(ROOT/'figures/software_V14_contact_detail.png'))
    window.figure.savefig(ROOT/'figures/V14_contact_detail.png',dpi=240,bbox_inches='tight')
    assert window.current_frame['contact']['intrusions']
    assert any(a.get_alpha()==window.view_settings.intrusion_alpha for a in window.issue_artists)
    assert all(0<a.get_alpha()<=.5 for a in window.probability_artists)
    # Space Map is independent of probability and follows its source layer.
    assert not window.space_toggle.isChecked() and window.space_toggle.text()=='Space Map'
    window.space_toggle.setChecked(True);wait_idle();assert len(window.space_artists)==2
    assert all(q['layers']==['30/0'] for q in window.space_data['maps'])
    viewport_visibility=[a.get_visible() for a in window.space_artists]
    assert any(viewport_visibility)  # Off-screen DIE maps are deliberately culled.
    window.visible_boxes['30/0'].setChecked(False)
    assert not any(a.get_visible() for a in window.space_artists)
    assert not window.space_colorbar.ax.get_visible() and not window.connector_segments.size
    window.visible_boxes['30/0'].setChecked(True)
    assert [a.get_visible() for a in window.space_artists]==viewport_visibility
    window.probability_toggle.setChecked(False);wait_idle()
    assert [a.get_visible() for a in window.space_artists]==viewport_visibility
    window.fit_plot();wait_idle();window.canvas.draw()
    assert all(a.get_visible() for a in window.space_artists)
    assert window.space_colorbar.ax.bbox.y1<window.ax.bbox.y0
    for width,height in [(1540,1000),(1800,820),(1200,1050)]:
        window.resize(width,height);app.processEvents();window.canvas.draw();app.processEvents()
        ratio=window.ax.bbox.width/window.ax.bbox.height
        assert abs(np.ptp(window.ax.get_xlim())/np.ptp(window.ax.get_ylim())/ratio-1)<1e-6
        assert window.ax.get_position().width>.88 # axes fill the allocated window width
    window.resize(1540,1000);app.processEvents();window.fit_plot();window.canvas.draw()
    window.grab().save(str(ROOT/'figures/software_V14_space_map.png'))
    window.figure.savefig(ROOT/'figures/V14_space_map.png',dpi=220)
    # Per-layer appearance and numerical settings persist without discarding analysis.
    from die_shift_studio.layer_controls import LayerStyleDialog
    with patch('die_shift_studio.gui.run_dialog',return_value=1),patch.object(LayerStyleDialog,'value',return_value=dict(color='#95b8d1',alpha=.35,fill=True,width=.8,linestyle='--')):
        logical=window._layer_keys.index('40/0');point=window.layer_table.visualItemRect(window.layer_table.item(logical,1)).center()
        QTest.mouseClick(window.layer_table.viewport(),Qt.LeftButton,pos=point);app.processEvents()
    assert window.layer_styles['40/0']['alpha']==.35 and window.layer_artists['40/0'].get_alpha()==.35
    window.setting_fields['space_cmap'].setCurrentIndex(window.setting_fields['space_cmap'].findData('cividis'));wait_idle()
    assert window.space_artists[0].get_cmap().name=='cividis' and window.result is snapshot
    window.write_project(saved);saved_data=json.loads(saved.read_text());assert saved_data['layer_styles']['40/0']['alpha']==.35
    assert saved_data['settings']['space_cmap']=='cividis'
    # Parameter edits preserve the computed snapshot while rebuilding live regions.
    window.fields['annulus_um'].setValue(12);wait_idle();assert window.result is snapshot and window.dirty
    assert window.engine.p.annulus_um==12
    window.target_sigma.setCurrentText('∞');wait_idle();assert window.space_artists and window.space_colorbar is not None;assert window.result is not snapshot and window.params().target_nsigma==float('inf')
    window.write_project(saved);assert json.loads(saved.read_text())['parameters']['target_nsigma']=='Infinity'
    window.target_sigma.setCurrentText('6');wait_idle()
    # Wheel events do not change parameter controls or project selection.
    for widget in (window.fields['annulus_um'],window.target_sigma,window.job_combo,window.manual_die):
        before_value=widget.value() if hasattr(widget,'value') else widget.currentText()
        widget.setFocus();event=QtGui.QWheelEvent(QtCore.QPointF(10,10),QtCore.QPointF(widget.mapToGlobal(QtCore.QPoint(10,10))),QtCore.QPoint(),QtCore.QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        app.sendEvent(widget,event);app.processEvents()
        assert (widget.value() if hasattr(widget,'value') else widget.currentText())==before_value
    # Invalid geometry is annotated and excluded, including hidden-layer behaviour.
    from die_shift_studio.geometry_errors import sanitize_data
    window.data.layers['90/0'].append(np.array([[0,0],[120,120]])+[-5700,2000])
    sanitize_data(window.data);window.visible_boxes['90/0'].setChecked(True);window.plot_layout()
    assert len(window.invalid_artists)==1
    window.visible_boxes['90/0'].setChecked(False);assert len(window.invalid_artists)==0
    window.visible_boxes['90/0'].setChecked(True);assert len(window.invalid_artists)==1
    window.plot_layout();window.preview_tabs.setCurrentIndex(0);app.processEvents();window.grab().save(str(ROOT/'figures/software_V14_preview.png'))
    window.tabs.setCurrentWidget(window.analysis_page);app.processEvents();window.grab().save(str(ROOT/'figures/software_V14_histograms.png'))
    window.tabs.setCurrentWidget(window.detail);app.processEvents();assert not errors,errors;assert '输入' in window.detail.toPlainText() and '相邻' in window.detail.toPlainText()
    html,resources=report_document(window.result,window.evaluation_motions)
    for uri,png in resources.items():assert window.detail.document().resource(QtGui.QTextDocument.ImageResource,QtCore.QUrl(uri))==QtGui.QImage.fromData(png,'PNG')
    window.grab().save(str(ROOT/'figures/software_V14_report.png'))
    window.target_sigma.setCurrentText('5.5');wait_idle();assert window.result['parameters']['target_nsigma']==6 and window.detail.toPlainText()
    assert not any(b.text()=='检查GDS图层与边框' for b in window.findChildren(W.QPushButton))
    assert not hasattr(window,'log_label') and 'via_diameter_um' not in window.fields
    window.close();app.processEvents()
# Embedded controls also initiate row dragging without toggling or opening a popup.
from die_shift_studio.gui import table
from die_shift_studio.layer_controls import LayerDrag
probe=table(['显示','用途']);probe.setRowCount(3);probe.resize(420,240)
filter_=LayerDrag(probe);probe.verticalHeader().setSectionsMovable(True);probe.show();embedded=[]
for row in range(3):
    checkbox=W.QCheckBox();checkbox.setChecked(True);combo=W.QComboBox();combo.addItems(['运动RDL','固定RDL'])
    probe.setCellWidget(row,0,checkbox);probe.setCellWidget(row,1,combo);filter_.watch(checkbox);filter_.watch(combo);embedded.append((checkbox,combo))
app.processEvents()
for widget,source,target in [(embedded[0][0],0,1),(embedded[2][1],2,0)]:
    local=widget.rect().center();QTest.mousePress(widget,Qt.LeftButton,pos=local)
    end=QtCore.QPoint(20,probe.rowViewportPosition(target)+probe.rowHeight(target)//2);glob=probe.viewport().mapToGlobal(end);point=widget.mapFromGlobal(glob)
    move=QtGui.QMouseEvent(QtCore.QEvent.MouseMove,QtCore.QPointF(point),QtCore.QPointF(glob),Qt.NoButton,Qt.LeftButton,Qt.NoModifier);app.sendEvent(widget,move)
    assert filter_.dragging
    QTest.mouseRelease(widget,Qt.LeftButton,pos=point);app.processEvents()
    assert not filter_.dragging
assert probe.verticalHeader().visualIndex(0)!=0 and probe.verticalHeader().visualIndex(2)!=2
assert all(checkbox.isChecked() and combo.currentIndex()==0 for checkbox,combo in embedded)
probe.close();app.processEvents()
print('GUI_SMOKE_PASS: Jobs and stack drag; latest-state preview; manual DIE offsets; control boundaries; foreign-network contacts/location; layer z-order; default-view legend; via-area occupancy; preserved results; infinity serialization; wheel protection; ignored-geometry layer markers; typeset report; full-DIE Space Map; source visibility; window aspect ratio')
