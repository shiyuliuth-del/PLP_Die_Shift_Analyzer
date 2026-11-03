"""Compare hot real Qt paths on the same GDS and report source/scale for replay."""
import argparse,json,os,sys,tempfile,time
from pathlib import Path
from statistics import median
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
parser=argparse.ArgumentParser()
parser.add_argument('--gds',type=Path,required=True)
parser.add_argument('--checkout',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args();sys.path.insert(0,str(args.checkout.resolve()))
from die_shift_studio import __version__
from die_shift_studio.gui import MainWindow,W
from die_shift_studio.core import read_gds
app=W.QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    win=MainWindow(jobs_dir=Path(tmp)/'Jobs');win.resize(1360,850);win.show()
    errors=[];win.error=lambda msg:errors.append(str(msg))
    def wait():
        until=time.monotonic()+300
        while time.monotonic()<until:
            app.processEvents()
            if not any((win.worker,win.preview_workers,win.preview_pending,win.motion_worker,win.motion_pending,win.hardware_worker,
                        win.transition.timer.isActive(),win.viewport_timer.isActive(),win.coverage_timer.isActive(),win._coverage_pending)):break
            time.sleep(.001)
        else:raise TimeoutError('Qt request timeout')
        if errors:raise RuntimeError(errors)
    data=read_gds(args.gds)
    roles={k:{'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}.get(k,'不参与') for k in data.layers}
    win.set_data(data,dict(schema_version=5,gds_path=str(args.gds),roles=roles,layer_order=['6/0','5/0','4/0','3/0'],parameters={},
                          settings=dict(animation_enabled=False),extreme_view=dict(mode='all',page=1)));wait()
    win.run_evaluation();wait()
    def select(row):
        win.extreme_table.setCurrentCell(row,0);wait()
    select(1);select(0)
    def timing(fn,repeats):
        values=[]
        for _ in range(repeats):
            start=time.perf_counter();fn();values.append((time.perf_counter()-start)*1000)
        return median(values)
    p=win.params();order=win.ordered_layers();motions=win.motions()
    timings=dict(signature_ms=timing(lambda:win.preview_identity(data,roles,order,motions,p,win.result),31),
                 cache_color_ms=timing(lambda:win.refresh_extreme_colors(win.current_frame),31),
                 cached_round_trip_ms=timing(lambda:(select(1),select(0)),7))
    stats=win.result['contact_analysis']
    record=dict(version=__version__,gds_sha256=data.sha256,extreme_rows=win.extreme_table.rowCount(),
                package_states=win.result['discrete_states'],contact_pairs=len(stats.get('locations',[])),
                low_pairs=sum(q['low'] for q in stats.get('locations',[])),witnessed_low_pairs=len({(q['source_layer'],q['source_index'],q['target_layer'],q['target_index']) for q in stats.get('low_issues',[])}),
                timings=timings,scope='Linux offscreen Qt at 1360×850, full catalogue, without motion/width overlays, animations disabled.')
    print(record,flush=True);args.out.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    win.close();app.processEvents()
