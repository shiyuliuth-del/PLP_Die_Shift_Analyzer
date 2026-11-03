"""Compare both checkouts on GD55. Publish only aggregate findings, never the layout."""
import argparse,itertools,json,math,os,subprocess,sys,tempfile
from pathlib import Path
import numpy as np


def sample_proof(gds,path):
    import time
    from unittest.mock import patch
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from die_shift_studio import acceleration
    from die_shift_studio.core import Parameters,read_gds,build_case
    from die_shift_studio.contact import ContactModel
    from die_shift_studio.distribution import bounded_poses
    from die_shift_studio.model_yield import DieEvent
    from die_shift_studio.settings import ViewSettings
    data=read_gds(gds);roles=mapping_for(data)
    case=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0'])
    model=ContactModel(data,case,roles,case['connections']);p=Parameters()
    runs=[];exact_fn=acceleration.rigid_pose_batch
    for i in range(len(case['dies'])):
        event=DieEvent(model,p.for_die(i),i,'cpu')
        z=bounded_poses(p.for_die(i),len(case['dies']),ViewSettings(sample_exponent=10,random_seed=27+i))
        enabled=event.projections
        results=[];oracle=None
        for label,projections in (('full',[None]*len(enabled)),('projected',enabled)):
            event.projections=projections;count=0
            def count_batch(xy,center,states,backend):
                nonlocal count
                count+=len(states)
                return exact_fn(xy,center,states,backend)
            started=time.perf_counter()
            with patch.object(acceleration,'rigid_pose_batch',count_batch):safe=event.no_contact(z)
            elapsed=time.perf_counter()-started
            if oracle is None:oracle=safe
            else:np.testing.assert_array_equal(safe,oracle)
            results.append(dict(mode=label,polygons=count,seconds=elapsed,safe_samples=int(safe.sum())))
        runs.append(dict(die=i+1,samples=len(z),runs=results))
    out=dict(dies=runs,full=sum(v['runs'][0]['polygons'] for v in runs),
             projected=sum(v['runs'][1]['polygons'] for v in runs),all_samples_identical=True)
    path.write_text(json.dumps(out,indent=2)+'\n');print(out)


def catalogue_proof(gds,path):
    from dataclasses import replace
    from time import perf_counter
    from unittest.mock import patch
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from die_shift_studio.core import Parameters,read_gds,build_case
    from die_shift_studio.contact import ContactModel
    from die_shift_studio.settings import ViewSettings
    from die_shift_studio.width_analysis import WidthAnalyzer
    from die_shift_studio.session_cache import ProjectCacheRegistry
    data=read_gds(gds);roles=mapping_for(data);case=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0'])
    model=ContactModel(data,case,roles,case['connections']);z=np.tile([-35.,-35.,-math.pi/180],(len(case['dies']),1))
    group=ProjectCacheRegistry().get('proof');rows={};times={}
    try:
        for name,k,cache in (('initial_6sigma',6,group),('new_2sigma_reuse',2,group),('new_2sigma_cold',2,None)):
            started=perf_counter();a=WidthAnalyzer(model,replace(Parameters(),target_nsigma=k),ViewSettings(),cache)
            try:
                with patch.object(a,'_catalogue',wraps=a._catalogue) as calls:
                    result=a.frame({'states':z});times[name]=dict(seconds=perf_counter()-started,catalogue_builds=calls.call_count,
                        confirmed_low=sum(bool((r.get('event_probability') or {}).get('low')) for r in result['rows']))
                    rows[name]=result['rows']
            finally:a.close()
        assert rows['new_2sigma_reuse']==rows['new_2sigma_cold']
        assert times['new_2sigma_reuse']['catalogue_builds']==0 and times['new_2sigma_cold']['catalogue_builds']>0
    finally:group.close()
    proof=dict(one_run=times,all_2sigma_rows_identical=True)
    path.write_text(json.dumps(proof,indent=2)+'\n');print(proof)


