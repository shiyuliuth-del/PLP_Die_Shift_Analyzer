"""Real Qt centred yield row, moved bias fields, keepout display and old/new Jobs."""
import json,os,sys,tempfile,time,traceback
from pathlib import Path
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.qt_compat import QtCore,Qt
from die_shift_studio.core import read_gds
from die_shift_studio.width_analysis import BLOCKED


def main():
    app=W.QApplication([]);tmp=tempfile.TemporaryDirectory()
    w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.show();errors=[];w.error=lambda t:errors.append(t)
    def settle():
        end=time.monotonic()+120
        while time.monotonic()<end:
            app.processEvents()
            if not (w._tasks or w.preview_pending or w.motion_pending or w.width_yield.timer.isActive()
                    or w.viewport_timer.isActive() or w.transition.timer.isActive()):break
            time.sleep(.003)
        else:raise AssertionError('V34 worker timeout')
        assert not errors,errors
        app.processEvents()
    def pose(dx):
        w.manual_states=np.array([[dx,0.,0.]])
        w.request_preview();settle()
    q=json.loads((ROOT/'examples/示例R_禁布区贴边与联合良率.json').read_text(encoding='utf-8'))
    q['gds_path']=str(ROOT/'examples'/q['gds_path'])
    q['settings'].update(animation_enabled=False,yield_method='partition',yield_box_budget=256)
    w.set_data(read_gds(Path(q['gds_path'])),q);settle()
    label=w.width_yield.label
    assert label.isVisible() and '良率1' in label.text() and '良率2' in label.text()
    assert '修黄 ' not in label.text() and len(w.width_yield.bank['chunks'])>0
    bank=w.width_yield.bank
    # Coordinates cannot move/resize the yield row, including long negative values.
    for width in (1550,1120):
        w.resize(width,960);settle()
        rect=label.geometry()
        for text in ('x=0 y=0','x=−123456789.123 y=+987654321.456',''):
            w.toolbar.locLabel.setText(text);app.processEvents()
            assert label.geometry()==rect
            lc=label.mapToGlobal(label.rect().center());cc=w.canvas.mapToGlobal(w.canvas.rect().center())
            assert abs(lc.x()-cc.x())<=1,(lc,cc)
            metrics=label.fontMetrics()
            assert max(metrics.horizontalAdvance(t) for t in label.text().splitlines())<=label.width()-8
    assert w.width_yield.bank is bank
    pose(-25)
    data=w.current_frame['width_analysis']
    assert any(v['kind']=='禁布区侵入' for v in data['edge_violations'])
    assert w.width_overlay.edges.get_visible()
    # Visual toggle is independent of numerical results and full-input yield.
    refs=[(r['kind'],r['layer'],r['indices']) for r in data['edge_violations']]
    w.visible_boxes['10/0'].setChecked(False);settle()
    assert w.width_yield.bank is bank
    assert [(r['kind'],r['layer'],r['indices']) for r in w.current_frame['width_analysis']['edge_violations']]==refs
    assert not w.width_overlay.edges.get_visible()
    w.visible_boxes['10/0'].setChecked(True);settle()
    assert w.width_overlay.edges.get_visible()
    pose(46)
    assert any('禁布区' in r['ends'] and r['status']==BLOCKED for r in w.current_frame['width_analysis']['rows'])
    bias=w.setting_sections['系统偏差与相关性']
    assert not bias.isChecked()
    settings_page=w.controls.widget(2)
    assert settings_page.isAncestorOf(w.fields['mean_x_um'])
    assert not w.controls.widget(0).isAncestorOf(w.fields['mean_x_um'])
    w.controls.setCurrentIndex(2);bias.setChecked(True);settle()
    w.fields['mean_x_um'].setValue(3);w.fields['rho_xy'].setValue(.25);settle()
    w.setting_fields['width_display'].boxes['禁布区'].setCurrentIndex(1);settle()
    with patch.object(W.QInputDialog,'getText',return_value=('V34 keepout',True)):w.save_project()
    path=w.project_path;record=json.loads(path.read_text(encoding='utf-8'))
    assert record['parameters']['mean_x_um']==3 and record['parameters']['rho_xy']==.25
    assert record['settings']['width_display']['禁布区']=='dim'
    w.load_project(path);settle()
    assert w.params().mean_x_um==3 and w.params().rho_xy==.25
    assert w.view_settings.width_display['禁布区']=='dim'
    # A V33-style file has no dedicated keepout display key: default Show.
    legacy=json.loads(json.dumps(q));legacy['settings'].pop('width_display',None)
    legacy['parameters'].update(mean_x_um=4,rho_xy=-.4)
    w.set_data(w.data,legacy);settle()
    assert w.params().mean_x_um==4 and w.params().rho_xy==-.4
    assert w.view_settings.width_display['禁布区']=='show'
    w.run_evaluation();settle()
    assert len(w.result['width_policy_yield']['values'])==2
    from die_shift_studio.report import report_document
    html,resources=report_document(w.result)
    assert '整包两种宽度策略良率' in html and '禁布区' in html and resources
    if os.environ.get('DSA_GUI_CAPTURE'):
        w.resize(1550,960);w.controls.setCurrentIndex(0);pose(46);w.grab().save(os.environ['DSA_GUI_CAPTURE'])
    w.close();settle();tmp.cleanup()
    print('GUI_V34_PASS fixed centred two yields, bias settings/old Jobs, keepout intrusion, hide/display independence, report')


if __name__=='__main__':
    try:main()
    except Exception:traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
