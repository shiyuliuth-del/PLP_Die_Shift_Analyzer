"""Reproducible source-read-only connectivity audit for an external saved job.

--all checks every discrete joint pose. These are electrical checks, NOT a
continuous-space proof or a DRC/production yield claim. Private geometry is
never written into the report.
"""
import argparse,json,time,sys,hashlib
from pathlib import Path
from itertools import product
import numpy as np
import shapely
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio import reconstruction_joint
from die_shift_studio.resources import parallel_map,resource_profile

def load(path):
    job=normalize_project(json.loads(path.read_text(encoding='utf8')));source=Path(job['gds_path'])
    if not source.is_absolute():source=path.parent/source
    data=read_gds(source,job['top_cell']);data,job=restore_project_geometry(data,job)
    case=build_case(data,job['roles'],layer_order=job['layer_order']);p=Parameters.from_dict(job['parameters'])
    cfg=ViewSettings.from_dict(job['settings']);cache=GeometryCache(data)
    e=PreviewEngine(cache,case,job['roles'],job.get('motions',{}),p,None,case['connections']);e.configure_reconstruction(True,cfg)
    return e,case,source

def audit(r,states,frame):
    from die_shift_studio.topology_routing import signature
    changed={(k,i):row['geometry'] for k,rows in frame['replacements'].items() for row in rows for i in row['indices']}
    missing=[];topology=[]
    for ref,ports in r.vias_by_target.items():
        source=r.model.nodes[r.model.ids[ref]]['poly'];actual=changed.get(ref,source)
        if signature(actual)!=signature(source) or not actual.is_valid:topology.append(ref)
        for v in ports:
            if r.model.moved(v['poly'],v['owner'],states).intersection(actual).area<=r.tol*r.tol:missing.append((ref,(v['key'],v['index'])))
    nets=r.rules.geometries(frame['replacements']);keys=list(nets);geos=list(nets.values());tree=shapely.STRtree(geos);shorts=[]
    for i,g in enumerate(geos):
        for j in tree.query(g,predicate='intersects'):
            if j>i and keys[i][0]==keys[int(j)][0]:shorts.append((keys[i],keys[int(j)]))
    return dict(missed_vias=missing,topology=topology,shorts=shorts,hard_failures=frame.get('hard_failures',()),repairs=frame.get('repair_refs',()))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('project',type=Path)
    parser.add_argument('--all',action='store_true');parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--threads',type=int,default=0);args=parser.parse_args()
    start=time.perf_counter();e,c,source=load(args.project);r=e.reconstructor;grid=ExtremeCatalog(c,e.p)
    report=dict(source=source.name,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),scope='all discrete joint poses' if args.all else 'zero and 8 simultaneous XYZ corners')
    try:
        r._plans_for_all_poses(lambda n,t:print(t,flush=True) if n%10==0 else None)
        report['planning_seconds']=time.perf_counter()-start
        if args.all:
            bundles=reconstruction_joint.prepare(r,grid,lambda n,t:print(t,flush=True) if n%10==0 else None)
            poses=list(product(*(range(n) for n in grid.radices)))
            def check(ds):
                t=time.perf_counter();states,frame=reconstruction_joint.scene(r,grid,bundles,ds,diagnostics=False)
                return dict(digits=ds,seconds=time.perf_counter()-t,**audit(r,states,frame))
            profile=resource_profile()
            if args.threads:profile=dict(profile,workers=min(profile['workers'],max(1,args.threads)))
            rows=parallel_map(check,poses,profile=profile,progress=lambda n,total:print(f'CONTINUITY {n}/{total}',flush=True))
            # Independently assembled full frames must match cached local-scene
            # composition. Never compare only bounding boxes or areas.
            for index in sorted(set(np.linspace(0,len(poses)-1,min(12,len(poses)),dtype=int))):
                ds=poses[index];z,scene=reconstruction_joint.scene(r,grid,bundles,ds,False);full=r.frame(z,diagnostics=False)
                a=r.rules.geometries(scene['replacements']);b=r.rules.geometries(full['replacements'])
                assert all(a[k].symmetric_difference(b[k]).area<=r.tol*r.tol for k in a),(ds,'local/full copper mismatch')
            report['full_frame_comparisons']=min(12,len(poses))
        else:
            from die_shift_studio.statistics import mean_and_bounds
            rows=[]
            for ds in [(0,0,0),*product((-1,1),repeat=3)]:
                states=[]
                for owner in range(grid.count):
                    mu,b=mean_and_bounds(e.p.for_die(owner),grid.count)
                    states.append(np.where(np.array(ds)==0,0,mu+np.array(ds)*b))
                states=np.asarray(states);t=time.perf_counter();frame=r.frame(states,diagnostics=False)
                row=dict(digits=ds,seconds=time.perf_counter()-t,**audit(r,states,frame));rows.append(row);print(json.dumps(row),flush=True)
        report.update(states=len(rows),failed_states=sum(bool(q['missed_vias'] or q['topology'] or q['shorts'] or q['hard_failures']) for q in rows),rows=rows,seconds=time.perf_counter()-start)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print('CONTINUITY_DONE',report['states'],report['failed_states'],report['seconds'],flush=True)
        return int(bool(report['failed_states']))
    finally:e.close()

if __name__=='__main__':raise SystemExit(main())
