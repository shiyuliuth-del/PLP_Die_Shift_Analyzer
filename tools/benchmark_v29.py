"""Compare two checkouts with the same GD55 input, rules, poses and cold session."""
import argparse,hashlib,json,math,os,platform,sys,tempfile,time
from dataclasses import asdict,replace
from itertools import product
from pathlib import Path
args=argparse.ArgumentParser()
args.add_argument('--checkout',type=Path,default=Path(__file__).resolve().parents[1])
args.add_argument('--gds',type=Path,required=True);args.add_argument('--out',type=Path,required=True)
args.add_argument('--draw',action='store_true');args.add_argument('--graphics-only',action='store_true');args.add_argument('--coupled',action='store_true')
args.add_argument('--coupled-full',action='store_true',help='Verify the full GD55 reconstruction grid and sample complete reconstructed frames')
a=args.parse_args();sys.path.insert(0,str(a.checkout.resolve()))
import numpy as np
from die_shift_studio import __version__
from die_shift_studio.core import Parameters,build_case,read_gds
from die_shift_studio.stack import Connections
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.session_cache import ProjectCacheRegistry
from die_shift_studio.extreme_filter import ProblemIndex
from die_shift_studio.statistics import ExtremeCatalog
from die_shift_studio.settings import ViewSettings
from die_shift_studio.resources import resource_profile
from die_shift_studio.acceleration import capabilities

def digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def graphics(d,roles,order,p,engine,frame,summary):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from die_shift_studio.gui import MainWindow,W
    app=W.QApplication.instance() or W.QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        w=MainWindow(jobs_dir=Path(tmp)/'Jobs');w.resize(1440,900);w.show();app.processEvents()
        w.set_data(d,dict(schema_version=5,roles=roles,layer_order=order,parameters=asdict(p)))
        w.case=engine.contact.case;w.engine=engine;w.geometry_cache=engine.contact.cache
        w.width_toggle.blockSignals(True);w.width_toggle.setChecked(True);w.width_toggle.blockSignals(False)
        w.current_frame=dict(frame,nominal_global=True,width_summary=summary);w.plot_layout();app.processEvents()
        refs=sorted(summary['lines']);draw=[];pick=[]
        for _ in range(5):
            start=time.perf_counter();w.plot_layout(preserve=True);app.processEvents();draw.append(time.perf_counter()-start)
            start=time.perf_counter();w.selection.apply_hits(refs,'replace');app.processEvents();pick.append(time.perf_counter()-start)
            w.selection.clear();app.processEvents()
        repeated=[]
        for _ in range(5):
            start=time.perf_counter();w.selection.apply_hits(refs,'replace');app.processEvents();repeated.append(time.perf_counter()-start)
            w.selection.clear();app.processEvents()
        w.close();app.processEvents()
        return dict(nominal_redraw_median_s=float(np.median(draw)),select_all_risks_median_s=float(np.median(pick)),
                    repeated_at_stable_view_s=float(np.median(repeated)),selected=len(refs))


def verify_and_couple(engine,summary,grid,cfg):
    from die_shift_studio.coupled_filter import scan
    replayed=0
    for ref,witness in summary.get('witnesses',{}).items():
        i=witness['index'];z=grid[i]['states'] if i>=0 else np.zeros((grid.count,3))
        data=engine.frame(z)['width_analysis']
        if witness['kind']=='channel':
            matches=[q for q in data['rows'] if ref in q['refs'] and q['pair']==witness['pair'] and
                     q['status']==summary['lines'][ref][0] and np.allclose(q['nominal_witness_um'],witness['nominal_ends'])]
            assert any(all(np.isclose(q[k],witness[k]) for k in ('original','required','current','narrowing_um','gap_um')) for q in matches)
        else:assert any(q['layer']==ref[0] and ref[1] in q['indices'] and np.isclose(q['gap_um'],witness['gap_um']) for q in data['edge_violations'])
        replayed+=1
    if not a.coupled:return replayed,None
    # Both revisions solve the same 3^3 combinations of the three local X poses.
    p=replace(engine.p,multi_bound_y_um=0,bound_angle_deg=0)
    e=PreviewEngine(engine.contact.cache,engine.contact.case,engine.roles,{},p,None,engine.connections);e.signature='coupled-small'
    try:
        g=ExtremeCatalog(engine.contact.case,p);e.configure_width(True,cfg);e.configure_reconstruction(True,cfg)
        warm=e.width_summary(lambda *_:None)
        from unittest.mock import patch
        with patch.object(e,'width_summary',return_value=warm):
            start=time.perf_counter();c=scan(e,g,cfg,True,None,lambda *_:None);elapsed=time.perf_counter()-start
        if 'indices' in c:indices=c['indices']
        else:
            comp=ProblemIndex(g,c['red_factors']);indices=[comp.at(i) for i in range(comp.total)]
        return replayed,dict(states=g.total+1,scanned=c['scanned'],time_s=elapsed,indices=indices,
                             lines=digest(sorted((str(k),v) for k,v in c['lines'].items())))
    finally:e.close()


