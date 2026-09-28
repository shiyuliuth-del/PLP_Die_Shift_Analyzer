"""Read-only, workload-specific V12 timing, including warm validated previews."""
import argparse,json,time,statistics,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.resources import resource_profile
from die_shift_studio import __version__

def timed(fn):
    t=time.perf_counter();value=fn();return value,1000*(time.perf_counter()-t)
def run(path,q,z,frames):
    data,load=timed(lambda:read_gds(path));case=build_case(data,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(data);p=Parameters.from_dict(q.get('parameters',{}))
    engine=PreviewEngine(cache,case,q['roles'],{},p,{},case['connections']);engine.configure_deformation(True,ViewSettings());engine.configure_drc(True,ViewSettings())
    last=[0.]
    def progress(n,text):
        now=time.monotonic()
        if now-last[0]>4:print(f'{path.name}: {text}',flush=True);last[0]=now
    first,ms=timed(lambda:engine.frame(z,progress));samples=[]
    for i in range(frames):
        pose=z.copy();pose[:,:2]+=.05*(i+1)
        f,t=timed(lambda:engine.frame(pose,progress,quick=True));samples.append(t)
    d=first['deformation'];report=dict(sha256=data.sha256,load_ms=load,interfaces=len(engine.contact.records),full_frame_ms=ms,accepted=d['accepted_count'],best_effort=d['best_effort_count'],blocked=d['blocked_count'],warm_quick_samples_ms=samples,warm_quick_median_ms=statistics.median(samples),warm_quick_max_ms=max(samples))
    print(json.dumps(report,ensure_ascii=False),flush=True);return report

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--attachment',action='store_true');ap.add_argument('--output',default='results/performance_V12.json');a=ap.parse_args()
    result=dict(version=__version__,hardware=resource_profile(),note='Wall time; DRC on. Warm quick frames revalidate the prior topology; idle refinement searches the full finite family. No 30 FPS claim.')
    q=json.loads((ROOT/'examples/示例G_小偏移与三段追溯.json').read_text());result['example_G']=run(ROOT/'examples'/q['gds_path'],q,np.array([[1.,1,0],[0,35,0]]),8)
    if a.attachment:
        path=Path('/home/niubi/下载/GD55_9-21.gds')
        q=dict(roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'},layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'])
        result['GD55']=run(path,q,np.tile([35.,-35,.01],(3,1)),3)
    (ROOT/a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
