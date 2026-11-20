"""Real Qt worker progress must disappear after the last background task."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from die_shift_studio.gui import MainWindow, W, Worker

app = W.QApplication([])


def wait_until(predicate):
    for _ in range(1000):
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for Qt workers')


with tempfile.TemporaryDirectory() as tmp:
    window = MainWindow(jobs_dir=Path(tmp) / 'Jobs')
    window.show()
    wait_until(lambda: not window._tasks)
    first, second = threading.Event(), threading.Event()

    def work(gate, label):
        def run(progress):
            progress(40, label)
            if not gate.wait(10):
                raise RuntimeError('Worker was not released')
        return run

    a, b = (Worker(work(gate, label), window) for gate, label in ((first, 'first'), (second, 'second')))
    try:
        for task, label in ((a, 'first'), (b, 'second')):
            window.watch_worker(task, 'test')
            task.start()
            wait_until(lambda: window.progress.isVisible() and window.statusBar().currentMessage() == label)
        first.set()
        wait_until(lambda: id(a) not in window._tasks)
        assert window.progress.isVisible() and window.statusBar().currentMessage() == 'second'
        second.set()
        wait_until(lambda: not window._tasks)
        assert window.progress.isHidden() and not window.statusBar().currentMessage()
        window.statusBar().showMessage('animation running')
        window.show_frame_status(None)
        assert not window.statusBar().currentMessage()
    finally:
        first.set(); second.set()
        wait_until(lambda: not window._tasks)
        window.close()
        app.processEvents()
print('GUI_V59_PASS: progress and status clear together after overlapping background jobs')
