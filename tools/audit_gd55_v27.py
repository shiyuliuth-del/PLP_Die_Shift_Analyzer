"""Compare both checkouts on GD55. Publish only aggregate findings, never the layout."""
import argparse,itertools,json,math,os,subprocess,sys,tempfile
from pathlib import Path
import numpy as np


def snapshot(checkout,gds,path):
    sys.path.insert(0,str(checkout))
    from die_shift_studio.core import Parameters,read_gds,build_case,evaluate
    from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
    from die_shift_studio.settings import ViewSettings
    data=read_gds(gds)
    mapping={'3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔','127/0':'封装边框','128/0':'芯片边框'}
    roles={k:mapping.get(k,'不参与') for k in data.layers}
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
    parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--snapshot',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
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
