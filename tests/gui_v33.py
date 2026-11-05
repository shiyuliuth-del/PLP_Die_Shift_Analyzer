"""Real Qt right-click menu, width-policy display, persistence and stale work."""
import json,os,sys,tempfile,time,traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt,BINDING
from die_shift_studio.core import read_gds
from die_shift_studio.width_overlay import IGNORED_COLOR
from die_shift_studio.width_display import low_ink
from matplotlib.colors import to_rgb
if BINDING=='PyQt6':from PyQt6.QtTest import QTest
else:from PyQt5.QtTest import QTest

def main():
    app=W.QApplication([]);tmp=tempfile.TemporaryDirectory()
    w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda t:errors.append(t)
    def settle():
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            app.processEvents()
            if not (w._tasks or w.preview_pending or w.motion_pending or w.width_yield.timer.isActive()
                    or w.viewport_timer.isActive() or w.transition.timer.isActive()):break
            time.sleep(.003)
        else:raise AssertionError('V33 worker timeout')
        assert not errors,errors
        app.processEvents()
    def pixel(xy):
        x,y=w.ax.transData.transform(xy);ratio=w.canvas.devicePixelRatioF()
        return QtCore.QPoint(round(x/ratio),round(w.canvas.height()-y/ratio))
    def right(xy):
        QTest.mouseClick(w.canvas,getattr(Qt,'MouseButton',Qt).RightButton,pos=pixel(xy))
        app.processEvents()
        return W.QApplication.activePopupWidget()
    def dismiss():
        q=W.QApplication.activePopupWidget()
        if q:q.close()
        app.processEvents()
    q=json.loads((ROOT/'examples/示例O_运动网络三线路通道.json').read_text())
    q['gds_path']=str(ROOT/'examples'/q['gds_path'])
    q.setdefault('settings',{}).update(animation_enabled=False,yield_sample_exponent=8)
    q['parameters'].update(target_nsigma=2,bound_angle_deg=0)
    q['overlays']={'width':True}
    w.set_data(read_gds(Path(q['gds_path'])),q);settle()
    assert w.width_yield.bank is not None and '原图' in w.width_yield.label.text()
    assert '99%' in w.width_yield.label.toolTip()
    w.run_evaluation();settle()
    assert 'width_policy_yield' in w.result
    w.manual_states=np.array([[3.,0,0],[0.,0,0]]);w.request_preview();settle()
    row=next(r for r in w.current_frame['width_analysis']['rows'] if r['status']=='需局部收窄')
    ref=row['refs'][0]
    # Hidden other layers prevent an overlapping pad from being picked instead.
    visibility={k:b.isChecked() for k,b in w.visible_boxes.items()}
    for k,b in w.visible_boxes.items():b.setChecked(k==ref[0])
    settle();w.fit_plot();w.canvas.draw()
    poly=w.geometry_cache.layers[ref[0]]['geometry'][ref[1]]
    w.selection.clear();bounds=(w.ax.get_xlim(),w.ax.get_ylim())
    menu=right(poly.representative_point().coords[0])
    assert menu is not None and w.selection.selected==[ref]
    assert any('名义节点' in a.text() for a in menu.actions())
    action=next(a for a in menu.actions() if a.text()=='忽略选中的问题线路')
    old_bank=w.width_yield.bank;action.trigger();dismiss();settle()
    from die_shift_studio.report import report_document
    html,resources=report_document(w.result)
    assert '整包三种宽度策略良率' in html and resources
    assert ref in w.ignored_width_lines and w.width_yield.bank is old_bank
    np.testing.assert_allclose((w.ax.get_xlim(),w.ax.get_ylim()),bounds)
    for k,on in visibility.items():w.visible_boxes[k].setChecked(on)
    settle()
    ink=w.width_overlay.outlines.get_edgecolors()
    assert any(np.allclose(color[:3],candidate) for color in ink for candidate in (to_rgb(IGNORED_COLOR),low_ink(IGNORED_COLOR,True)[:3])),ink
    # Existing selection has priority, including a click away from its source.
    menu=right([250,210]);assert menu is not None and w.selection.selected==[ref]
    assert any(a.text()=='取消忽略选中的问题线路' for a in menu.actions());dismiss()
    w.selection.clear();assert right([250,210]) is None
    np.testing.assert_allclose((w.ax.get_xlim(),w.ax.get_ylim()),bounds)
    # All routes on the chosen risk are now ignored, so its annotation is absent.
    all_refs={r for item in w.current_frame['width_analysis']['rows'] if item['status']!='宽度足够' for r in item['refs']}
    w.change_width_ignores(all_refs,True);settle()
    assert not any(a.get_visible() for a in w.width_overlay.labels)
    with patch.object(W.QInputDialog,'getText',return_value=('width choices',True)):
        w.save_project()
    if os.environ.get('DSA_GUI_CAPTURE'):
        w.grab().save(os.environ['DSA_GUI_CAPTURE'])
    saved=w.project_path;record=json.loads(saved.read_text())
    assert record['ignored_width_lines']['refs']
    w.load_project(saved);settle();assert w.ignored_width_lines==all_refs
    # M/pose changes and visibility must reuse the same whole-input distribution.
    bank=w.width_yield.bank
    with patch('die_shift_studio.width_yield_display.measure_width_yield',side_effect=AssertionError('recomputed cached global probability')):
        w.manual_states=np.array([[-7.,2,0],[0,0,0]]);w.request_preview();settle()
        w.width_yield.refresh();assert w.width_yield.bank is bank
        w.change_width_ignores(all_refs,False);settle()
        w.target_sigma.setCurrentText('∞');settle()
        assert w.width_yield.bank is bank and w.width_yield.bank['scale_nsigma']==2
        assert w.setting_fields['yield_method'].isEnabled()
        assert not w.setting_fields['probability_show_ranges'].isEnabled()
    assert not w.ignored_width_lines
    # A stale completion from a prior GDS cannot update the active labels.
    w.width_yield.reset()
    with patch('die_shift_studio.width_yield_display.measure_width_yield',side_effect=lambda *a,**k: (time.sleep(.08),old_bank)[1]):
        w.width_yield.refresh();w.width_yield.timer.stop();w.width_yield.start()
        legacy=dict(q);legacy['overlays']={}
        w.set_data(w.data,legacy)
    settle();assert not w.ignored_width_lines and not w.width_yield.action.isVisible()
    w.close();settle();tmp.cleanup()
    print('GUI_V33_PASS actual right-click pick/menu, blank click, green ignore, cached policy updates, saved/old jobs, stale worker')

if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
