"""Rerunnable cold/repeat timings and final visible-copper audit, no source edits."""
import sys,json,time,argparse,statistics,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import shapely
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.drc import rule_region
from die_shift_studio.deformation import angle_regions
from die_shift_studio.resources import resource_profile

def timed(fn):
    start=time.perf_counter();value=fn();return value,1000*(time.perf_counter()-start)

def run(path,q,z,figure=False,version="V13"):
    data=read_gds(path);case=build_case(data,q['roles'],layer_order=q['layer_order']);cache=GeometryCache(data);p=Parameters.from_dict(q['parameters']);cfg=ViewSettings()
    e=PreviewEngine(cache,case,q['roles'],{},p,{},case['connections']);e.configure_deformation(True,cfg);e.configure_drc(True,cfg)
    calls=[];original=e.deformer.search;lock=threading.Lock()
    def search(c,*args,**kwargs):
        start=time.perf_counter()
        try:return original(c,*args,**kwargs)
        finally:
            with lock:calls.append(dict(indices=c.indices,ms=1000*(time.perf_counter()-start)))
    e.deformer.search=search;last=[0.]
    def progress(n,text):
        if time.monotonic()-last[0]>5:print(path.name+': '+text,flush=True);last[0]=time.monotonic()
    frame,cold=timed(lambda:e.frame(z,progress));n=len(calls)
    repeated=[timed(lambda:e.frame(z))[1] for _ in range(10)];assert len(calls)==n
    e.configure_drc(False,cfg);e.frame(z);e.configure_drc(True,cfg)
    _,toggle=timed(lambda:e.frame(z));assert len(calls)==n
    # Independent verification on the final replacement set against all visible
    # neighbouring nets, not against hidden original versions of those nets.
    d=frame['deformation'];final=e.deformer._snapshot(d['replacements']);audit=[]
    for key,values in d['replacements'].items():
        ids,geoms,tree=final[key.split('/')[0]]
        for v in values:
            g=v['geometry'];assert v['verified'] and g.is_valid
            assert rule_region(g,p.line_width_um,data.dbu_um).is_empty
            assert rule_region(g,p.line_space_um,data.dbu_um,'notch').is_empty
            assert angle_regions(g,cfg.deformation_min_angle_deg,data.dbu_um).is_empty
            for j in tree.query(g,predicate='dwithin',distance=p.line_space_um):
                if e.contact.nodes[ids[j]]['net']!=v['net']:assert g.distance(geoms[j])+data.dbu_um*.01>=p.line_space_um
            audit.append(dict(layer=key,indices=v['indices'],verified=True))
    assert len(d['interfaces'])==sum(len(c.records) for c in e.deformer.components)
    result=dict(sha256=data.sha256,parameters=q['parameters'],states=z.tolist(),interfaces=len(e.contact.records),copper_components=len(e.deformer.components),
        cold_frame_ms=cold,repeat_frame_median_ms=statistics.median(repeated),repeat_frame_max_ms=max(repeated),toggle_roundtrip_ms=toggle,
        repeat_search_calls=0,search_calls=n,slowest_searches=sorted(calls,key=lambda q:q['ms'],reverse=True)[:5],accepted=d['accepted_count'],
        unadapted=d['unresolved_count'],rejected_displayed=d['best_effort_count'],visible_drc_groups=len(frame['drc']),interface_outcomes=d['interfaces'],audit=audit,
        iteration_history=d['iterations'],joint_rounds=d['joint_rounds'],rollback_rounds=d['rollback_rounds'])
    (ROOT/'results'/(('audit_GD55_' if figure else 'audit_G_')+version+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if figure:draw(e,frame,case,p,version)
    e.close();return result

def draw(e,f,case,p,version='V13'):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt,font_manager
    from matplotlib.patches import PathPatch,Patch
    from die_shift_studio.plot_geometry import geometry_path
    font=next((x for x in ('Noto Sans CJK JP','Microsoft YaHei','DejaVu Sans') if x in {f.name for f in font_manager.fontManager.ttflist}),'sans-serif')
    plt.rcParams.update({'font.family':font,'font.size':10,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    bounds=case['board'].bounds
    for ax,after in zip(axes,(False,True)):
        def add(g,color,**kw):ax.add_patch(PathPatch(geometry_path(g),facecolor=color,edgecolor='none',**kw))
        values=f['deformation']['replacements'].get('5/0',[]) if after else [];hidden={i for v in values for i in v['indices']}
        for i,g in enumerate(e.contact.cache.layers['5/0']['geometry']):
            if i not in hidden:add(g,'#dce6ef')
        for v in values:add(v['geometry'],'#dce6ef');add(v['dark'],'#8badc7')
        for r in e.contact.records:add(e.contact.moved(r['poly'],r['owner'],f['states']) if after else r['poly'],'#467d89')
        for i,die in enumerate(case['dies']):
            if after:die=e.contact.moved(die,i,f['states'])
            xy=np.asarray(die.exterior.coords);ax.plot(xy[:,0],xy[:,1],color='#a9bdb0',lw=.7)
        if after:
            points=[v for d in f['deformation']['diagnostics'] for v in d.get('points',[d['point']])]
            if points:ax.scatter(*np.asarray(points).T,marker='x',s=22,c='#b8752e',linewidths=.9,zorder=5)
        ax.set(xlim=(bounds[0]-100,bounds[2]+100),ylim=(bounds[1]-100,bounds[3]+100),xlabel='X / μm',ylabel='Y / μm',title='b  最终预演：未通过线路保留原图' if after else 'a  原始固定 RDL 与运动过孔');ax.set_aspect('equal')
    fig.legend(handles=[Patch(color='#dce6ef',label='原图 / 固定段'),Patch(color='#8badc7',label='已验证型变'),Patch(color='#467d89',label='运动过孔'),Patch(color='#b8752e',label='未适配位置')],loc='lower center',bbox_to_anchor=(.5,-.04),ncol=4,frameon=False)
    for ext in ('png','svg'):fig.savefig(ROOT/'figures'/(version+'_GD55_final.'+ext),dpi=300,bbox_inches='tight')
    plt.close(fig)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--attachment',action='store_true');a=parser.parse_args()
    q=json.loads((ROOT/'examples/示例G_小偏移与三段追溯.json').read_text())
    result=dict(version='V13',hardware=resource_profile(),scope='Read-only Linux CPU; cold includes native candidate validation. Repeat uses session cache, not a fresh solve.')
    result['example_G']=run(ROOT/'examples'/q['gds_path'],q,np.array([[1.,1,0],[0,35,0]]))
    if a.attachment:
        q=dict(roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'},layer_order=['6/0','5/0','4/0','3/0','128/0','127/0'],parameters=dict(line_width_um=20,line_space_um=20,annulus_um=10,multi_bound_x_um=25,multi_bound_y_um=25,bound_angle_deg=1))
        result['GD55']=run(Path('/home/niubi/下载/GD55_9-21.gds'),q,np.tile([-25.,25.,np.pi/180],(3,1)),True)
    (ROOT/'results/performance_V13.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('V13_BENCHMARK_PASS',flush=True)
if __name__=='__main__':main()
