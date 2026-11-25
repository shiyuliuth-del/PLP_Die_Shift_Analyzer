"""Live interface sizing, project persistence and legacy defaults."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

import gdstk

from die_shift_studio.project_results import identity
from die_shift_studio.settings import ViewSettings
from qt_shutdown import close_window
from test_v38 import fixture


class InterfaceScaleTests(unittest.TestCase):
    def test_live_scale_and_project_round_trip(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from die_shift_studio.gui import MainWindow, W

        app = W.QApplication.instance() or W.QApplication([])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            w = MainWindow(jobs_dir=root/'Jobs')
            w.show()
            errors = []
            w.error = lambda message: errors.append(str(message))

            def wait():
                end = monotonic() + 20
                while monotonic() < end:
                    app.processEvents()
                    if not w._tasks and not w.viewport_timer.isActive():
                        self.assertFalse(errors, errors)
                        return
                    sleep(.005)
                self.fail('Project load did not finish')

            try:
                engine, data, _ = fixture()
                roles = engine.roles
                engine.close()
                lib = gdstk.Library(unit=1e-6, precision=1e-9)
                cell = lib.new_cell('TOP')
                for key, polygons in data.layers.items():
                    layer, datatype = map(int, key.split('/'))
                    cell.add(*(gdstk.Polygon(xy, layer=layer, datatype=datatype) for xy in polygons))
                source = root/'case.gds'
                lib.write_gds(source)
                old_project = root/'legacy.dshift.json'
                old_project.write_text(json.dumps(dict(schema_version=5, gds_path=str(source), top_cell='TOP',
                    roles=roles, layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'], settings={})), encoding='utf8')
                w.load_project(old_project)
                wait()

                scale = w.setting_fields['ui_scale_percent']
                w.controls.setCurrentWidget(w.controls.widget(2))
                w.setting_sections['通用'].setChecked(True)
                self.assertEqual(scale.accessibleName(), '界面缩放级别')
                self.assertEqual([scale.itemText(i) for i in range(scale.count())],
                                 [f'{value}%' for value in range(10, 301, 10)])
                self.assertEqual(scale.currentData(), 100)
                for invalid in (0, 15, 310):
                    with self.assertRaises(ValueError):
                        ViewSettings.from_dict({'ui_scale_percent': invalid})

                def dimensions():
                    return (w.run_button.font().pixelSize(), w.save_button.minimumHeight(),
                            w.toolbar.iconSize().width(), w.extreme_table.verticalHeader().defaultSectionSize(),
                            w.issue_table.columnWidth(0), w.role_boxes['5/0'].font().pixelSize())

                before = dimensions()
                revision, fingerprint = w.revision, identity(w)
                for percent in (150, 300, 10, 100, 170):
                    with self.subTest(percent=percent):
                        scale.setCurrentIndex(scale.findData(percent))
                        wait()
                        self.assertEqual(w.view_settings.ui_scale_percent, percent)
                        self.assertEqual(dimensions(), tuple(max(1, round(value*percent/100)) for value in before))
                        self.assertEqual(w.revision, revision)
                        self.assertEqual(identity(w), fingerprint)

                saved = root/'saved.dshift.json'
                w.write_project(saved)
                self.assertEqual(json.loads(saved.read_text(encoding='utf8'))['settings']['ui_scale_percent'], 170)
                w.load_project(old_project)
                wait()
                self.assertEqual(scale.currentText(), '100%')
                self.assertEqual(dimensions(), before)
                w.load_project(saved)
                wait()
                self.assertEqual(scale.currentText(), '170%')
                self.assertEqual(w.run_button.font().pixelSize(), 20)
            finally:
                close_window(app, w)
