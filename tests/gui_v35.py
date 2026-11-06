"""Native-picker contract, on-row yields, independent numerical settings and pinned labels."""
import json,os,sys,tempfile,time,traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,ACCEPTED
from die_shift_studio.core import read_gds
from die_shift_studio.gds_shapes import shape_nodes,node_fingerprint
from die_shift_studio.width_yield import policy_yields


def main():
    app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
    w=MainWindow(jobs_dir=folder/'Jobs');w.show();errors=[];w.error=lambda text:errors.append(str(text))
    def settle():
        until=time.monotonic()+180
        while time.monotonic()<until:
            app.processEvents()
            if not (w._tasks or w.preview_pending or w.motion_pending or w.width_yield.timer.isActive()
                    or w.viewport_timer.isActive() or w.transition.timer.isActive() or w.coverage_timer.isActive()):break
            time.sleep(.003)
        else:raise AssertionError('V35 UI timeout')
        app.processEvents()
        assert not errors,errors
        assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-2000:]
    try:
        job=json.loads((ROOT/'examples/示例O_运动网络三线路通道.json').read_text(encoding='utf-8'))
        job['gds_path']=str(ROOT/'examples'/job['gds_path'])
        job['parameters'].update(multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=2)
        job['settings'].update(animation_enabled=False,width_yield_method='importance',width_yield_exponent=8,width_grid_nodes=5)
        job['overlays']={'width':True}
        w.set_data(read_gds(Path(job['gds_path'])),job);settle();w.run_evaluation();settle()
        assert w.controls.tabText(3)=='输出' and w.dxf_button.text()=='DXF输出'
        assert not hasattr(w,'width_label_overflow')
        label=w.width_yield.label
        for width in (1550,1120):
            w.resize(width,950);settle()
            frame_height=w.canvas.height();center=label.mapToGlobal(label.rect().center())
            location=w.toolbar.locLabel
            assert abs(center.y()-location.mapToGlobal(location.rect().center()).y())<=1
            assert abs(center.x()-w.canvas.mapToGlobal(w.canvas.rect().center()).x())<=1
            for text in ('x=1 y=2','x=−123456789.999 y=+123456.99',''):
                location.setText(text);app.processEvents()
                assert label.mapToGlobal(label.rect().center())==center
                assert w.canvas.height()==frame_height
            assert '全修正良率' in label.text() and '不修正良率' in label.text(),label.text()
            w.width_toggle.setChecked(False);settle();assert w.canvas.height()==frame_height
            w.width_toggle.setChecked(True);settle();assert w.canvas.height()==frame_height
        # Width sampling settings only restart the global numerical job.
        frame=w.current_frame
        with patch.object(w,'request_preview',side_effect=AssertionError('sampling settings rebuilt preview')):
            w.setting_fields['width_yield_seed'].setValue(912);settle()
            assert w.current_frame is frame
            w.setting_fields['width_yield_method'].setCurrentIndex(w.setting_fields['width_yield_method'].findData('grid'));settle()
            assert w.current_frame is frame and w.width_yield.bank['method']=='grid'
        assert not w.setting_fields['width_yield_exponent'].isEnabled() and w.setting_fields['width_grid_nodes'].isEnabled()
        old=w.width_yield.bank
        refs=tuple(old['by_line'])
        w.change_width_ignores(refs[:1],True);settle();assert w.width_yield.bank is old
        w.change_width_ignores(refs,True);settle()
        q=policy_yields(old,refs);assert q['values'][0]==q['values'][1]
        w.change_width_ignores(refs,False);settle()
        # Keep every explicitly selected nominal label on the canvas.
        summary=w.current_frame['width_summary'];selected=sorted(summary['lines'])[:2]
        w.selection.apply_hits(selected,'replace');settle()
        for point in ((4000,4000),(-4000,-4000)):
            w.set_view(point,350,180);w.viewport_changed(force=True);settle();w.canvas.draw()
            labels=[a for a in w.width_overlay.labels if a.get_visible()]
            assert len(labels)==len(selected)
            for a in labels:
                tip=w.ax.transData.transform(a.xy)
                assert min(abs(tip[0]-w.ax.bbox.x0),abs(tip[0]-w.ax.bbox.x1),abs(tip[1]-w.ax.bbox.y0),abs(tip[1]-w.ax.bbox.y1))<2
                bbox=a.get_bbox_patch().get_window_extent(w.canvas.get_renderer())
                assert w.ax.bbox.contains(bbox.x0,bbox.y0) and w.ax.bbox.contains(bbox.x1,bbox.y1)
        w.fit_plot();settle()
        # Apply a real saved-node edit, then export the shifted whole drawing.
        kind,xy,_=shape_nodes(w.data,'4/0',0)
        edit=dict(layer='4/0',index=0,original=node_fingerprint(kind,xy),nodes=(xy+[2.,0]).tolist())
        w.apply_node_edits([edit]);settle();assert w.data.node_edits
        states=np.zeros((len(w.case['dies']),3));states[0]=[5,0,0]
        w.manual_states=states;w.request_preview();settle()
        risky=next(r for r in w.current_frame['width_analysis']['rows'] if r['status']!='宽度足够')
        w.selection.apply_hits([risky['refs'][0]],'replace');settle()
        w.set_view((4000,-4000),350,180);w.viewport_changed(force=True);settle();w.canvas.draw()
        pinned=[a for a in w.width_overlay.labels if a.get_visible() and '视野外' in a.get_text()]
        assert pinned
        for a in pinned:
            tip=w.ax.transData.transform(a.xy)
            assert min(abs(tip[0]-w.ax.bbox.x0),abs(tip[0]-w.ax.bbox.x1),abs(tip[1]-w.ax.bbox.y0),abs(tip[1]-w.ax.bbox.y1))<2
            bbox=a.get_bbox_patch().get_window_extent(w.canvas.get_renderer())
            assert w.ax.bbox.contains(bbox.x0,bbox.y0) and w.ax.bbox.contains(bbox.x1,bbox.y1)
        w.selection.clear();w.fit_plot();settle()
        for key,box in w.visible_boxes.items():box.setChecked(key=='4/0')
        settle()
        w.dxf_fields['merge_layers'].setChecked(False)  # Retain the unmerged-node export regression.
        target=folder/'当前 修改.dxf';calls=[]
        def choose(dialog):
            assert dialog.acceptMode()==getattr(W.QFileDialog,'AcceptMode',W.QFileDialog).AcceptSave
            assert not dialog.testOption(getattr(W.QFileDialog,'Option',W.QFileDialog).DontUseNativeDialog)
            assert not dialog.styleSheet()
            calls.append(dialog.windowTitle());dialog.selectFile(str(target));return ACCEPTED
        with patch('die_shift_studio.export_ui.run_dialog',side_effect=choose):w.dxf_button.click();settle()
        assert calls==['DXF输出'] and target.is_file()
        import ezdxf
        document=ezdxf.readfile(target);assert not document.audit().errors
        entities=list(document.modelspace());actual={e.dxf.layer for e in entities}
        assert actual=={'L'+key.replace('/','_D') for key in w.data.layers}
        expected=w.current_frame['layers']['4/0']['polygons'][0]
        polygons=list(document.modelspace().query('LWPOLYLINE[layer=="L4_D0"]'))
        if np.array_equal(expected[0],expected[-1]):expected=expected[:-1]
        np.testing.assert_array_equal(np.array(polygons[0].get_points('xy')),expected)
        record=w.project_settings()
        assert record['dxf_directory']==str(folder) and record['settings']['width_yield_method']=='grid'
        assert record['settings']['width_yield_seed']==912
        saved=folder/'current.dshift.json';w.write_project(saved);w.load_project(saved);settle()
        assert w.dxf_directory==str(folder) and w.view_settings.width_yield_seed==912 and w.data.node_edits
        from die_shift_studio.report import report_document
        if w.result is None:w.run_evaluation();settle()
        html,resources=report_document(w.result);assert '全情况分析' in html and resources
        w.setting_fields['width_yield_method'].setCurrentIndex(w.setting_fields['width_yield_method'].findData('importance'));settle()
        html,resources=report_document(w.result);assert '尾部增强' in html and resources
        if os.environ.get('DSA_GUI_CAPTURE'):
            w.resize(1550,950);w.grab().save(os.environ['DSA_GUI_CAPTURE'])
        print('GUI_V35_PASS on-row fixed centre/no canvas loss, independent saved width integration, pinned labels, native picker, edited shifted DXF/all layers')
    finally:
        w.close()
        until=time.monotonic()+60
        while w._tasks and time.monotonic()<until:app.processEvents();time.sleep(.005)
        app.processEvents();tmp.cleanup()


if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
