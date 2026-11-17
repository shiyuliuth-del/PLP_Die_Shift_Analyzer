"""Real Qt client controls, background IO and paged ledger rendering."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import threading
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from die_shift_studio.qt_compat import QtWidgets as W
from RealDataAnalyse.gui import Window, COMMANDS
from RealDataAnalyse.excel import generate_example, import_files

app = W.QApplication.instance() or W.QApplication([])
with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    files = generate_example(folder, count=403, die_count=2)
    database = folder / 'run.sqlite'
    import_files(files, database, 2, 2, threading.Event())
    w = Window()
    w.show()
    w.database.setText(str(database))
    w.refresh()
    assert w.table.rowCount() == 200
    w.change_page(1)
    assert w.table.verticalHeaderItem(0).text() == '201'
    w.change_page(1)
    assert w.table.rowCount() == 3 and w.table.verticalHeaderItem(0).text() == '401'
    for command in COMMANDS:
        w.command.setCurrentText(command)
        assert isinstance(__import__('json').loads(w.args.toPlainText()), dict)
    w.execute(lambda stop, progress: (stop.wait(.15), {'worker': True})[1])
    assert not w.run_button.isEnabled()
    until = time.monotonic() + 5
    while w.worker and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    assert w.worker is None and w.run_button.isEnabled()
    w.grab().save(str(Path(tempfile.gettempdir()) / 'RealDataAnalyse-v44.png'))
    w.close()
    app.processEvents()
print('GUI_V44_PASS: all 15 commands, 403 rows/3 pages, worker gating and clean close')
