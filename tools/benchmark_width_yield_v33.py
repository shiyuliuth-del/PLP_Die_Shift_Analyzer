"""Measure first result, full joint calculation and ignored-line cache reclassification."""
import argparse,json,os,sys,time
from dataclasses import replace
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio import __version__
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.stack import Connections
from die_shift_studio.contact import ContactModel
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_yield import measure_width_yield,policy_yields

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gds',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    data=read_gds(args.gds)
    known=dict(zip(('127/0','128/0','3/0','4/0','5/0','6/0'),
                   ('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
    roles={k:known.get(k,'不参与') for k in data.layers};order=['6/0','5/0','4/0','3/0']
    cache=GeometryCache(data);con=Connections(data,roles,order,cache.geometries)
    case=build_case(data,roles,connections=con);p=Parameters();model=ContactModel(data,case,roles,con,cache,parameters=p)
    cfg=replace(ViewSettings(),acceleration='cpu')
    first=[];start=time.perf_counter()
    def partial(bank):
        if not first:first.append(time.perf_counter()-start)
    bank=measure_width_yield(model,p,cfg,None,lambda *_:None,partial)
    full=time.perf_counter()-start
    refs=sorted({r for row in bank['chunks'] for r in row[3]})
    timings=[]
    for _ in range(100):
        stamp=time.perf_counter();policy_yields(bank,refs);timings.append(time.perf_counter()-stamp)
    report=dict(version=__version__,gds_sha256=data.sha256,shape_count=sum(map(len,data.layers.values())),
                die_count=len(case['dies']),logical_cpu=os.cpu_count(),python=sys.version.split()[0],backend='cpu',
                single_side_um=[35,35],angle_deg=1,linewidth_um=15,spacing_um=15,enclosure_um=15,
                sigma=6,samples=4096,seed=cfg.random_seed,first_result_seconds=first[0] if first else full,
                complete_seconds=full,ignored_reclassification_median_seconds=float(np.median(timings)),
                original_values=policy_yields(bank,[])['values'].tolist(),
                ignore_all_sampled_problem_lines_values=policy_yields(bank,refs)['values'].tolist(),
                risky_source_count=len(refs),sampling='joint independent Monte Carlo on the complete bounded support')
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
