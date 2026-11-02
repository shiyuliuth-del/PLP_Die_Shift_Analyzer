"""Standalone offscreen regression for the straight-line repair patch view.

Run: .venv/Scripts/python.exe tests/gui_repair.py
"""
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from die_shift_studio.gui import MainWindow, W


def main():
    app = W.QApplication.instance() or W.QApplication([])
    errors = []
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(jobs_dir=Path(directory) / 'Jobs')
        window.resize(1500, 1000)
        window.error = lambda error: errors.append(str(error))
        window.show()

        def wait():
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                app.processEvents()
                pending = (window.worker, window.preview_workers, window.preview_pending,
                           window.motion_worker, window.motion_pending, window.hardware_worker,
                           window.coverage_timer.isActive(), window._coverage_pending,
                           window.viewport_timer.isActive(), window.transition.timer.isActive(),
                           window.mouse_die.timer.isActive(), window.mouse_die.idle.isActive())
                if not any(pending):
                    app.processEvents()
                    assert not errors, errors
                    return
                time.sleep(.004)
            raise AssertionError('Repair Qt worker/timer timeout')

        def show(shift):
            window.manual_states = np.array([[shift, 0., 0.], [0., 0., 0.]])
            window.request_preview()
            wait()

        try:
            window.load_project(ROOT / 'Jobs/示例N_三线路窄通道宽度.dshift.json')
            wait()
            window.view_settings = replace(window.view_settings, animation_enabled=False)
            window.run_evaluation()
            wait()
            tab = window.preview_tabs.indexOf(window.preview_tabs.widget(window.preview_tabs.count() - 1))
            window.preview_tabs.setCurrentIndex(tab)

            # A via that shifted but still lands on the copper needs no patch.
            window.repair_toggle.setChecked(True)
            wait()
            show(35.)
            assert window.repair_data is not None, 'Repair data was not computed'
            assert window.repair_data['summary']['count'] == 0, window.repair_data['summary']
            assert not window.repair_overlay.patches.get_visible()
            assert '无需直线补块' in window.repair_summary.text(), window.repair_summary.text()
            assert window.repair_table.rowCount() == 0

            # Far enough that the via leaves the RDL: one patch, new DRC areas.
            show(400.)
            summary = window.repair_data['summary']
            assert summary['count'] == 1, summary
            assert summary['layers'] == ['5/0']
            assert summary['added'] == 4, summary
            assert summary['on_patch'] == 4 and summary['elsewhere'] == 0, summary
            assert window.repair_overlay.patches.get_visible()
            assert window.repair_overlay.added.get_visible()
            assert window.repair_table.rowCount() == 1
            assert window.repair_table.item(0, 0).text() == '5/0'
            # The default diameter follows the via, not the fixed-RDL line width.
            assert window.repair_data['summary']['widths_um'] == [60.], window.repair_data['summary']
            assert window.repair_table.item(0, 5).text() == '60.000'
            assert window.repair_table.item(0, 7).text() == '4'
            text = window.repair_summary.text()
            assert '直线补块 1 块' in text and '补块新增 4 处' in text and '60.000' in text, text
            # Repair mode owns the violation markers, so they follow its toggle.
            assert window.violation_artist.get_visible()
            assert len(window.violation_artist.get_paths()) == len(window.repair_data['violations'])

            # Clicking a row must take the view to the region that row describes.
            window.ax.set_xlim(-2000, 2000)
            window.ax.set_ylim(-2000, 2000)
            wait()
            wide = (np.ptp(window.ax.get_xlim()), np.ptp(window.ax.get_ylim()))
            patch = window.repair_data['repairs'][0]
            px, py = (patch.patch.bounds[0] + patch.patch.bounds[2]) / 2., (patch.patch.bounds[1] + patch.patch.bounds[3]) / 2.
            window.repair_table.setCurrentCell(-1, -1)
            window.repair_table.setCurrentCell(0, 0)
            wait()
            xlim, ylim = window.ax.get_xlim(), window.ax.get_ylim()
            assert np.ptp(xlim) < wide[0] and np.ptp(ylim) < wide[1], (xlim, ylim, wide)
            assert xlim[0] <= px <= xlim[1] and ylim[0] <= py <= ylim[1], (xlim, ylim, px, py)

            assert window.repair_violation_table.rowCount() == 4
            assert window.repair_violation_table.item(0, 2).text() == '5/0 #1'
            violation = window.repair_data['new_violations'][0]['geometry'].bounds
            vx, vy = (violation[0] + violation[2]) / 2., (violation[1] + violation[3]) / 2.
            window.repair_violation_table.setCurrentCell(-1, -1)
            window.repair_violation_table.setCurrentCell(0, 0)
            wait()
            xlim, ylim = window.ax.get_xlim(), window.ax.get_ylim()
            assert np.ptp(xlim) < wide[0] and np.ptp(ylim) < wide[1], (xlim, ylim, wide)
            assert xlim[0] <= vx <= xlim[1] and ylim[0] <= vy <= ylim[1], (xlim, ylim, vx, vy)

            # Layer visibility still governs the patch, like any other overlay.
            window.visible_boxes['5/0'].setChecked(False)
            wait()
            assert not window.repair_overlay.patches.get_visible()
            window.visible_boxes['5/0'].setChecked(True)
            wait()
            assert window.repair_overlay.patches.get_visible()

            # Returning to a cached pose must not keep the previous patch.
            show(35.)
            assert window.repair_data['summary']['count'] == 0
            assert not window.repair_overlay.patches.get_visible()
            show(400.)
            assert window.repair_data['summary']['count'] == 1

            # An explicit diameter is honoured, and the landing option only adds
            # copper while the patch is narrower than the annulus.
            window.view_settings = replace(window.view_settings, repair_patch_width_um=15.)
            show(400.)
            assert window.repair_data['summary']['widths_um'] == [15.]
            assert window.repair_table.item(0, 5).text() == '15.000'
            bare = sum(q['geometry'].area for q in window.repair_data['violations']
                       if q['kind'] == '孔盘包围不足')
            window.view_settings = replace(window.view_settings, repair_patch_width_um=15.,
                                           repair_landing_pad=True)
            show(400.)
            landed = sum(q['geometry'].area for q in window.repair_data['violations']
                         if q['kind'] == '孔盘包围不足')
            assert landed < bare, (landed, bare)
            window.view_settings = replace(window.view_settings, repair_patch_width_um=0.,
                                           repair_landing_pad=False)
            show(400.)
            assert window.repair_data['summary']['widths_um'] == [60.]

            window.repair_toggle.setChecked(False)
            wait()
            assert window.repair_data is None
            assert not window.repair_overlay.patches.get_visible()
            assert not window.repair_overlay.added.get_visible()
            assert '勾选' in window.repair_summary.text()
            assert not window.violation_artist.get_visible() or not window.drc_toggle.isChecked()
            print('GUI_REPAIR_PASS: straight-line patch drawn, per-via default diameter, '
                  'post-patch DRC attributed, row click-through navigation to patch and violation, '
                  'layer visibility, cached poses and landing option')
        finally:
            window.close()
            deadline = time.monotonic() + 30
            while window.isVisible() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.004)
            app.processEvents()
            assert not window.isVisible(), 'Main window did not close cleanly'


if __name__ == '__main__':
    main()
