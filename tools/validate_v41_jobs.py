"""Read-only supplied-project audit: full discrete factors and written ABC contours.

Private GDS and developer checkpoints stay outside the repository. Pickle
checkpoints are written for follow-up GUI checks, never accepted as inputs.
"""
import argparse,hashlib,json,os,pickle,sys,time
from pathlib import Path
from dataclasses import asdict,replace
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import numpy as np,shapely,ezdxf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.recognition import automatic_parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.reconstruction_events import scan,current_failures,scene_failure,zone_key,color_for
from die_shift_studio.reconstruction_probability import attach
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_contract import EXPORT_ROLES
from die_shift_studio.resources import resource_profile

def code_hash():return hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'die_shift_studio').glob('*.py')))).hexdigest()

def main():
 pa=argparse.ArgumentParser();pa.add_argument('--project',action='append',required=True);pa.add_argument('--output',required=True);pa.add_argument('--scratch',required=True);args=pa.parse_args()
 scratch=Path(args.scratch);scratch.mkdir(parents=True,exist_ok=True);sha=code_hash();results=[]
 for path in map(Path,args.project):
  name=path.stem;job=normalize_project(json.loads(path.read_text()));d=read_gds(path.parent/job['gds_path'],job['top_cell']);assert d.sha256==job['sha256'];d,job=restore_project_geometry(d,job)
  c=build_case(d,job['roles'],layer_order=job['layer_order']);p=Parameters.from_dict({**asdict(Parameters.from_dict(job['parameters'])),**automatic_parameters(d,job['roles'])[0]});cfg=ViewSettings.from_dict(job['settings'])
  e=PreviewEngine(GeometryCache(d),c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,cfg);r=e.reconstructor;grid=ExtremeCatalog(c,p);last=[0.]
  def progress(n,text):
   if time.monotonic()-last[0]>4:print(name,n,text,flush=True);last[0]=time.monotonic()
  try:
   start=time.perf_counter();zero=r.frame(np.zeros((grid.count,3)),progress=progress);cold=time.perf_counter()-start
   required={(l,i,v['key'],v['index']) for (l,i),values in r.vias_by_target.items() for v in values}
   covered={(q['layer'],q['index'],k,i) for q in zero['pieces'] for k,i in q['via_refs']}
   covered|={(l,i,v['key'],v['index']) for (l,i),values in r.safe_vias.items() for v in values}
   assert required<=covered,('untracked interfaces',required-covered)
   assert all(q['valid'] and not q['regions']['A'].is_empty for q in zero['pieces']),[(q['layer'],q['index'],q['warning']) for q in zero['pieces'] if not q['valid'] or q['regions']['A'].is_empty]
   print(name,'COLD',cold,'ZONES',len(zero['pieces']),'AUTO_IGNORED',len(r.auto_ignored),flush=True)
   checkpoint=scratch/(name+'.pkl');checkpoint.write_bytes(pickle.dumps(dict(source_sha256=d.sha256,code_sha256=sha,plans=r.plan_pool,job=job),protocol=5))
   start=time.perf_counter();bank=attach(scan(r,grid,progress),r);scan_seconds=time.perf_counter()-start
   checkpoint.write_bytes(pickle.dumps(dict(source_sha256=d.sha256,code_sha256=sha,plans=r.plan_pool,job=job,evidence=bank),protocol=5))
   index=ProblemIndex(grid,bank['red_factors']);zone_indices={key:ProblemIndex(grid,row['clauses']) for key,row in bank['zones'].items()};parity=[]
   samples=list(dict.fromkeys([-1,0,grid.total-1,*np.random.default_rng(41).integers(0,grid.total,9).tolist()]))
   for i in samples:
    z=np.zeros((grid.count,3)) if i<0 else grid[i]['states'];digits=[0]*grid.count if i<0 else grid.choices(i)
    start=time.perf_counter();f=r.frame(z);seconds=time.perf_counter()-start;bad=current_failures(f)
    assert index.matches(digits)==scene_failure(f),(name,i,'scene factor mismatch')
    for q in f['pieces']:assert zone_indices[zone_key(q)].matches(digits)==(zone_key(q) in bad),(name,i,zone_key(q))
    assert all(v['geometry'].is_valid for values in f['replacements'].values() for v in values)
    parity.append(dict(index=int(i),seconds=seconds,rule_groups=len(f['violations']),problem_zones=len(bad),disconnected=sum(q['connection_failed'] for q in f['pieces'])))
    print(name,'PARITY',parity[-1],flush=True)
   poses=[('zero',np.zeros((grid.count,3))),('mixed',z)];exports=[]
   for label,z in poses:
    f=r.frame(z);full=dict(states=z,layers=e.bound.transform(z),reconstruction=f);frozen=snapshot(d,e.bound.cache,full,reconstruction=True)
    start=time.perf_counter();out=write_regions(frozen,f,job['roles'],scratch/(name+'_'+label+'.dxf'));out['seconds']=time.perf_counter()-start
    doc=ezdxf.readfile(out['path']);assert not doc.audit().errors;entities=list(doc.modelspace());metrics=[]
    allowed=set()
    for layer,blobs in frozen.layers.items():
     role=job['roles'][layer]
     if role not in EXPORT_ROLES:continue
     base='L'+layer.replace('/','_D');names={base+k for k in 'ABC'} if role=='固定RDL' else {base};allowed|=names
     expected=shapely.union_all([shapely.from_wkb(b) for b in blobs]).boundary
     lines=[shapely.LineString(np.array(list(ent.get_points('xy')))) for ent in entities if ent.dxf.layer in names]
     actual=shapely.union_all(lines);distance=float(actual.hausdorff_distance(expected));difference=abs(float(actual.length-expected.length))
     assert distance<1e-8 and difference<max(1e-8,expected.length*1e-10),(layer,distance,difference)
     metrics.append(dict(layer=layer,hausdorff_um=distance,length_error_um=difference))
    assert all(ent.dxftype()=='LWPOLYLINE' and not ent.closed and ent.dxf.layer in allowed for ent in entities)
    out.pop('path');exports.append(dict(pose=label,contours=metrics,**out));print(name,'EXPORT',label,'PASS',flush=True)
   styles=[];fixed_positions={zone_key(q):q['interval'] for q in zero['pieces']}
   for style in ('linear','c1','c2','arc'):
    e.configure_reconstruction(True,replace(cfg,reconstruction_style=style));start=time.perf_counter();f=e.reconstructor.frame(z)
    assert f['parameters']['connection_style']==style
    assert {zone_key(q):q['interval'] for q in f['pieces']}==fixed_positions
    assert all(v['geometry'].is_valid for values in f['replacements'].values() for v in values)
    styles.append(dict(style=style,seconds=time.perf_counter()-start,pieces=len(f['pieces']),disconnected=sum(q['connection_failed'] for q in f['pieces']),rule_groups=len(f['violations'])))
    print(name,'STYLE',styles[-1],flush=True)
   results.append(dict(project=path.stem,source_sha256=d.sha256,parameters=asdict(p),dies=grid.count,interfaces=len(required),covered_interfaces=len(required&covered),inferred_fixed_interfaces=len(c['connections'].inferred_interfaces),auto_ignored=len(r.auto_ignored),nominal_zones=len(zero['pieces']),nominal_valid_zones=sum(q['valid'] for q in zero['pieces']),cold_seconds=cold,scan_seconds=scan_seconds,states=grid.total+1,geometry_checks=bank['geometry_checks'],local_checks=bank['local_checks'],pair_checks=bank['pair_checks'],colours={k:sum(color_for(q,bank,True,p.target_nsigma)==k for q in zero['pieces']) for k in ('red','yellow','green','gray')},parity=parity,exports=exports,styles=styles))
   assert code_hash()==sha,'Runtime changed during audit; results must be rerun'
   Path(args.output).write_text(json.dumps(dict(status='PARTIAL',code_sha256=sha,hardware=resource_profile(),cases=results),ensure_ascii=False,indent=2)+'\n')
  finally:e.close()
 assert code_hash()==sha
 Path(args.output).write_text(json.dumps(dict(status='PASS',code_sha256=sha,hardware=resource_profile(),cases=results,scope='Linux CPU; complete discrete factors, selected full-frame native parity and actual DXF readback. Geometric validity is distinct from DRC, continuous probability or production acceptance.'),ensure_ascii=False,indent=2)+'\n')
 print('V41_JOBS_PASS',flush=True)

if __name__=='__main__':main()
