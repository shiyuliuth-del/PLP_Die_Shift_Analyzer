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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from die_shift_studio.qt_compat import QtWidgets as W, QtCore, QtGui, BINDING
if BINDING == 'PyQt6':
    from PyQt6.QtTest import QTest
else:
    from PyQt5.QtTest import QTest
from RealDataAnalyse.gui import Window, ProgressBar
from RealDataAnalyse.transport import Transport
from RealDataAnalyse.storage import configure_logging, sha256

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
        w.open_folder(folder, link=False); wait(w)
        assert not w.file_entries()[1]['enabled'] and w.sheet.text() == 'Diagnostic' and w.workers.value() == 2
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked)
        w.sheet.setText('Measurements')
        w.import_button.click(); wait(w)
        assert w.table.rowCount() == 12
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Unchecked)
        w.run_button.click()
        until = time.monotonic() + 10
        while w.worker and time.monotonic() < until: app.processEvents(); time.sleep(.02)
        assert w.worker is None and w.progress.failed
        assert '与任务记录不符' in w.response.toPlainText()
        assert not Path(w.output.text()).exists()
        w.file_view.item(1, 0).setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked)
        w.run_button.click(); wait(w, 900)
        assert Path(w.output.text()).is_file()
        assert 'OAS' in w.progress.detail
        assert sha256(folder/'GD55_1105.gds') == before
        assert (folder/'logs'/'RealDataAnalyse.log').is_file()
        # Continuous fill has no alternating Windows segments; text fits by elision.
        w.progress.status('批次1 · 完成6/12 · 并行2 · 封装8 重构几何 · 写入OAS', 6, 12)
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
        print('REALDATA_PROJECT_GUI_PASS: folder -> linked server -> 3 XLSX -> 12 OAS, selections/settings, green/red status, continuous overlay, hidden log', flush=True)
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
