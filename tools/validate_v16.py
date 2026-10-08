"""Read-only GD55 evidence: current Qt, full-input probability and warmed draws."""
import argparse,hashlib,json,os,sys,tempfile,time,statistics
from pathlib import Path
from dataclasses import asdict
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import read_gds,clean_json
from tools.validate_gd55_v14 import ROLES,ORDER

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--reuse-assessment',action='store_true');a=parser.parse_args()
    before=hashlib.sha256(a.gds.read_bytes()).hexdigest();app=W.QApplication([]);tmp=tempfile.TemporaryDirectory();w=MainWindow(jobs_dir=Path(tmp.name)/'Jobs');w.resize(1680,1140);w.show();errors=[];w.error=lambda e:errors.append(str(e))
    def wait():
        end=time.monotonic()+480
        while time.monotonic()<end:
            app.processEvents()
            if not any((w.worker,w.preview_worker,w.preview_pending,w.motion_worker,w.motion_pending,w.hardware_worker,w.coverage_timer.isActive(),w._coverage_pending,w.viewport_timer.isActive(),w.transition.timer.isActive())):break
            time.sleep(.003)
        else:raise AssertionError('GD55 Qt timeout')
        assert not errors,errors
    def measure(fn,n=1):
        values=[]
        for _ in range(n):
            start=time.perf_counter();fn();app.processEvents();values.append(1000*(time.perf_counter()-start))
        return dict(samples_ms=values,median_ms=statistics.median(values))
    wait();d=read_gds(a.gds);w.set_data(d,{});wait();assert set(w.roles().values())=={'不参与'}
    for key in d.layers:
        w.visible_boxes[key].setChecked(True);wait();assert len(w.layer_artists[key].get_paths())>0,key
        w.visible_boxes[key].setChecked(False);wait();assert not w.layer_artists[key].get_visible()
    parameters=dict(multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1,line_width_um=20,line_space_um=20,annulus_um=15)
    w.set_data(d,dict(schema_version=5,roles=ROLES,layer_order=ORDER,parameters=parameters));wait()
    t=time.perf_counter()
    if a.reuse_assessment:
        saved=json.loads((ROOT/'results/assessment_GD55_V16.json').read_text());assert saved['source_sha256']==before;w.display_result(saved)
    else:w.run_evaluation()
    wait();assessment_ms=None if a.reuse_assessment else 1000*(time.perf_counter()-t)
    (ROOT/'results/assessment_GD55_V16.json').write_text(json.dumps(clean_json(w.result),ensure_ascii=False,indent=2)+'\n')
    assert w.result['model_yield']['available'] and len(w.result['model_yield']['rows'])==3
    w.tabs.setCurrentWidget(w.analysis_page);wait();w.analysis_split.setSizes([275,650,0]);wait();w.grab().save(str(ROOT/'figures/software_V16_GD55_analysis.png'))
    w.tabs.setCurrentIndex(0);wait();w.probability_toggle.setChecked(True);w.space_toggle.setChecked(True);w.drc_toggle.setChecked(True);wait()
    maps=w.probability_data['maps'];assert len(maps)==158
    assert all(q['edge_probability']<1e-10 and q['convolution_mass_error']<1e-5 for q in maps)
    w.ax.set_xlim(-640,-350);w.ax.set_ylim(-1010,-700);w.plot_layout(preserve=True);wait();w.grab().save(str(ROOT/'figures/software_V16_GD55_probability.png'))
    w.extreme_table.setCurrentCell(4,0);wait()
    warmed=measure(lambda:w.plot_layout(preserve=True),15)
    w.tabs.setCurrentWidget(w.analysis_page);wait();hidden=measure(lambda:w.plot_layout(preserve=True),30)
    first=w.analysis_split.sizes()[0];direction=[1]
    def resize_analysis():
        direction[0]*=-1;w.analysis_split.moveSplitter(first+direction[0]*20,1)
    drag=measure(resize_analysis,6)
    q=w.result['model_yield'];assert q['lower']<=q['estimate']<=q['upper']
    from die_shift_studio.safe_range_plot import draw_safe_ranges
    import matplotlib.pyplot as plt
    fig=plt.figure(figsize=(12,6.7));draw_safe_ranges(fig,w.result['safe_ranges'],q)
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/('V16_safe_ranges.'+ext),dpi=300,bbox_inches='tight')
    plt.close(fig)
    from die_shift_studio.resources import resource_profile
    out=dict(version='V16',sha256=before,parameters=parameters,hardware=resource_profile(),assessment_including_extremes_ms=assessment_ms,
        visible_overlays_cached_draw=warmed,hidden_layout_update=hidden,analysis_splitter_update=drag,
        interfaces=len(maps),green_extreme_rows=sum(r['safe'] is True for r in w.result['extreme_validation']),
        extreme_rows=len(w.result['extreme_validation']),yield_result=q,
        scope='Linux shared host, offscreen Qt 1680×1140. Cached draws include fixed-RDL DRC, via probability and Space Map at detail zoom; no cold deformation claim.',
        probability_max_edge=max(q['edge_probability'] for q in maps),probability_max_mass_error=max(q['convolution_mass_error'] for q in maps),
        probability_max_raster_area_error=max(q['raster_area_relative_error'] for q in maps))
    w.close();app.processEvents();wait();tmp.cleanup();assert hashlib.sha256(a.gds.read_bytes()).hexdigest()==before
    (ROOT/'results/gui_GD55_V16.json').write_text(json.dumps(clean_json(out),ensure_ascii=False,indent=2)+'\n')
    print('GD55_V16_PASS',json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
