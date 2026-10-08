"""Same-input reference/cached preview benchmark; no claim of full DRC timing."""
import argparse, json, platform, sys, time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,evaluate,Parameters
from die_shift_studio.fast_preview import GeometryCache,check_frame
from die_shift_studio.preview import default_motion,layer_owners,transformed_layers
from die_shift_studio.state_check import check_state
from die_shift_studio.resources import resource_profile

def measure(fn,repeats):
    values=[];out=None
    for _ in range(repeats):
        start=time.perf_counter();out=fn();values.append((time.perf_counter()-start)*1000)
    return out,dict(samples_ms=values,median_ms=float(np.median(values)))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repeat',type=int,default=7);parser.add_argument('--copies',type=int,default=48)
    args=parser.parse_args()
    if args.repeat<1 or args.copies<1:parser.error('repeat and copies must be positive')
    f=ROOT/'examples/示例C_多芯片晶圆接口.json';project=json.loads(f.read_text());data=read_gds(f.parent/project['gds_path']);roles=project['roles']
    p=Parameters.from_dict(project['parameters']);result=evaluate(data,roles,p)
    # Evaluation of nominal contacts is done on the base example only. Repeated,
    # overlapping synthetic copies stress transforms, not connection extraction.
    for key in ('10/0','20/0','30/0','31/0','40/0','41/0'):
        data.layers[key]=[a+np.array([j*.002,0]) for j in range(args.copies) for a in data.layers[key]]
    case=build_case(data,roles);modes={k:default_motion(r) for k,r in roles.items()};modes['40/0']='auto'
    z=np.array([[35,-20,.01],[-12,28,-.01]]);timings={}
    owners,timings['reference_ownership']=measure(lambda:layer_owners(data,case,modes),1)
    cache,timings['cache_build']=measure(lambda:GeometryCache(data),1)
    bound,timings['cache_bind']=measure(lambda:cache.bind(case,modes,list(data.layers)),1)
    legacy,timings['reference_transform']=measure(lambda:transformed_layers(data,case,modes,z,owners=owners),args.repeat)
    fast,timings['cached_transform_serial']=measure(lambda:bound.transform(z,workers=1),args.repeat)
    _,timings['cached_transform_threads']=measure(lambda:bound.transform(z,workers=cache.profile['workers']),args.repeat)
    _,timings['calibration']=measure(lambda:bound.calibrate(z),1)
    _,timings['cached_transform_auto']=measure(lambda:bound.transform(z),args.repeat)
    for key in legacy:
        for a,b in zip(legacy[key],fast[key]['polygons']):np.testing.assert_allclose(a,b,atol=1e-9)
    old,timings['reference_state_check']=measure(lambda:check_state(data,case,roles,p,result,z,legacy,1),args.repeat)
    new,timings['cached_state_check']=measure(lambda:check_frame(bound,roles,p,result,z,fast,1),args.repeat)
    normalize=lambda r:sorted((x['kind'],tuple(np.round(x['point'],7))) for x in r['failures'])
    assert old['problem_percent']==new['problem_percent'] and normalize(old)==normalize(new)
    record=dict(version='0.4.0',python=platform.python_version(),platform=platform.platform(),resources=resource_profile(),
                source_sha256=data.sha256,copies=args.copies,polygons=data.count,vertices=sum(len(a) for v in data.layers.values() for a in v),
                description='Same-input reference vs cache; scheme 1 state check; all interfaces eligible in both kernels; no nominal contact extraction, GUI rasterization or routing in timing; sequential groups, shared-host variability.',
                equivalent=True,calibration=bound.calibration,timings=timings)
    (ROOT/'results').mkdir(exist_ok=True);(ROOT/'results/performance_v040.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
