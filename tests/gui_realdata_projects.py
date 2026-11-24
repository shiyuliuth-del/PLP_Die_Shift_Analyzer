"""Real Qt project folder workflow and local IPC project linking, no mocked geometry."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from die_shift_studio.qt_compat import QtWidgets as W, QtCore, QtGui, BINDING
if BINDING == 'PyQt6':
    from PyQt6.QtTest import QTest
else:
    from PyQt5.QtTest import QTest
from RealDataAnalyse.gui import Window, ProgressBar
from RealDataAnalyse.transport import Transport
from RealDataAnalyse.storage import Ledger, configure_logging, sha256

ROOT = Path(__file__).resolve().parents[1]
app = W.QApplication.instance() or W.QApplication([])

def wait(window, timeout=240):
    until = time.monotonic() + timeout
    while window.worker and time.monotonic() < until:
        app.processEvents(); time.sleep(.015)
    assert window.worker is None, 'Qt worker failed to finish safely'
    assert not window.progress.failed, window.response.toPlainText()
    app.processEvents()

with tempfile.TemporaryDirectory(prefix='rda-project-ui-') as temp:
    root = Path(temp).resolve()
    folder = root/'Jobs'/'GD55 项目'
    shutil.copytree(ROOT/'Jobs/GD55', folder)
    before = sha256(folder/'GD55_1105.gds')
    endpoint = root/'ipc'/'endpoint.json'
    w = Window(); w.endpoint.setText(str(endpoint)); w.show(); app.processEvents()
    try:
        assert not w.log_check.isChecked() and w.log_view.isHidden()
        assert all(field.isHidden() for field in (w.project, w.database, w.output))
        assert not any(label.text() in ('Die Shift Analyze工程', '任务记录', '最终OAS') for label in w.findChildren(W.QLabel))
        w.log_check.click(); assert not w.log_view.isHidden()
        w.log_check.click(); assert w.log_view.isHidden()
        assert not any('生成GD55' in x.text() for x in w.findChildren(W.QPushButton))
        assert w.connect_button.text() == '连接状态' and w.start_button.text() == '启动预演软件'
        w.open_folder(folder)
        wait(w)
        assert w.workers.value() == w.memory.value() == 0
        assert [field.value() for field in w.bypass_limits] == [0,0,0]
        assert not w.bypass_enabled.text() and not w.bypass_enabled.isChecked()
        assert all(field.decimals() == 3 and not field.isEnabled() for field in w.bypass_limits)
        assert all(field.decimals() == 3 and not field.isEnabled() for field in w.skip_drc_limits)
        assert not w.skip_drc_enabled.isChecked()
        assert [w.tabs.tabText(i) for i in range(2)] == ['信息读取','任务运行']
        assert w.ignore_measurement_failure.parentWidget() is w.task_tab
        assert w.excel_section.isChecked() and w.mark_section.isChecked()
        assert w.mark_table.isVisible() and w.template.isVisible() and w.mark_table.rowCount() == 0
        w.mark_section.click()
        assert not w.mark_table.isVisible() and not w.template.isVisible()
        w.mark_section.click()
        w.excel_section.click()
        assert not w.file_view.isVisible() and not w.add_files.isVisible()
        w.excel_section.click()
        assert w.mapping_section.text() == 'Excel列映射' and not w.mapping_section.isChecked()
        assert not w.profile.isVisible()
        w.mapping_section.click()
        assert w.profile.isVisible()
        assert not w.conversation_timer.isActive()
        w.conversation.record('send',endpoint,{'id':'hidden-tab'})
        assert not w.conversation.drain() and not w.conversation_view.toPlainText()
        w.tabs.setCurrentWidget(w.conversation_tab)
        app.processEvents()
        assert w.conversation_timer.isActive()
        w.conversation.record('receive',endpoint,{'id':'visible-tab'})
        w.flush_conversation()
        assert 'visible-tab' in w.conversation_view.toPlainText()
        w.tabs.setCurrentIndex(0)
        app.processEvents()
        assert not w.conversation_timer.isActive()
        w.conversation.record('send',endpoint,{'id':'background-only'})
        assert 'background-only' not in w.conversation_view.toPlainText()
        trace = (folder/'RealDataAnalyse/logs/IPC-conversation.log').read_text('utf8')
        assert all(token in trace for token in ('hidden-tab','visible-tab','background-only'))
        assert w.connect_button.property('connected') is True
        assert w.start_button.property('connected') is True
        assert w.file_view.rowCount() == 3 and w.profile.rowCount() == 9
        assert w.log_check.text() == ''
        assert w.log_check.geometry().right() < w.progress.geometry().left()
        assert abs(w.log_check.geometry().center().y() - w.progress.geometry().center().y()) <= 3
        assert w.dies.parentWidget().title() == '连接与工程'
        assert w.dies.geometry().top() > w.connect_button.geometry().bottom()
        assert w.mapping_shared.isChecked()
        w.sheet.setText('Diagnostic')
        w.mapping_shared.click()
        assert not w.mapping_shared.isChecked()
        def select(row):
            item = w.file_view.item(row, 1)
            w.file_view.scrollToItem(item)
            QTest.mouseClick(w.file_view.viewport(), getattr(QtCore.Qt, 'MouseButton', QtCore.Qt).LeftButton, pos=w.file_view.visualItemRect(item).center())
            app.processEvents()
        select(1)
        assert w.sheet.text() == 'Diagnostic'
        w.sheet.setText('SeparateSheet'); w.header_row.setValue(2)
        w.profile.item(0, 1).setText('SeparatePackage')
        select(0); assert w.sheet.text() == 'Diagnostic' and w.header_row.value() == 1
        select(1); assert w.sheet.text() == 'SeparateSheet' and w.profile.item(0, 1).text() == 'SeparatePackage'
        w.persist(); w.open_folder(folder, link=False); wait(w)
        assert not w.mapping_shared.isChecked()
        select(1); assert w.sheet.text() == 'SeparateSheet' and w.header_row.value() == 2
        w.mapping_shared.click(); assert w.sheet.text() == 'Diagnostic' and w.header_row.value() == 1
        w.sheet.setText('Measurements')
        w.mapping_shared.click(); select(1); assert w.sheet.text() == 'SeparateSheet'
        w.mapping_shared.click(); assert w.sheet.text() == 'Measurements'

        assert w.info['die_count'] == 3
        assert Path(w.info['project']).samefile(folder/'GD55.dshift.json')
        assert Path(w.database.text()).is_relative_to(folder)
        assert Path(w.output.text()).is_relative_to(folder)
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Unchecked)
        w.sheet.setText('Diagnostic'); w.workers.setValue(2); w.persist()
        w.bypass_enabled.click()
        assert all(field.isEnabled() for field in w.bypass_limits)
        for field, value in zip(w.bypass_limits, (2.5,3,.125)):
            field.setValue(value)
        w.skip_drc_enabled.click()
        for field, value in zip(w.skip_drc_limits, (3,4,.5)):
            field.setValue(value)
        excel = folder/'RealDataAnalyse/Excel'
        shutil.copyfile(excel/'GD55_SYNTHETIC_DIE1.xlsx', excel/'Z_new.xlsx')
        w.open_folder(folder, link=False); wait(w)
        assert [field.value() for field in w.bypass_limits] == [2.5,3,.125]
        assert w.bypass_enabled.isChecked()
        assert w.skip_drc_enabled.isChecked() and [field.value() for field in w.skip_drc_limits] == [3,4,.5]
        assert w.file_view.rowCount() == 4 and w.file_entries()[-1]['enabled']
        select(3); w.remove_files.click()
        assert w.file_view.rowCount() == 3 and (excel/'Z_new.xlsx').is_file()
        assert not w.file_entries()[1]['enabled'] and w.sheet.text() == 'Diagnostic' and w.workers.value() == 2
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked)
        w.sheet.setText('Measurements')
        # Exercise the UI-to-import boundary with a genuinely different DIE2 file.
        from openpyxl import load_workbook
        workbook_path = folder/'RealDataAnalyse/Excel/GD55_SYNTHETIC_DIE2.xlsx'
        book = load_workbook(workbook_path)
        sheet = book['Measurements']; sheet.title = 'SeparateSheet'
        sheet.insert_rows(1); sheet['A2'] = 'SeparatePackage'
        book.save(workbook_path); book.close()
        w.mapping_shared.click()
        select(0); w.sheet.setText('Measurements')
        select(2); w.sheet.setText('Measurements')
        select(1)
        assert w.sheet.text() == 'SeparateSheet' and w.header_row.value() == 2
        select(0)
        w.mapping_section.setChecked(False)
        assert not w.profile.isVisible()
        w.import_button.click(); wait(w)
        assert w.table.rowCount() == 36
        previous_database = w.database.text()
        w.sheet.setText('MissingSheet')
        w.import_button.click()
        until = time.monotonic() + 15
        while w.worker and time.monotonic() < until: app.processEvents(); time.sleep(.02)
        assert w.worker is None and w.progress.failed
        assert w.database.text() == previous_database and Path(previous_database).is_file()
        w.sheet.setText('Measurements')
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Unchecked)
        w.run_button.click()
        until = time.monotonic() + 10
        while w.worker and time.monotonic() < until: app.processEvents(); time.sleep(.02)
        assert w.worker is None and w.progress.failed
        assert '与任务记录不符' in w.response.toPlainText()
        assert w.completion_dialog is None or not w.completion_dialog.isVisible()
        assert not Path(w.output.text()).exists()
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked)
        journal = Ledger(previous_database)
        try:
            journal.set('config',dict(project=w.project.text(),endpoint=str(endpoint)))
            journal.set('state','paused')
            journal.set('display_progress',[2,1])
        finally:
            journal.close()
        w.endpoint.setText(str(root/'changed-endpoint.json'))
        w.open_folder(folder,link=False); wait(w)
        assert Path(w.endpoint.text()) == endpoint
        w.endpoint.setText(str(root/'changed-endpoint.json'))
        with patch('RealDataAnalyse.gui.Runner') as runner:
            runner.return_value.run.return_value = dict(state='completed', output=w.output.text())
            w.bypass_enabled.click()
            assert all(not field.isEnabled() for field in w.bypass_limits)
            w.run_button.click(); wait(w)
            assert list(runner.call_args.kwargs['bypass_limits']) == [0,0,0]
            w.completion_dialog.accept()
            runner.reset_mock()
            w.bypass_enabled.click()
            w.run_button.click(); wait(w)
            runner.return_value.run.assert_called_once()
            assert runner.call_args.kwargs['bypass_limits'] == [2.5,3,.125]
            assert runner.call_args.kwargs['skip_drc_limits'] == [3,4,.5]
            assert runner.call_args.kwargs['endpoint'] == str(endpoint)
            assert 'FAIL 1' in w.progress.label(.5)
            assert all(label in w.progress.toolTip() for label in ('开始 ', '结束 ', '耗时 '))
            assert w.progress.started is None and w.progress.end_time >= w.progress.start_time
            dialog = w.completion_dialog
            assert dialog.isVisible() and dialog.windowTitle() == '任务完成'
            assert w.completion_path.isReadOnly() and w.completion_path.text() == w.output.text()
            dialog.accept(); app.processEvents()
        w.endpoint.setText(str(endpoint))
        assert w.template.text()==str(folder/'RealDataAnalyse/Template/GD55_Template.gds')
        assert sha256(folder/'GD55_1105.gds') == before
        assert (folder/'RealDataAnalyse/logs/RealDataAnalyse.log').is_file()
        clock = ProgressBar()
        assert '开始 --:--:-- · 总耗时 00:00:00' in clock.label(0)
        clock.begin()
        clock.package_status(6,12,False,2)
        initial = clock.toolTip()
        until = time.monotonic() + 4
        while clock.toolTip() == initial and time.monotonic() < until:
            QTest.qWait(50)
        assert clock.toolTip() != initial
        assert clock.value() == 5000
        assert all(label in clock.label(.5) for label in ('开始 ', '总耗时 ', 'FAIL 2', '已完成 6 / 12'))
        clock.finish()
        final = clock.label(.5)
        assert not clock.clock_timer.isActive() and '结束 ' in clock.toolTip()
        QTest.qWait(1100)
        assert clock.label(.5) == final
        # Continuous fill has no alternating Windows segments; text fits by elision.
        w.progress.failed = False
        w.progress.package_status(6,12,False)
        w.log('后台写入阶段')
        assert w.progress.detail == '已完成 6 / 12' and w.progress.value() == 5000
        app.processEvents()
        for style in ('Fusion', 'Windows'):
            if style in W.QStyleFactory.keys():
                app.setStyle(W.QStyleFactory.create(style)); app.processEvents()
                img = w.progress.grab().toImage()
                colors = {img.pixelColor(x, 3).name() for x in range(4, img.width()//2 - 4)}
                assert colors == {'#79bce8'}, colors
        w.grab().save(str(Path(tempfile.gettempdir())/'RealDataAnalyse-projects.png'))
        old_endpoint = w.endpoint.text()
        w.endpoint.setText(str(root/'absent.json')); w.connect_button.click();
        until=time.monotonic()+15
        while w.worker and time.monotonic()<until: app.processEvents(); time.sleep(.02)
        assert w.worker is None and w.connect_button.property('connected') is False
        w.endpoint.setText(old_endpoint)
        production = root/'Jobs'/'SH22'
        shutil.copytree(ROOT/'Jobs/SH22',production)
        w.open_folder(production,link=False);wait(w)
        assert w.dies.value()==2 and not w.dies.isEnabled()
        assert w.file_view.rowCount()==4 and w.profile.rowCount()==10
        assert w.ignore_measurement_failure.text() == '忽略测量失败'
        assert w.ignore_measurement_failure.isEnabled() and not w.ignore_measurement_failure.isChecked()
        w.ignore_measurement_failure.click()
        w.open_folder(production,link=False);wait(w)
        assert w.ignore_measurement_failure.isChecked()
        w.accept_info(dict(project=w.project.text(), die_count=9))
        assert w.dies.value()==2, 'Production DIE count must come from its directories'
        print('REALDATA_PROJECT_GUI_PASS: folder -> linked server -> exact 36 XLSX records and template, selections/settings, green/red status, continuous overlay, hidden log', flush=True)
    finally:
        if w.worker:
            w.stop_work()
            until=time.monotonic()+120
            while w.worker and time.monotonic()<until: app.processEvents();time.sleep(.02)
        if endpoint.exists():
            t=Transport(endpoint)
            t.request('app.shutdown')
            until=time.monotonic()+90
            while t.alive() and time.monotonic()<until: app.processEvents();time.sleep(.1)
            assert not t.alive(), 'Owned source server did not exit'
        w.close(); app.processEvents()
        configure_logging(ROOT)  # release project log handle before Windows cleanup
