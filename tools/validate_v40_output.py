"""Independent written-contour check and real-layout angle-mode smoke checks.

--local-plans accepts only the developer's own checkpoint from benchmark_v40;
never load a pickle supplied by another person. This is not an application cache.
"""
from pathlib import Path
import argparse,hashlib,json,pickle,sys,time
from dataclasses import replace
import numpy as np,shapely,ezdxf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.reconstruction_events import zone_key,scene_failure
pa=argparse.ArgumentParser();pa.add_argument('--gds',required=True);pa.add_argument('--dxf',required=True);pa.add_argument('--index',type=int,default=9228);pa.add_argument('--local-plans',required=True);pa.add_argument('--out',default=str(ROOT/'results/v40_layout_checks.json'));args=pa.parse_args()
d=read_gds(args.gds);saved=pickle.loads(Path(args.local_plans).read_bytes());assert saved['source_sha256']==d.sha256
roles={k:'不参与' for k in d.layers};roles.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'})
c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters();cfg=ViewSettings();z=ExtremeCatalog(c,p)[args.index]['states']
e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,cfg);e.reconstructor.plan_pool.update(saved['plans'])
try:
 f=e.reconstructor.frame(z);full=dict(states=z,layers=e.bound.transform(z),reconstruction=f);snap=snapshot(d,e.bound.cache,full,reconstruction=True)
 doc=ezdxf.readfile(args.dxf);assert not doc.audit().errors
 entities=list(doc.modelspace());metrics=[]
 for key,blobs in snap.layers.items():
  expected=shapely.union_all([shapely.from_wkb(v) for v in blobs]).boundary;base='L'+key.replace('/','_D');names={base+k for k in 'ABC'} if roles[key]=='固定RDL' else {base}
  lines=[shapely.LineString(np.array(list(ent.get_points('xy')))) for ent in entities if ent.dxf.layer in names]
  actual=shapely.union_all(lines);distance=float(actual.hausdorff_distance(expected));delta=abs(float(actual.length-expected.length))
  assert distance<1e-8 and delta<max(1e-8,expected.length*1e-10),(key,distance,delta)
  metrics.append(dict(layer=key,hausdorff_um=distance,length_error_um=delta))
 positions={zone_key(q):q['interval'] for q in f['pieces']};modes=[]
 for mode in ('wide','balanced'):
  e.configure_reconstruction(True,replace(cfg,reconstruction_boundary_mode=mode));t=time.perf_counter();g=e.reconstructor.frame(z);elapsed=time.perf_counter()-t
  assert {zone_key(q):q['interval'] for q in g['pieces']}==positions
  assert all(q['geometry'].is_valid for rows in g['replacements'].values() for q in rows)
  assert all(abs(v)<=np.pi/3+1e-12 for q in g['pieces'] for v in q['boundary_angles'])
  modes.append(dict(mode=mode,seconds=elapsed,valid_output=True,shared_positions=True,tilted_records=sum(any(abs(v)>1e-6 for v in q['boundary_angles']) for q in g['pieces']),violations=len(g['violations']),scene_failure=scene_failure(g)))
 result=dict(source_sha256=d.sha256,code_sha256=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'die_shift_studio').glob('*.py')))).hexdigest(),index=args.index,contours=metrics,angle_modes=modes,scope='One complete GD55 pose; valid output and rule violations are different quantities.')
 Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('V40_OUTPUT_PASS',flush=True)
finally:e.close()