def mapping_for(data):
    mapping={'3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔','127/0':'封装边框','128/0':'芯片边框'}
    return {k:mapping.get(k,'不参与') for k in data.layers}


def snapshot(checkout,gds,path):
    sys.path.insert(0,str(checkout))
    from die_shift_studio.core import Parameters,read_gds,build_case,evaluate
    from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
    from die_shift_studio.settings import ViewSettings
    data=read_gds(gds)
    roles=mapping_for(data)
    order=['6/0','5/0','4/0','3/0'];case=build_case(data,roles,layer_order=order)
    p=Parameters();cfg=ViewSettings();engine=PreviewEngine(GeometryCache(data),case,roles,{},p,None,case['connections'])
    engine.configure_width(True,cfg)
    poses=[];events=[]
    keys=('plane','pair','refs','nominal_witness_um','status','required','original','current','split','locked')
    try:
        for x,y,o in [(0,0,0),*itertools.product((-35,35),(-35,35),(-1,1))]:
            state=np.tile([x,y,math.radians(o)],(len(case['dies']),1))
            rows=engine.frame(state)['width_analysis']['rows']
            events.append(sum(bool((r.get('event_probability') or {}).get('low')) for r in rows))
            values=[[[r[k] for k in keys],[*r['point'],*r['direction'],*np.ravel(r['witness_um']),r['gap_um'],r['narrowing_um']]] for r in rows]
            poses.append(sorted(values,key=lambda item:json.dumps(item[0],ensure_ascii=False)))
    finally:engine.close()
    result=evaluate(data,roles,p,layer_order=order,connections=case['connections'],settings=cfg)
    ranges=[(r['minimum'],r['maximum'],r['certified']) for r in result['safe_ranges']['rows']]
    path.write_text(json.dumps(dict(rows=poses,low=events,safe_ranges=ranges,scheme=[r['gamma'] for r in result['schemes']],
                                   yield_estimate=result['model_yield']['estimate'])))


def compare(base,new):
    worst=0.;rows=positive=0
    assert len(base['rows'])==len(new['rows'])==9
    for i,(old,current) in enumerate(zip(base['rows'],new['rows'])):
        assert len(old)==len(current),(i,len(old),len(current))
        for a,b in zip(old,current):
            assert a[0]==b[0],(i,a[0],b[0])
            error=float(np.max(np.abs(np.asarray(a[1])-b[1])));assert error<1e-6,(i,error,a,b)
            worst=max(worst,error);rows+=1;positive+=b[1][-2]>0
    for key in ('scheme','yield_estimate','safe_ranges'):
        assert base[key]==new[key],(key,base[key],new[key])
    return dict(rows=rows,positive=int(positive),max_absolute_numeric_difference=worst,
                per_pose_low=new['low'],same_safe_ranges_and_scheme_yield=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--base',type=Path)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--snapshot',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--sample-proof',action='store_true',help='Compare projected and full sampled contact predicates')
    parser.add_argument('--catalogue-proof',action='store_true',help='Verify width catalogue reuse at a new yield level')
    args=parser.parse_args()
    if args.catalogue_proof:return catalogue_proof(args.gds,args.out)
    if args.sample_proof:return sample_proof(args.gds,args.out)
    if args.base is None:parser.error('--base is required to compare checkouts')
    if args.snapshot:return snapshot(args.base.resolve(),args.gds,args.out)
    with tempfile.TemporaryDirectory() as directory:
        samples=[]
        for n,checkout in enumerate((args.base.resolve(),Path(__file__).resolve().parents[1])):
            path=Path(directory)/f'{n}.json'
            subprocess.run([sys.executable,__file__,'--snapshot','--gds',str(args.gds),'--base',str(checkout),'--out',str(path)],
                           env=dict(os.environ,OPENBLAS_NUM_THREADS='1'),check=True)
            samples.append(json.loads(path.read_text()))
        info=compare(*samples)
    args.out.write_text(json.dumps(info,indent=2)+'\n')
    print(info)


if __name__=='__main__':main()
