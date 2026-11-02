"""Actual Qt: import visibility, stable splitter drags and graded probability."""
import os,sys,time,tempfile,json
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W,QtCore
from die_shift_studio.qt_compat import Qt
from PyQt5.QtTest import QTest
app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1680,1080);w.show();errors=[];w.error=lambda e:errors.append(str(e))
def wait():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents()
        if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):break
        time.sleep(.003)
    else:raise AssertionError('V16 GUI timeout')
    assert not errors,errors
wait();w.load_gds(ROOT/'examples/示例G_小偏移与三段追溯.gds');wait()
assert set(w.roles().values())=={'不参与'}
for key in ('5/0','127/0','128/0'):
    w.visible_boxes[key].setChecked(True);wait()
    assert w.layer_artists[key].get_visible() and len(w.layer_artists[key].get_paths())
    w.visible_boxes[key].setChecked(False);wait();assert not w.layer_artists[key].get_visible()
# Borders without RDL must still be viewable (original bug).
w.role_boxes['127/0'].setCurrentText('封装边框');w.role_boxes['128/0'].setCurrentText('芯片边框');wait()
assert w.params().panel_layers==w.params().wafer_layers==0
w.visible_boxes['5/0'].setChecked(True);wait();assert len(w.layer_artists['5/0'].get_paths())
assert all(a.get_visible()==w.visible_boxes[k].isChecked() for k,a in w.layer_artists.items())
assert 'Ctrl' in w.shortcut_label.text() and 'ESC' in w.shortcut_label.text()
w.load_project(ROOT/'Jobs/示例G_小偏移与三段追溯.dshift.json');wait();w.run_evaluation();wait()
assert w.result['model_yield']['lower']<=w.result['model_yield']['estimate']<=w.result['model_yield']['upper']
assert not hasattr(w,'verdict') and not hasattr(w,'safe_range_caption')
chosen=w.result['recommendation']['scheme'];assert chosen is not None
assert all(w.scheme_table.item(chosen-1,j).background().color().name()=='#fff0d9' for j in range(4))
for i,status in enumerate(w.result['extreme_validation'],1):
    assert ('通过已检查规则' in w.extreme_table.item(i,0).toolTip()) == (status['safe'] is True)
# Physics/data unaffected by alpha; no default rectangular envelope artist.
w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);wait()
cmap=w.probability_colormap();alphas=cmap(np.array([0.,.001,.01,.1,1.]))[:,3]
assert alphas[0]==0 and np.all(np.diff(alphas)>0)
assert len(w.probability_artists)==len(w.probability_data['maps'])
for q in w.probability_data['maps']:
    assert q['edge_probability']<1e-10 and q['convolution_mass_error']<1e-5
w.grab().save(str(ROOT/'figures/software_V16_probability.png'))
# Switching states/pages must preserve user sizes and reuse histogram artists.
old=None
for row in (1,2,3,4):
    w.extreme_table.setCurrentCell(row,0);wait();w.tabs.setCurrentWidget(w.analysis_page);wait()
    if old is not None:assert w._histogram_axes[0] is old
    old=w._histogram_axes[0]
    w.analysis_split.setSizes([250,400,220]);app.processEvents();before=w.analysis_split.sizes()
    # Real drag; allow no expensive hidden GDS redraw in the analysis tab.
    with patch.object(w,'_plot_layout',side_effect=AssertionError('hidden layout redraw')):
        w.plot_layout(preserve=True);w.analysis_split.moveSplitter(w.analysis_split.sizes()[0]+32,1);app.processEvents()
    assert abs(w.analysis_split.sizes()[0]-before[0])>=15,w.analysis_split.sizes()
    sizes=w.analysis_split.sizes();w.tabs.setCurrentIndex(0);wait();w.tabs.setCurrentWidget(w.analysis_page);wait()
    assert w.analysis_split.sizes()==sizes
w.analysis_split.setSizes([250,490,0]);wait();w.grab().save(str(ROOT/'figures/software_V16_analysis.png'))
assert len(w.safe_range_figure.axes)==2
assert any('%）' in t.get_text() for t in w.safe_range_figure.texts)
# A genuinely generous nominal pad with tiny shifts must receive green rows.
for key,value in [('annulus_um',.1),('multi_bound_x_um',.01),('multi_bound_y_um',.01),('bound_angle_deg',.0001)]:
    w.fields[key].setValue(value)
wait();w.run_evaluation();wait()
assert any(q['safe'] is True for q in w.result['extreme_validation'])
assert any('通过已检查规则' in w.extreme_table.item(i,0).toolTip() for i in range(1,w.extreme_table.rowCount()))
w.close();app.processEvents();wait();tmp.cleanup();print('GUI_V16_PASS',flush=True)
