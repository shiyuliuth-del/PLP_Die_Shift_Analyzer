"""Read-only real-layout colour/filter, cached export and reconstruction timings."""
from pathlib import Path
import argparse,json,sys,time,hashlib,math
import numpy as np,shapely
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.reconstruction_events import scan,current_failures,color_for,zone_key
from die_shift_studio.reconstruction_probability import attach
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.resources import resource_profile
parser=argparse.ArgumentParser();parser.add_argument('--gds',required=True);parser.add_argument('--output',default=str(ROOT/'results/v40_gd55.json'));parser.add_argument('--dxf');parser.add_argument('--checkpoint',help='Optional local developer snapshot; never load untrusted pickle files');args=parser.parse_args()
code_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'die_shift_studio').glob('*.py')))).hexdigest()
d=read_gds(args.gds);roles={k:'不参与' for k in d.layers};roles.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'})
c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters();cfg=ViewSettings();grid=ExtremeCatalog(c,p)
e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,cfg);r=e.reconstructor
last=[0.]
def progress(n,t):
 if time.monotonic()-last[0]>3 or n==100:print(n,t,flush=True);last[0]=time.monotonic()
try:
 t=time.perf_counter();nominal=r.frame(np.zeros((grid.count,3)),progress=progress);cold=time.perf_counter()-t
 print('COLD',cold,flush=True);t=time.perf_counter();evidence=attach(scan(r,grid,progress),r);scan_seconds=time.perf_counter()-t

 if args.checkpoint:
  import pickle
  Path(args.checkpoint).write_bytes(pickle.dumps(dict(source_sha256=d.sha256,code_sha256=code_hash,plans=r.plan_pool,evidence=evidence),protocol=5))
 idx=ProblemIndex(grid,evidence['red_factors']);print('SCAN',scan_seconds,'FOUND',idx.total,'OF',grid.total,flush=True)
 actual=[];samples=[0,1,grid.total-1,*np.random.default_rng(40).integers(0,grid.total,9).tolist()]
 for i in samples:
  pose=grid[i];t=time.perf_counter();frame=r.frame(pose['states']);elapsed=time.perf_counter()-t
  bad=current_failures(frame);assert idx.matches(pose['choices'])==bool(bad)
  for q in frame['pieces']:
   found=ProblemIndex(grid,evidence['zones'][zone_key(q)]['clauses']).matches(pose['choices'])
   assert found==(zone_key(q) in bad),(i,zone_key(q),found,bad)
  actual.append(dict(index=int(i),seconds=elapsed,problem_zones=len(bad)))
  print('PARITY',actual[-1],flush=True)
 export=None
 if args.dxf:
  from die_shift_studio.dxf_export import snapshot
  from die_shift_studio.region_export import write_regions
  state=pose['states'];f=dict(states=state,layers=e.bound.transform(state),reconstruction=frame)
  frozen=snapshot(d,e.bound.cache,f,reconstruction=True);t=time.perf_counter()
  export=write_regions(frozen,frame,roles,args.dxf,progress);export['seconds']=time.perf_counter()-t
  import ezdxf
  from collections import Counter
  doc=ezdxf.readfile(args.dxf);assert not doc.audit().errors;ends=Counter()
  for entity in doc.modelspace():
   assert entity.dxftype()=='LWPOLYLINE' and not entity.closed
   xy=np.array(list(entity.get_points('xy')));assert not np.array_equal(xy[0],xy[-1])
   ends[tuple(xy[0])]+=1;ends[tuple(xy[-1])]+=1
  assert all(n%2==0 for n in ends.values());export['all_open']=True;export['unmatched_endpoints']=0
 result=dict(version='V40',source_sha256=d.sha256,code_sha256=code_hash,hardware=resource_profile(),cold_seconds=cold,
             scan_seconds=scan_seconds,states=grid.total+1,filtered_states=idx.total+1,geometry_checks=evidence['geometry_checks'],local_checks=evidence['local_checks'],pair_checks=evidence['pair_checks'],
             zones=len(evidence['zones']),nominal_colors={k:sum(color_for(q,evidence,True,6)==k for q in nominal['pieces']) for k in ('red','yellow','green','gray')},
             frame_parity=actual,export=export,scope='Linux CPU; complete discrete factor analysis, selected full-frame parity. No physical GPU or production-yield acceptance.')
 Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('V40_GD55_PASS',flush=True)
finally:e.close()
