"""Actual Qt render and responsive preview timings for GD55 and fanout example."""
import os,sys,time,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import clean_json
app=W.QApplication([]);w=MainWindow(jobs_dir=ROOT/'Jobs');w.show();errors=[];w.error=lambda text:errors.append(text)
def wait():
    deadline=time.monotonic()+80
    while time.monotonic()<deadline:
        app.processEvents()
        if w.worker is None and w.preview_worker is None and w.preview_pending is None:break
        time.sleep(.002)
    assert w.worker is None and w.preview_worker is None and w.preview_pending is None
    assert not errors,errors
    app.processEvents();w.canvas.draw()
def shot(name):w.grab().save(str(ROOT/'figures'/f'software_v070_{name}.png'))
metrics={}
real=ROOT/'Jobs/GD55_用户确认用途.dshift.json'
if real.exists() and Path('/home/niubi/下载/GD55_9-21.gds').exists():
    w.load_project(real);wait();w.run_evaluation();wait();w.space_toggle.setChecked(True);wait()
    records=[];w.extreme_table.setCurrentCell(1,0);wait();artists=list(w.space_artists)
    for row in [2,3,4,5,6,7,8,9]:
        t=time.perf_counter();w.extreme_table.setCurrentCell(row,0);wait();records.append((time.perf_counter()-t)*1000)
    assert all(a is b for a,b in zip(artists,w.space_artists))
    metrics['GD55_Qt_preview']=dict(median_ms=float(np.median(records)),p95_ms=float(np.quantile(records,.95)),samples_ms=records,
        interface_count=len(w.engine.contact.records),owners=[int((w.case['ownership']['4/0']==i).sum()) for i in range(3)],space_artist_reused=True)
    w.issue_scheme.setCurrentIndex(2);w.fit_plot();w.canvas.draw();shot('GD55_space')
    w.preview_tabs.setCurrentIndex(1);app.processEvents();shot('GD55_info')
    w.tabs.setCurrentWidget(w.analysis_page);app.processEvents();shot('GD55_analysis')
    w.tabs.setCurrentIndex(0);w.preview_tabs.setCurrentIndex(0)
w.load_project(ROOT/'Jobs/示例D_芯片外扇出.dshift.json');wait()
w.setting_fields['probability_method'].setCurrentIndex(w.setting_fields['probability_method'].findData('quadrature'));wait()
w.run_evaluation();wait();w.space_toggle.setChecked(True);wait()
w.manual_die.setCurrentIndex(0);w.manual_fields['dx'].setValue(65);w.manual_fields['dy'].setValue(0);w.manual_fields['angle'].setValue(0);w.apply_manual();wait();w.issue_scheme.setCurrentIndex(2)
assert w.current_frame['contact']['intrusions'];w.fit_plot();w.canvas.draw();shot('fanout')
w.probability_toggle.setChecked(True);wait();assert w.probability_data['samples']==0
w.space_toggle.setChecked(False);w.set_view([55,1000],260,170);w.canvas.draw();shot('fanout_detail')
assert any(a.get_alpha()==w.view_settings.intrusion_alpha for a in w.issue_artists)
w.controls.setCurrentIndex(2);app.processEvents();shot('settings')
metrics['fanout_Qt']=dict(interfaces=len(w.engine.contact.records),owners=w.case['ownership']['4/0'].tolist(),probability_method=w.probability_data['method'],probability_samples=w.probability_data['samples'],
    intrusion_area_um2=sum(q['area_um2'] for q in w.current_frame['contact']['intrusions']))
(ROOT/'results/GUI_v070.json').write_text(json.dumps(clean_json(metrics),ensure_ascii=False,indent=2)+'\n')
w.close();app.processEvents();print(json.dumps(metrics,ensure_ascii=False,indent=2))
