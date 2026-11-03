"""Paired CPU measurements and all per-via grids. Run in separate processes per checkout."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
from dataclasses import replace
p=argparse.ArgumentParser();p.add_argument('--checkout',required=True,type=Path);p.add_argument('--gds',required=True,type=Path)
p.add_argument('--out',required=True,type=Path);p.add_argument('--grids',required=True,type=Path)
args=p.parse_args();sys.path.insert(0,str(args.checkout.resolve()))
import numpy as np
from die_shift_studio import __version__
from die_shift_studio.acceleration import rigid_points
from die_shift_studio.core import read_gds,Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.stack import Connections
from die_shift_studio.settings import ViewSettings
from die_shift_studio.probability_overlay import probability_maps

def main():
    data=read_gds(args.gds);known={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    roles={k:known.get(k,'不参与') for k in data.layers};order=['6/0','5/0','4/0','3/0'];cache=GeometryCache(data)
    con=Connections(data,roles,order,cache.geometries);case=build_case(data,roles,connections=con);params=Parameters()
    eng=PreviewEngine(cache,case,roles,{},params,None,con);values={};times={}
    try:
        for method in ('sobol','quadrature'):
            samples=[];cfg=replace(ViewSettings(),acceleration='cpu',probability_method=method)
            for _ in range(2):
                t=time.perf_counter();maps=probability_maps(cache,eng.bound,roles,params,masks=eng.contact.interface_masks,settings=cfg)
                samples.append(time.perf_counter()-t)
            times[method]=samples
            for j,row in enumerate(maps['maps']):
                prefix=f'{method}_{j}';values[prefix]=row['probability'];values[prefix+'_extent']=row['extent']
        # GD55 layer vertices replicated to measure a large interactive transform.
        points=np.asarray(cache.layers['3/0']['flat']);points=np.tile(points,(max(1,200000//len(points)),1))
        owners=np.zeros(len(points),int);centers=np.array([[125.,-350.]])
        for key,state in [('translation',[35.,-35.,0.]),('rotation',[35.,-35.,np.deg2rad(-1.)])]:
            samples=[]
            for _ in range(13):
                t=time.perf_counter();out=rigid_points(points,owners,centers,np.asarray([state]),'cpu');samples.append(time.perf_counter()-t)
            times[key]=samples;values[key]=out
        np.savez(args.grids,**values)
        result=dict(version=__version__,gds_sha=data.sha256,shapes=sum(map(len,data.layers.values())),dies=len(case['dies']),logical_cpu=os.cpu_count(),
                    single_layer_vertices=len(points),region_count=len(maps['maps']),timings_seconds=times,
                    rules_um=[15,15,15],bounds=[35,35,1],yield_level=params.target_nsigma)
        args.out.write_text(json.dumps(result,indent=2)+'\n')
    finally:eng.close()

if __name__=='__main__':main()
