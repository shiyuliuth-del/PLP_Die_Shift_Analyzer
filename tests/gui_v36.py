"""Saved output controls and same-probability colour/text in nominal and moved views."""
import os,sys,json,tempfile,time,traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np,ezdxf
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import ACCEPTED
from die_shift_studio.core import read_gds
from die_shift_studio.width_risk_probability import probability_text,lookup

def main():
    app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();folder=Path(tmp.name)
    w=MainWindow(jobs_dir=folder/'Jobs');w.resize(1500,950);w.show()
    errors=[];w.error=lambda text:errors.append(str(text))
    def settle():
        until=time.monotonic()+180
        while time.monotonic()<until:
            app.processEvents()
            if not (w._tasks or w.preview_pending or w.motion_pending or w.width_yield.timer.isActive()
                    or w.viewport_timer.isActive() or w.transition.timer.isActive() or w.coverage_timer.isActive()):break
            time.sleep(.003)
        else:raise AssertionError('V36 timeout')
        app.processEvents();assert not errors,errors
        assert '未处理异常' not in w.runtime_view.toPlainText(),w.runtime_view.toPlainText()[-2000:]
    try:
        assert '宽度分析' in w.setting_sections
        assert not any(k.startswith('宽度分析 ·') for k in w.setting_sections)
        assert not any(h.isChecked() for h in w.setting_sections.values())
        assert w.setting_fields['width_yield_method'].currentData()=='grid'
        assert w.dxf_fields['merge_layers'].isChecked()
        q=json.loads((ROOT/'examples/示例O_运动网络三线路通道.json').read_text())
        q['gds_path']=str(ROOT/'examples'/q['gds_path'])
        q['parameters'].update(multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=2)
        q['settings'].update(animation_enabled=False,width_grid_nodes=5)
        q['settings'].pop('width_yield_method',None)
        q['expanded_settings']=['宽度分析 · 元素显示']
        q['overlays']={'width':True}
        w.set_data(read_gds(q['gds_path']),q);settle();w.run_evaluation();settle()
        assert w.setting_sections['宽度分析'].isChecked()
        bank=w.width_yield.bank;assert bank['method']=='grid' and bank['probability_lines']
        summary=w.current_frame['width_summary'];ref=next(iter(summary['lines']))
        w.selection.apply_hits([ref],'replace');settle();w.canvas.draw()
        texts=[a.get_text() for a in w.width_overlay.labels if a.get_visible()]
        assert texts and all('问题概率：' in text for text in texts)
        value=w.width_overlay.problem_probability(summary['witnesses'][ref])
        assert probability_text(value,6) in texts[0]
        # Exercise actual paint alpha while probability changes by orders of magnitude.
        luminance=[];alphas=[];nominal_frame=w.current_frame
        for probability in (1e-9,.8):
            evidence=dict(summary['lines']);evidence[ref]=(evidence[ref][0],'exceeded' if probability==1e-9 else 'undetermined')
            w.current_frame=dict(nominal_frame,width_summary=dict(summary,lines=evidence))
            local=dict(bank,probability_lines=dict(bank['probability_lines']))
            local['probability_lines'][ref]=dict(p=probability,success=1-probability,method='grid')
            w.width_yield.bank=local;w.width_yield.refresh_overlay();w.canvas.draw()
            rgba=w.width_overlay.outlines.get_facecolors()
            visible_refs=[key for key in summary['lines'] if key[0] in w.visible_boxes and w.visible_boxes[key[0]].isChecked()]
            index=visible_refs.index(ref);color=rgba[index]
            alphas.append(color[3]);luminance.append(color[:3]@np.array([.2126,.7152,.0722]))
        assert alphas[0]==alphas[1]==.20 and luminance[1]<luminance[0]
        w.current_frame=nominal_frame;w.width_yield.bank=bank;w.width_yield.refresh_overlay()
        frame=w.current_frame
        with patch.object(w,'request_preview',side_effect=AssertionError('display control requested geometry')):
            w.setting_fields['width_yield_decimals'].setValue(3);settle()
            w.setting_fields['width_show_probability'].setChecked(False);settle()
            assert all('问题概率：' not in a.get_text() for a in w.width_overlay.labels if a.get_visible())
            w.setting_fields['width_show_probability'].setChecked(True);settle()
        assert w.width_yield.bank is bank and w.current_frame is frame
        outline_mode=w.setting_fields['width_display'].boxes['描边']
        outline_mode.setCurrentIndex(outline_mode.findData('dim'));settle()
        assert np.allclose(w.width_overlay.outlines.get_facecolors()[:,3],.20*.34)
        outline_mode.setCurrentIndex(outline_mode.findData('show'));settle()
        assert any(probability_text(value,3) in a.get_text() for a in w.width_overlay.labels if a.get_visible())
        states=np.zeros((len(w.case['dies']),3));states[0,0]=30
        w.manual_states=states;w.request_preview();settle()
        risk=next(r for r in w.current_frame['width_analysis']['rows'] if r['status']!='宽度足够')
        w.selection.apply_hits([risk['refs'][0]],'replace');settle()
        assert w.width_yield.bank is bank
        labels=[a for a in w.width_overlay.labels if a.get_visible()]
        assert labels and all('问题概率：' in a.get_text() for a in labels)
        assert np.allclose(w.width_overlay.outlines.get_facecolors()[:,3],.20)
        outline_mode.setCurrentIndex(outline_mode.findData('dim'));settle()
        assert np.allclose(w.width_overlay.outlines.get_facecolors()[:,3],.20*.34)
        outline_mode.setCurrentIndex(outline_mode.findData('show'));settle()
        w.set_view((4000,4000),400,250);w.viewport_changed(force=True);settle();w.canvas.draw()
        assert any('视野外' in a.get_text() and '问题概率：' in a.get_text() for a in w.width_overlay.labels if a.get_visible())
        for a in w.width_overlay.labels:
            if not a.get_visible():continue
            b=a.get_bbox_patch().get_window_extent(w.canvas.get_renderer())
            assert w.ax.bbox.contains(b.x0,b.y0) and w.ax.bbox.contains(b.x1,b.y1)
        w.fit_plot();settle()
        # Explicit visible-only export is independent of numerical visibility.
        for key,check in w.visible_boxes.items():check.setChecked(key=='4/0')
        before=w.width_yield.bank;settle()
        w.dxf_fields['layer_scope'].setCurrentIndex(w.dxf_fields['layer_scope'].findData('visible'))
        w.dxf_fields['polygon_mode'].setCurrentIndex(w.dxf_fields['polygon_mode'].findData('hatch'))
        w.dxf_fields['show_fill'].setChecked(True);w.dxf_fields['scale'].setValue(2)
        target=folder/'输出 现态.dxf'
        def picker(dialog):
            assert not dialog.styleSheet()
            assert not dialog.testOption(getattr(W.QFileDialog,'Option',W.QFileDialog).DontUseNativeDialog)
            dialog.selectFile(str(target));return ACCEPTED
        with patch('die_shift_studio.export_ui.run_dialog',side_effect=picker):w.dxf_button.click();settle()
        doc=ezdxf.readfile(target);assert doc.header['$FILLMODE']==1 and doc.units==13
        assert all(e.dxf.layer=='L4_D0' and e.dxftype()=='HATCH' for e in doc.modelspace())
        assert w.width_yield.bank is before
        w.setting_sections['DXF输出参数'].setChecked(True)
        w.setting_fields['width_show_probability'].setChecked(False);settle()
        path=folder/'saved.dshift.json';w.write_project(path);record=json.loads(path.read_text())
        assert record['dxf_options']['scale']==2 and record['dxf_options']['layer_scope']=='visible'
        w.load_project(path);settle()
        assert w.dxf_settings.scale==2 and w.setting_sections['DXF输出参数'].isChecked()
        assert not w.view_settings.width_show_probability
        assert w.view_settings.width_yield_method=='grid'
        q.pop('expanded_settings');q['settings'].pop('width_show_probability',None)
        w.set_data(read_gds(q['gds_path']),q);settle()
        assert w.view_settings.width_show_probability and w.dxf_settings.scale==1 and w.dxf_settings.merge_layers
        if os.environ.get('DSA_GUI_CAPTURE'):w.grab().save(os.environ['DSA_GUI_CAPTURE'])
        print('GUI_V36_PASS merged settings/default grid, saved export preferences, native picker, fixed-alpha probability fills, labels/precision/offscreen/cache and legacy defaults')
    finally:
        w.close();until=time.monotonic()+60
        while w._tasks and time.monotonic()<until:app.processEvents();time.sleep(.005)
        app.processEvents();tmp.cleanup()

if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
