"""Offscreen actual-event/engineering-save regression for selective integration.

Geometry classification and cut movement are covered independently by engine tests.
This exercises real Qt controls, Matplotlib mouse delivery, and project persistence.
"""
import os,sys,time,tempfile,json
import numpy as np
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from matplotlib.backend_bases import MouseEvent
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W


def main():
    app=W.QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        w=MainWindow(jobs_dir=Path(tmp)/'Jobs');w.resize(1500,1000);w.show()
        errors=[];w.error=lambda e:errors.append(str(e))
        def settle():
            until=time.monotonic()+180
            while time.monotonic()<until:
                app.processEvents()
                if not (w._tasks or w.preview_pending or w.motion_pending or
                        w.width_yield.timer.isActive() or w.viewport_timer.isActive() or
                        w.transition.timer.isActive() or w.coverage_timer.isActive()):break
                time.sleep(.005)
            else:raise AssertionError('integration GUI timeout')
            assert not errors,errors
            assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-3000:]
        def mouse(kind,x,y):
            px,py=w.ax.transData.transform((x,y))
            w.canvas.callbacks.process(kind,MouseEvent(kind,w.canvas,px,py,button=1))
        try:
            settle()
            assert hasattr(w,'dxf_button') and w.dxf_fields['merge_layers'].isChecked()
            for key in ('width_yield_method','width_yield_seed','width_grid_nodes','width_show_probability',
                        'show_annulus_range','show_spacing_range','show_low_contact','show_low_width'):
                assert key in w.setting_fields,key
            assert w.setting_fields['width_yield_method'].currentData()=='grid'
            assert w.setting_fields['acceleration'].findData('cuda')>=0
            assert hasattr(w,'width_yield') and hasattr(w,'problem_filter')
            assert hasattr(w,'second_left') and hasattr(w,'second_right')
            w.load_project(ROOT/'Jobs/示例F_逐芯片幅度与迭代窗口.dshift.json');settle()
            w.tabs.setCurrentIndex(next(i for i in range(w.tabs.count()) if w.tabs.tabText(i)=='结果图形'));app.processEvents();w.canvas.draw()
            w.ax.set_xlim(0,100);w.ax.set_ylim(0,100);w.canvas.draw()
            # Supply a minimal overlay snapshot, not fabricated engine results.
            # This isolates GUI event dispatch and avoids a costly geometry solve.
            with patch.object(w,'request_preview') as preview, \
                 patch.object(w.reconstruction_overlay,'data',return_value={'pieces':[{'layer':'5/0'}]}), \
                 patch.object(w.reconstruction_overlay,'pick',return_value=None), \
                 patch.object(w.reconstruction_overlay,'draw'):
                w.reconstruction_toggle.blockSignals(True);w.reconstruction_toggle.setChecked(True);w.reconstruction_toggle.blockSignals(False)
                w.region_move.click();assert w.region_select.mode=='move'
                mouse('button_press_event',40,40);mouse('motion_notify_event',50,50);mouse('button_release_event',50,50)
                assert len(w.view_settings.reconstruction_regions)==1
                w.region_stay.click();assert not w.region_move.isChecked()
                mouse('button_press_event',60,60);mouse('motion_notify_event',55,55);mouse('button_release_event',55,55)
                assert [r[2] for r in w.view_settings.reconstruction_regions]==['move','stay']
                assert preview.called
                saved_regions=w.view_settings.reconstruction_regions
                plane=('3/0',2,(('1/0',7),('2/0',7)),1.,0.,12.,14.)
                w.view_settings=replace(w.view_settings,reconstruction_cuts=(plane,),
                    reconstruction_second_cuts=(plane,),reconstruction_extra_cuts=(plane,),
                    reconstruction_dropped_cuts=(plane[:3],))
                # Both presentation and numeric settings must retain unexposed edits.
                w.setting_fields['width_yield_decimals'].setValue(4)
                assert w.view_settings.reconstruction_cuts==(plane,)
                before=preview.call_count
                w.dirty=False;w._coupled_key=('old',);w._coupled_data={'old':True}
                w.setting_fields['reconstruction_frame_margin_um'].setValue(w.view_settings.reconstruction_frame_margin_um+1)
                assert preview.call_count>before
                assert w.dirty and w._coupled_key is None and w._coupled_data is None
                assert w.view_settings.reconstruction_regions==saved_regions
                project=Path(tmp)/'integration.dshift.json';w.write_project(project)
                raw=json.loads(project.read_text())
                assert 'dxf_options' in raw and raw['settings']['width_yield_method']=='grid'
                clear=next(b for b in w.findChildren(W.QPushButton) if b.text()=='清除全部手动切口')
                w.dirty=False;clear.click();assert w.dirty
                for key in ('reconstruction_cuts','reconstruction_second_cuts','reconstruction_extra_cuts','reconstruction_dropped_cuts'):
                    assert not getattr(w.view_settings,key),key
                w.dirty=False;w.clear_regions.click();assert w.dirty
                assert not w.view_settings.reconstruction_regions
                assert w.region_select.mode is None
                # Helper geometry is tested separately; verify these GUI edit routes
                # invalidate the same stale evidence even without mouse dragging.
                changed=replace(w.view_settings,reconstruction_second_cuts=(plane,))
                with patch.object(w,'_selected_reconstruction_piece',return_value={}), \
                     patch('die_shift_studio.reconstruction_overlay.nudge_cut',return_value=changed):
                    w.dirty=False;w.nudge_selected_cut(5);assert w.dirty
                rotated=replace(changed,reconstruction_second_cuts=((plane[0],plane[1],plane[2],0.,1.,12.,14.),))
                with patch.object(w,'_selected_reconstruction_piece',return_value={}), \
                     patch('die_shift_studio.reconstruction_overlay._rotate_second',return_value=rotated):
                    w.dirty=False;w.rotate_selected_second(5);assert w.dirty
                previous=w.current_frame
                w.current_frame={'reconstruction':{'pieces':[{'layer':plane[0],'index':plane[1],'vias':plane[2]}]}}
                w.reconstruction_table.blockSignals(True)
                w.reconstruction_table.setRowCount(1);w.reconstruction_table.setCurrentCell(0,0)
                w.reconstruction_table.blockSignals(False)
                w.dirty=False;w.delete_selected_cut();assert w.dirty
                assert not w.view_settings.reconstruction_second_cuts
                w.current_frame=previous
                status_frame={'states':np.zeros((len(w.case['dies']),3)),
                              'reconstruction':{'scope':'test','pieces':[],
                                                'summary':{'regions_ambiguous':2}}}
                w.show_frame_status(status_frame)
                assert '2 处手动动区归属不明确，未应用' in w.statusBar().currentMessage()
            w.load_project(project);settle()
            assert w.view_settings.reconstruction_regions==saved_regions
            assert w.view_settings.reconstruction_second_cuts==(plane,)
            assert w.view_settings.reconstruction_dropped_cuts==(plane[:3],)
            assert w.view_settings.width_yield_decimals==4
            assert w.region_select.mode is None and not w.region_move.isChecked() and not w.region_stay.isChecked()
            print('GUI_BRANCH_INTEGRATION_PASS: preserved controls, real bidirectional region events, edit retention, cache refresh, save/reopen, clear all')
        finally:w.close();app.processEvents()

if __name__=='__main__':main()
