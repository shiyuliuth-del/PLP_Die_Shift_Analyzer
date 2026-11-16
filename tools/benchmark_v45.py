"""Read-only GD55/project timing, complete current-copper DRC and cache reuse."""
import argparse,hashlib,json,sys,time
from dataclasses import asdict,replace
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from verify_reconstruction_continuity import load,audit
from die_shift_studio.core import evaluate,clean_json
from die_shift_studio.current_drc import locations
from die_shift_studio.statistics import mean_and_bounds
from die_shift_studio.resources import resource_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('project',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    e,case,source=load(args.project);cfg=replace(e.reconstructor.settings,reconstruction_style='linear',drc_tolerance_um=.1)
    e.configure_reconstruction(True,cfg);e.configure_drc(True,cfg);count=len(case['dies'])
    report=dict(source_sha256=e.bound.cache.data.sha256,parameters=asdict(e.p),connection_style=cfg.reconstruction_style,tolerance_um=cfg.drc_tolerance_um,
                hardware=resource_profile(),runtime_sha256=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'die_shift_studio').glob('*.py')))).hexdigest(),
                scope='Local CPU, saved project, zero and two joint XYZ corners. Geometry timings are not GUI FPS or continuous-space proof.')
    try:
        t=time.perf_counter();r=evaluate(e.bound.cache.data,e.roles,e.p,connections=case['connections'],contact_model=e.contact,settings=cfg,validate_extremes=True,include_analysis=False)
        report['assessment_seconds']=time.perf_counter()-t
        assert not r['analysis_complete'] and not r['safe_ranges'] and r['extreme_factors']['checked']
        rows=[]
        for sign in (0,-1,1,0):
            states=[]
            for owner in range(count):
                mean,bound=mean_and_bounds(e.p.for_die(owner),count)
                states.append(np.zeros(3) if sign==0 else mean+sign*bound)
            z=np.array(states);t=time.perf_counter();frame=e.frame(z);elapsed=time.perf_counter()-t
            row=dict(state=z.tolist(),seconds=elapsed,drc_groups=len(frame['drc']),locations=len(locations(frame['drc'])),
                     pieces=len(frame['reconstruction']['pieces']),native_cache_hits=e.global_drc.hits,native_cache_misses=e.global_drc.misses,
                     electrical=audit(e.reconstructor,z,frame['reconstruction']))
            assert len(frame['drc_locations'])==row['locations']
            assert not any(row['electrical'][k] for k in ('missed_vias','topology','shorts','hard_failures')),row
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        report.update(status='PASS',rows=rows)
    finally:
        e.close();args.output.write_text(json.dumps(clean_json(report),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return 0

if __name__=='__main__':raise SystemExit(main())