def main():
    d=read_gds(a.gds);known={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    roles={k:known.get(k,'不参与') for k in d.layers};order=['6/0','5/0','4/0','3/0']
    p=Parameters();cfg=ViewSettings();cache=GeometryCache(d);con=Connections(d,roles,order,cache.geometries)
    case=build_case(d,roles,connections=con);grid=ExtremeCatalog(case,p);registry=ProjectCacheRegistry()
    group=registry.get('bench');engine=PreviewEngine(cache,case,roles,{},p,None,con,group,'bench');engine.signature='bench'
    try:
        engine.configure_width(True,cfg);start=time.perf_counter();summary=engine.width_summary(lambda *_:None)
        cold=time.perf_counter()-start;start=time.perf_counter();engine.width_summary(lambda *_:None);warm=time.perf_counter()-start
        if a.graphics_only:
            view=graphics(d,roles,order,p,engine,engine.frame(np.zeros((grid.count,3))),summary)
            a.out.write_text(json.dumps(view,indent=2)+'\n',encoding='utf8');print(view,flush=True);return
        index=ProblemIndex(grid,summary['red_factors']);red=[index.at(j) for j in range(index.total)]
        poses=[np.zeros((grid.count,3))]
        for sx,sy,st in product((-1,1),repeat=3):poses.append(np.tile([sx*35.,sy*35.,st*math.pi/180],(grid.count,1)))
        for i in (0,1,2,0,1):
            z=poses[-1].copy();z[i]*=-1;poses.append(z)
        actual=[];times=[];rows=0
        for z in poses:
            start=time.perf_counter();frame=engine.frame(z);times.append(time.perf_counter()-start)
            channels=frame['width_analysis']['rows'];rows+=len(channels)
            actual.append([[q[k] for k in ('plane','pair','refs','status','original','required','current','gap_um','narrowing_um','point','direction','witness_um','nominal_witness_um','locked','split')] for q in channels])
        replay=[]
        for z in poses:
            start=time.perf_counter();engine.frame(z);replay.append(time.perf_counter()-start)
        verified,coupled=verify_and_couple(engine,summary,grid,cfg)
        full=None
        if a.coupled_full:
            from die_shift_studio.coupled_filter import scan
            engine.configure_reconstruction(True,cfg)
            start=time.perf_counter();result=scan(engine,grid,cfg,True,None,lambda *_:None)
            seconds=time.perf_counter()-start;index_full=ProblemIndex(grid,result['red_factors'])
            samples=[[0]*grid.count,*[grid.choices(index_full.at(j)) for j in (0,11,index_full.total-1) if 0<=j<index_full.total],
                     *np.random.default_rng(29).integers(0,grid.radices,(8,grid.count)).tolist()]
            positives=0
            for digits in samples:
                w=engine.frame(np.array([v[d] for v,d in zip(grid.locals,digits)]))['width_analysis']
                hit=bool(w['counts']['宽度不足'] or w['edge_violations']);positives+=hit
                assert hit==index_full.matches(digits),(digits,hit)
            full=dict(states=result['package_states'],local_checks=result['scanned'],boundary_sources=result['boundary_sources'],
                      red_states=index_full.total,time_s=seconds,compared=len(samples),red_samples=positives)
            engine.configure_reconstruction(False,cfg)
        drawing=graphics(d,roles,order,p,engine,engine.frame(poses[0]),summary) if a.draw else None
        out=dict(version=__version__,witnesses_replayed=verified,coupled=coupled,coupled_full=full,graphics=drawing,platform=platform.system(),hardware=resource_profile(),gpu=capabilities(),gds_sha=d.sha256,
                 shape_count=sum(map(len,d.layers.values())),sigma=6,process_um=[15,15,15],offset=[35,35,1],
                 grid_states=grid.total+1,red_states=index.total,risk_lines=len(summary['lines']),paired_row_count=rows,
                 hashes=dict(lines=digest(sorted((str(ref),v) for ref,v in summary['lines'].items())),red=digest(red),full_rows=digest(actual)),
                 times_s=dict(cold_summary=cold,summary_hit=warm,new_frame_sum=sum(times[1:]),frame_median=float(np.median(times[1:])),
                              existing_frame_sum=sum(replay)))
        a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(__version__,out['hashes']['full_rows'],out['times_s'],flush=True)
    finally:engine.close();registry.close()

if __name__=='__main__':main()
