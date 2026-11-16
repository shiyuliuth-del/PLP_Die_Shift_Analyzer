"""Let asynchronous application shutdown complete before a test interpreter exits."""
import time
from die_shift_studio.qt_compat import QtCore


def close_window(app,window,timeout=120):
    window.close();deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        threads=window.findChildren(QtCore.QThread)
        if not window.isVisible() and not window._tasks and not any(t.isRunning() for t in threads):break
        time.sleep(.005)
    else:raise AssertionError('Application did not finish cooperative shutdown; test must not destroy running QThreads')
    QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete)
    app.processEvents()
