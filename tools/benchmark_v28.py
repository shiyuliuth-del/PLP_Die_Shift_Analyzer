"""Time an uncached full-grid GD55 width summary; verify sampled rows against full geometry."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from die_shift_studio.core import read_gds,Parameters,build_case
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.stack import Connections
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.settings import ViewSettings
from die_shift_studio.session_cache import ProjectCacheRegistry
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.resources import resource_profile

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gds',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    d=read_gds(args.gds);known={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    roles={k:known.get(k,'不参与') for k in d.layers};order=['6/0','5/0','4/0','3/0']
    p=Parameters();cfg=ViewSettings();cache=GeometryCache(d);con=Connections(d,roles,order,cache.geometries)
    case=build_case(d,roles,connections=con);grid=ExtremeCatalog(case,p)
    registry=ProjectCacheRegistry();group=registry.get('benchmark',2000)
    e=PreviewEngine(cache,case,roles,{},p,None,con,group,'benchmark');e.signature='benchmark'
    try:
        e.configure_width(True,cfg)
        start=time.perf_counter();q=e.width_summary(lambda *_:None);cold=time.perf_counter()-start
        start=time.perf_counter();again=e.width_summary(lambda *_:None);warm=time.perf_counter()-start
        assert again['lines']==q['lines']
        start=time.perf_counter();idx=ProblemIndex(grid,q['red_factors']);page=[idx.at(j) for j in range(min(256,idx.total))]
        page_s=time.perf_counter()-start
        rng=np.random.default_rng(2801)
        samples=[[0]*grid.count,*[grid.choices(idx.at(i)) for i in (0,11,idx.total-1) if 0<=i<idx.total],
                 *[rng.integers(0,grid.radices).tolist() for _ in range(20)]]
        positive=0
        for digits in samples:
            states=np.array([v[j] for v,j in zip(grid.locals,digits)]);w=e.frame(states)['width_analysis']
            hit=bool(w['counts']['宽度不足'] or w['edge_violations'])
            assert idx.matches(digits)==hit,(digits,hit)
            positive+=hit
        result=dict(version='V28',source_sha256=d.sha256,shapes=sum(map(len,d.layers.values())),platform=sys.platform,
                    resource_profile=resource_profile(),dies=grid.count,rules_um=[15,15,15],single_sided=[35,35,1],sigma=6,
                    package_states=q['package_states'],red_shifted_states=idx.total,risk_lines=len(q['lines']),eligible_endpoint_poses=q['local_checks'],
                    sampled_rows=len(samples),red_samples=positive,other_samples=len(samples)-positive,first_page_nonzero=len(page),
                    times_s=dict(cold_full_summary=cold,cache_lookup=warm,build_index_and_first_page=page_s))
        Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(result,flush=True)
    finally:e.close();registry.close()

if __name__=='__main__':main()
