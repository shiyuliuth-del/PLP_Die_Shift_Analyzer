"""Reproduce one cold GD55 run against the current checkout or an archived V25."""
import argparse
import json
import os
import platform
from pathlib import Path
import sys
import time

parser=argparse.ArgumentParser()
parser.add_argument('--gds',type=Path,required=True)
parser.add_argument('--checkout',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
sys.path.insert(0,str(args.checkout.resolve()))
import numpy as np
from die_shift_studio import __version__
from die_shift_studio.acceleration import capabilities
from die_shift_studio.core import Parameters,build_case,read_gds,evaluate
from die_shift_studio.extreme_validation import evaluate_states,frame_status
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.prepared_motion import prepare_motion
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.resources import resource_profile
from die_shift_studio.session_cache import SessionCache
from die_shift_studio.settings import ViewSettings
from die_shift_studio.stack import Connections
from die_shift_studio.statistics import ExtremeCatalog

p=Parameters();cfg=ViewSettings(animation_duration_ms=180);timings={}
def measure(key,fn):
    start=time.perf_counter();value=fn();timings[key]=round(time.perf_counter()-start,6)
    print(key,timings[key],flush=True);return value

d=measure('import',lambda:read_gds(args.gds));known={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL',
                                                    '4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
roles={k:known.get(k,'不参与') for k in d.layers};order=['6/0','5/0','4/0','3/0']
cache=GeometryCache(d);con=Connections(d,roles,order,cache.geometries);case=build_case(d,roles,connections=con)
def engine():return PreviewEngine(cache,case,roles,{},p,None,con)
z=np.zeros((len(case['dies']),3));end=np.tile([-35.,-35.,-np.pi/180],(len(z),1))
e=engine();e.signature='bench';e.configure_width(True,cfg);bundle=SessionCache()
try:
    a=measure('width_cold_nominal',lambda:e.frame(z))
    b=measure('width_first_shift',lambda:e.frame(end))
    measure('width_repeat',lambda:e.frame(end))
    packet=measure('width_animation_cold',lambda:prepare_motion(e,z,end,cfg,10000,bundle,lambda *_:None))
    replay=measure('width_animation_replay',lambda:prepare_motion(e,end,z,cfg,10000,bundle,lambda *_:None))
    counts=[v['width_analysis']['counts'] for v in (a,b)]
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import PolyCollection
    fig=Figure(figsize=(9,5),dpi=100);canvas=FigureCanvasAgg(fig);ax=fig.subplots()
    for k in ('3/0','4/0','5/0','6/0'):
        ax.add_collection(PolyCollection(b['layers'][k]['polygons'],alpha=.6,linewidths=.25))
    ax.autoscale();ax.set_aspect('equal');canvas.draw()
    measure('agg_full_draw',canvas.draw)
finally:e.close();bundle.close()
r=measure('scheme_core',lambda:evaluate(d,roles,p,layer_order=order,connections=con,settings=cfg))
e=engine();grid=ExtremeCatalog(case,p);focus=RepresentativeExtremes.from_result(case,p,r,grid) if __version__!='V25' else RepresentativeExtremes(case,p)
try:
    checks=measure('extreme_checks',lambda:evaluate_states(e,focus,cfg))
    verify=[]
    if isinstance(checks,dict):
        from die_shift_studio.extreme_validation import status_for
        rng=np.random.default_rng(26)
        digits=[[0]*len(z),*[list(map(int,rng.integers(0,27,len(z)))) for _ in range(15)]]
        for ids in digits:
            pose=np.array([v[d] for v,d in zip(grid.locals,ids)])
            full=frame_status(e.frame(pose));summed=status_for(checks,ids)
            assert all(full[k]==summed[k] for k in ('checked','safe','failures','contact','drc')),(ids,full,summed)
            verify.append(ids)
finally:e.close()
out=dict(version=__version__,source_sha256=d.sha256,shape_count=sum(map(len,d.layers.values())),dies=len(z),platform=platform.system(),
         python=platform.python_version(),hardware=resource_profile(),cuda=capabilities(),openblas_threads=os.environ.get('OPENBLAS_NUM_THREADS'),
         pose=[-35,-35,-1],rules_um=[15,15,15],fps=cfg.animation_fps,duration_limit_ms=cfg.animation_duration_ms,
         intermediate_frames=packet['count'],reverse_cached=replay['cached'],timings_s=timings,width_counts=counts,
         scheme_scores=[q['gamma'] for q in r['schemes']],recommendation=r['recommendation']['scheme'],
         yield_estimate=r['model_yield']['estimate'],focused_shifted_rows=focus.total,
         grid_summary={k:checks[k] for k in ('local_states','batches','pair_states','package_states')} if isinstance(checks,dict) else {'checked_shifted_rows':len(checks)},
         direct_comparisons=len(verify))
args.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
