"""Delete, staged graphics, and editor undo across multiple action types."""
import json
import os
from dataclasses import asdict
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

import gdstk
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import Qt,QtCore
from test_v38 import fixture
from qt_shutdown import close_window


def run():
    app=W.QApplication([])
    with TemporaryDirectory() as tmp:
        folder=Path(tmp)
        w=MainWindow(jobs_dir=folder/'Jobs');w.show()
        errors=[];w.error=lambda message:errors.append(str(message))
        def wait():
            end=time.monotonic()+120
            while time.monotonic()<end:
                app.processEvents()
                if not w._tasks and not w.preview_pending and not w.coverage_timer.isActive() and not w._coverage_pending:
                    if errors:raise AssertionError(errors)
                    return
                time.sleep(.01)
            raise AssertionError(w.runtime_view.toPlainText()[-4000:])
        try:
            engine,data,_=fixture()
            p,roles=engine.p,engine.roles
            engine.close()
            lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('TOP')
            for name,rows in data.layers.items():
                layer,datatype=map(int,name.split('/'))
                cell.add(*(gdstk.Polygon(xy,layer=layer,datatype=datatype) for xy in rows))
            cell.add(gdstk.rectangle((200,100),(310,140),layer=5))
            layout=folder/'test.gds';lib.write_gds(layout)
            job=folder/'test.dshift.json'
            job.write_text(json.dumps(dict(schema_version=5,gds_path=str(layout),top_cell='TOP',roles=roles,
                 layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=asdict(p),
                 settings={'animation_enabled':False,'reconstruction_auto':False},overlays={'reconstruction':True})))
            w.load_project(job);wait()
            assert w.view_settings.max_undo_steps==10
            assert len(w.data.layers['5/0'])==2
            keys=getattr(Qt,'Key',Qt)
            qtest=importlib.import_module(QtCore.__name__.split('.')[0]+'.QtTest').QTest
            w.selection.apply_hits([('5/0',1)],'replace')
            w.activateWindow();w.canvas.setFocus();app.processEvents()
            qtest.keyClick(w.canvas,keys.Key_Delete);app.processEvents()
            assert not w._tasks and not w.preview_pending and not w.coverage_timer.isActive()
            assert len(w.data.layers['5/0'])==2 and w._effective_deleted_shapes()
            assert w.reconstruction_pending and w.pending_deleted_shapes and w.reconstruction_overlay.staged.get_visible()

            w.rulers.start=np.array([1.,2.]);w.rulers.finish(np.array([10.,2.]))
            w.change_width_ignores({('5/0',0)},True)
            w.setting_fields['space_export_interval_um'].setValue(7.)
            w.undo_button.click()
            assert not w.ignored_width_lines and w.rulers.rows and w.view_settings.space_export_interval_um==7.
            qtest.keyClick(w.canvas,keys.Key_U);app.processEvents()
            assert not w.rulers.rows and len(w.data.layers['5/0'])==2 and w.pending_deleted_shapes
            w.undo_edit();app.processEvents()
            assert not w._tasks and len(w.data.layers['5/0'])==2 and not w.pending_deleted_shapes and not w._effective_deleted_shapes()
            control=getattr(getattr(Qt,'KeyboardModifier',Qt),'ControlModifier')
            shift=getattr(getattr(Qt,'KeyboardModifier',Qt),'ShiftModifier')
            qtest.keyClick(w.canvas,keys.Key_U,control);app.processEvents()
            assert not w._tasks and len(w.data.layers['5/0'])==2 and w.pending_deleted_shapes
            w.compute_reconstruction();wait()
            assert len(w.data.layers['5/0'])==1 and w.data.deleted_shapes and not w.pending_deleted_shapes
            # Undo an already-applied deletion while auto-compute is off: must defer & show restore marker
            w.undo_edit();app.processEvents()
            assert not w._tasks and len(w.data.layers['5/0'])==1 and w.reconstruction_pending and w.pending_restored_shapes and w.reconstruction_overlay.staged.get_visible()
            w.compute_reconstruction();wait()
            assert len(w.data.layers['5/0'])==2 and not w.data.deleted_shapes and not w.pending_restored_shapes
            qtest.keyClick(w.canvas,keys.Key_U,shift);app.processEvents()
            assert not w._tasks and w.pending_deleted_shapes
            w.compute_reconstruction();wait()
            assert len(w.data.layers['5/0'])==1 and w.data.deleted_shapes and not w.pending_deleted_shapes
            saved=folder/'saved.dshift.json';w.write_project(saved)
            w.load_project(saved);wait()
            assert len(w.data.layers['5/0'])==1 and w.data.deleted_shapes
            assert w.show_issues.isChecked() and not w.view_settings.animation_enabled
            assert w.view_settings.reconstruction_boundary_mode=='perpendicular' and w.dxf_settings.region_auto_join
            assert w.output_compensation()=={}
            w.compensation_boxes['5/0'].setValue(-12.345)
            assert w.output_compensation()=={'5/0':-12.345}
            # A press whose release belongs to a cell editor must not arm a
            # later unpressed mouse move. Reproduce at the actual viewport.
            view=w.layer_table.viewport();old_order=w.ordered_layers()
            qtest.mousePress(view,getattr(getattr(Qt,'MouseButton',Qt),'LeftButton'),
                             pos=w.layer_table.visualItemRect(w.layer_table.item(0,2)).center())
            assert w.layer_drag.press is not None
            qtest.mouseRelease(w.compensation_boxes['5/0'],
                               getattr(getattr(Qt,'MouseButton',Qt),'LeftButton'))
            qtest.mouseMove(view,w.layer_table.visualItemRect(w.layer_table.item(2,2)).center())
            assert w.ordered_layers()==old_order and w.layer_drag.press is None
            from shapely.geometry import box
            from die_shift_studio.drc_waivers import pose_id
            w.problem_filter.setCurrentIndex(0);app.processEvents()
            w.select_preview(1,0);wait()
            assert w.active_preview is not None
            piece=w.current_frame['reconstruction']['pieces'][0]
            witness=dict(kind='线宽不足',layer='5/0',refs=(('5/0',piece['index']),),geometry=box(40,0,45,5))
            frame=dict(w.current_frame,drc=[witness],nominal_global=False)
            frame.pop('drc_locations',None)
            frame['reconstruction']=dict(frame['reconstruction'],constraints_complete=True,hard_failures=(),
                                         pieces=[dict(piece,rule_witnesses=[witness],connection_failed=False)])
            w.current_frame=frame
            w.fill_issues()
            def click_waiver(expected):
                def select_menu():
                    menu=app.activePopupWidget()
                    assert isinstance(menu,W.QMenu)
                    action,=menu.actions()
                    assert action.text()==expected
                    action.trigger();menu.close()
                QtCore.QTimer.singleShot(30,select_menu)
                w.issue_menu(w.issue_table.visualItemRect(w.issue_table.item(0,0)).center())
                app.processEvents()
            click_waiver('忽略此问题')
            assert len(frame['drc'])==1 and w.issue_table.item(0,0).background().color().name()=='#e4f4e8'
            assert next(iter(w.reconstruction_overlay.zone_views.values()))[1]=='green'
            assert not w.reconstruction_overlay.problems.get_visible()
            assert w.active_preview in w.approved_drc_poses()
            click_waiver('取消忽略此问题')
            assert not w.ignored_drc and not w.approved_drc_poses()
            assert next(iter(w.reconstruction_overlay.zone_views.values()))[1]=='yellow'
            w.undo_edit()
            assert pose_id(frame['states']) in w.ignored_drc
            w.write_project(saved)
            w.load_project(saved);wait()
            assert w.ignored_drc and w.output_compensation()=={'5/0':-12.345}
            auto=w.setting_fields['reconstruction_auto_waive_drc']
            auto.setChecked(True)
            assert w.ignored_drc
            auto.setChecked(False);wait()
            assert not w.ignored_drc
            w.clear_cached_results();wait()
            assert not w.undo_button.isEnabled() and not w.redo_button.isEnabled()
        finally:
            close_window(app,w)
    print('GUI_V64_EDITING_PASS delete, reuse of source indices, pending marker, undo/redo, and save/restore')


if __name__=='__main__':
    run()
