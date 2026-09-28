"""Reproducible numerical/preview timing; read-only attachment, no frozen build."""
import argparse,sys,json,time,statistics,os
from pathlib import Path
import numpy as np
parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--output');args=parser.parse_args()
ROOT=Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters,evaluate
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.contact import ContactModel
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.resources import resource_profile
from die_shift_studio import __version__

def elapsed(fn):
 t=time.perf_counter();out=fn();return out,1000*(time.perf_counter()-t)
q=json.loads((ROOT/'examples/示例E_连续型变与约束.json').read_text());d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);cache=GeometryCache(d);m=ContactModel(d,c,q['roles'],c['connections'],cache);res=evaluate(d,q['roles'],p,connections=c['connections'])
def sequence():
 e=PreviewEngine(cache,c,q['roles'],{},p,res,c['connections']);e.configure_deformation(True,ViewSettings());times=[]
 for t in np.linspace(0,1,25):
  f,ms=elapsed(lambda:e.frame(np.tile([35*t,35*t,.01*t],(3,1))));times.append(ms)
 return dict(total_ms=sum(times),first_nonzero_frame_ms=times[1],median_ms=statistics.median(times),frames=len(times),accepted=f['deformation']['accepted_count'])
report=dict(version=__version__,hardware=resource_profile(),affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
 deformation_sequence=sequence())
path=Path('/home/niubi/下载/GD55_9-21.gds')
if path.exists():
 roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
 order=['6/0','5/0','4/0','3/0','128/0','127/0'];data,load=elapsed(lambda:read_gds(path));cc=build_case(data,roles,layer_order=order);gc=GeometryCache(data);pp=Parameters();mm=ContactModel(data,cc,roles,cc['connections'],gc)
 rr,evalms=elapsed(lambda:evaluate(data,roles,pp,connections=cc['connections']))
 ee=PreviewEngine(gc,cc,roles,{},pp,rr,cc['connections']);ee.configure_deformation(True,ViewSettings());z=np.tile([35.,-35,.01],(len(cc['dies']),1))
 ff,frame_ms=elapsed(lambda:ee.frame(z));cfg=ViewSettings(sample_exponent=10)
 heat,heat_ms=elapsed(lambda:probability_maps(gc,mm.bound,roles,pp,masks=mm.interface_masks,settings=cfg))
 report['GD55']=dict(sha256=data.sha256,load_ms=load,evaluation_ms=evalms,frame_ms=frame_ms,probability_ms=heat_ms,
   interfaces=len(mm.records),accepted=ff['deformation']['accepted_count'],remaining=ff['deformation']['blocked_count'],released=ff['deformation'].get('released_count'),
   grid_count=len(heat['maps']),max_area_error=max(abs(q['integrated_area_um2']/q['via_area_um2']-1) for q in heat['maps']),
   max_edge_probability=max(q.get('edge_probability',0) for q in heat['maps']))
text=json.dumps(report,ensure_ascii=False,indent=2);print(text)
if args.output:Path(args.output).write_text(text+'\n')
