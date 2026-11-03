"""Exercise hardware selection and Qt shutdown when the worker has already returned."""
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow, QtCore, W


def main():
    app=W.QApplication.instance() or W.QApplication([])
    card={'name':'test NVIDIA','id':1}
    detected={'backend':'cuda','devices':[card],'selected':card,'reason':'verified test device'}
    inventory=[{'name':'test AMD'},card]
    with tempfile.TemporaryDirectory() as folder, patch(
            'die_shift_studio.acceleration.capabilities',return_value=detected), patch(
            'die_shift_studio.acceleration.graphics_inventory',return_value=inventory):
        # Hold the initial callback to deliver done/finished in a controlled order.
        with patch.object(MainWindow,'discover_hardware',lambda self:None):
            window=MainWindow(jobs_dir=Path(folder)/'Jobs')
            window.show()
            app.processEvents()
        window.view_settings=replace(window.view_settings,acceleration='cuda')
        window.discover_hardware()
        assert window.hardware_worker.wait(5000)
        app.processEvents()
        combo=window.setting_fields['acceleration']
        assert combo.currentData()=='cuda' and card['name'] in combo.currentText()
        assert all(x['name'] in window.hardware_label.text() for x in inventory)
        with patch('die_shift_studio.acceleration.capabilities',return_value={
                'backend':'cpu','devices':[],'reason':'no compatible driver'}):
            window.discover_hardware()
            assert window.hardware_worker.wait(5000)
            app.processEvents()
        assert combo.currentData()=='cuda' and 'CPU' in combo.currentText()
        window.discover_hardware()
        worker=window.hardware_worker
        assert worker.wait(5000) and not worker.isRunning() and window._tasks
        assert not window.close() and window._closing and window.isVisible()
        # Do not start another probe or touch widgets awaiting destruction.
        window.discover_hardware()
        assert window.hardware_worker is worker
        deadline=time.monotonic()+5
        while window._tasks and time.monotonic()<deadline:
            app.processEvents()
        assert not window._tasks
        app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete)
    print('GUI_V32_PASS selected NVIDIA, CPU fallback preserves saved preference, queued worker shutdown')

if __name__=='__main__':
    main()
