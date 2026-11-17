"""Actual Qt interaction: plan capture, row headers, table grips and bounded log."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import shutil
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W, BINDING
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest
from RealDataAnalyse.gui import Window
from RealDataAnalyse.widgets import HeightGrip
app=W.QApplication.instance() or W.QApplication([])
def wait(w):
    end=time.monotonic()+30
    while w.worker and time.monotonic()<end:app.processEvents();time.sleep(.01)
    assert w.worker is None and not w.progress.failed,w.response.toPlainText()
    app.processEvents()
with tempfile.TemporaryDirectory(prefix='rda-plans-gui-') as name:
    root=Path(name).resolve();folder=root/'GD55'
    shutil.copytree(Path(__file__).resolve().parents[1]/'Jobs/GD55',folder)
    w=Window(plan_path=root/'plans.sqlite');w.show();app.processEvents()
    try:
        assert w.timeout.value()==0
        assert w.import_button.text()=='创建任务'
        assert not any(b.text()=='刷新记录' for b in w.findChildren(W.QPushButton))
        tabs=w.findChild(W.QTabWidget);assert [tabs.tabText(i) for i in range(tabs.count())][-2:]==['全部IPC命令调试','实时命令信息']
        w.open_folder(folder,link=False);wait(w)
        w.sheet.setText('Diagnostic');w.workers.setValue(2)
        w.create_plan_button.click();wait(w)
        w.sheet.setText('Measurements');w.workers.setValue(3)
        w.create_plan_button.click();wait(w)
        panel=w.plan_panel;panel.refresh();assert panel.table.rowCount()==2
        assert panel.table.verticalHeaderItem(0).text()=='1'
        ids=[r['id'] for r in w.plan_store.rows()[1]]
        assert w.plan_store.get(ids[0])['snapshot']['profile']['sheet']=='Diagnostic'
        assert w.plan_store.get(ids[1])['snapshot']['profile']['sheet']=='Measurements'
        assert panel.table.item(0,0).background().color().name()=='#fff1c7'
        w.plan_store.update(ids[0],status='completed',progress=100,started=time.time()-2,ended=time.time())
        w.plan_store.update(ids[1],status='error',severity='error',message='missing file')
        panel.refresh()
        assert panel.table.item(0,0).background().color().name()=='#d9efdf'
        assert panel.table.item(1,0).background().color().name()=='#f8d9d7'
        for table in (w.file_view,w.profile,w.table,panel.table):assert not table.verticalHeader().isHidden()
        # Send actual mouse events to each drag handle, including the plan panel.
        left=getattr(QtCore.Qt,'MouseButton',QtCore.Qt).LeftButton
        grips=w.findChildren(HeightGrip)
        assert len(grips)==4
        for grip in grips:
            old=grip.table.height();position=grip.rect().center()
            QTest.mousePress(grip,left,pos=position)
            delta=QtCore.QPoint(0,31)
            kind=getattr(QtCore.QEvent,'Type',QtCore.QEvent).MouseMove
            none=getattr(QtCore.Qt,'MouseButton',QtCore.Qt).NoButton
            mods=getattr(QtCore.Qt,'KeyboardModifier',QtCore.Qt).NoModifier
            move=QtGui.QMouseEvent(kind,QtCore.QPointF(position+delta),QtCore.QPointF(grip.mapToGlobal(position)+delta),none,left,mods)
            W.QApplication.sendEvent(grip,move)
            QTest.mouseRelease(grip,left,pos=position+QtCore.QPoint(0,31))
            app.processEvents();assert grip.table.height()>old,(old,grip.table.height())
        w.conversation_limit.setValue(100)
        for i in range(300):w.conversation.record('send','endpoint',dict(id=i,command='info'))
        for _ in range(5):w.flush_conversation()
        assert w.conversation_view.document().blockCount()<=100
        assert '299' in w.conversation_view.toPlainText()
        # The package table must not disguise partial output as a clean result.
        import threading,json
        from RealDataAnalyse.excel import import_files
        from RealDataAnalyse.storage import Ledger
        database=folder/'Tasks'/'table-check.sqlite'
        import_files(sorted(folder.glob('*.xlsx')),database,3,3,threading.Event())
        ledger=Ledger(database)
        try:
            with ledger.db:ledger.db.execute("update packages set state='committed_with_issues',result=? where seq=1",(json.dumps(dict(drc_count=3,connection_failure_count=1,error_rectangles=2,partial_output=True)),))
        finally:ledger.close()
        w.database.setText(str(database));w.refresh()
        assert w.table.item(0,3).text()=='已输出（含问题）'
        assert '连接失败 1项' in w.table.item(0,4).text() and 'ERROR矩形 2个' in w.table.item(0,4).text()
        assert w.command.findText('task.issues')>=0
        w.grab().save(str(Path(tempfile.gettempdir())/'RealDataAnalyse-v48.png'))
    finally:
        # Do not let tests persist changed production UI height preferences.
        for grip in w.findChildren(HeightGrip):w.settings.remove('height/'+grip.key)
        w.close();app.processEvents()
        from RealDataAnalyse.storage import configure_logging
        configure_logging(Path(__file__).resolve().parents[1])
print('GUI_REALDATA_PLANS_PASS: captured per-row settings, colors, real mouse height drags, headers, timeout zero, bounded conversation')
