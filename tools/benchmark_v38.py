import sys,itertools,time,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--gds',required=True);parser.add_argument('--output',type=Path,default=ROOT/'results/v38_gd55_reconstruction.json');args=parser.parse_args()
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.resources import resource_profile
from die_shift_studio.reconstruction_route import warp
import numpy as np,shapely
from collections import Counter
path=args.gds;d=read_gds(path);roles={k:'不参与' for k in d.layers};roles.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'})
c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters();e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings());r=e.reconstructor;rows=[];start=time.perf_counter()
base=r.frame(np.zeros((len(c['dies']),3)));cold=time.perf_counter()-start;intervals=[q['interval'] for q in base['pieces']]
for sx,sy,so in itertools.product((-1,1),repeat=3):
 state=np.tile([sx*35,sy*35,so*np.pi/180],(len(c['dies']),1));t=time.perf_counter();f=r.frame(state);elapsed=time.perf_counter()-t
 assert intervals==[q['interval'] for q in f['pieces']]
 assert all(q['geometry'].is_valid for rows_ in f['replacements'].values() for q in rows_)
 t=time.perf_counter();again=r.frame(state);cache_ms=1000*(time.perf_counter()-t);assert again is f
 rows.append(dict(pose=[sx*35,sy*35,so],seconds=elapsed,cached_ms=cache_ms,pieces=len(f['pieces']),valid=sum(q['valid'] for q in f['pieces']),warnings=dict(Counter(q['warning'].split('：')[0] for q in f['pieces'] if q['warning']))))
 print(rows[-1],flush=True)
# Drag work is measured independently of painting and final DRC.
q=next(q for q in base['pieces'] if q['route_plan'] and q['valid'] and q['interval'][1]-q['interval'][0]>300)
plan=q['route_plan'];timings=[]
for delta in np.linspace(1,30,30):
 t=time.perf_counter();cfg,part=r.edited(q,0,plan.route.interpolate(q['interval'][0]+delta).coords[0]);warp(part,plan,r.model.bound.centers[q['die']],np.array([-35.,-35.,-np.pi/180]));timings.append(1000*(time.perf_counter()-t))
code_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'die_shift_studio').glob('*.py')))).hexdigest()
record=dict(code_sha256=code_hash,version='V38',source_sha256=d.sha256,hardware=resource_profile(),cold_nominal_plan_seconds=cold,rows=rows,local_drag_geometry_ms=dict(median=float(np.median(timings)),maximum=max(timings)),scope='Linux CPU; local geometry time excludes Qt paint and final full DRC; no CUDA performance claim')
args.output.open('w',encoding='utf8').write(json.dumps(record,ensure_ascii=False,indent=2));print('COLD',cold,'DRAG',record['local_drag_geometry_ms'],flush=True);e.close()
