"""Reproducible fanout/area evidence, scientific figures, and real-GDS timing."""
from pathlib import Path
import json,sys,time,hashlib
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import PathPatch
from matplotlib.colors import LinearSegmentedColormap,Normalize
from dataclasses import replace
from die_shift_studio.core import read_gds,build_case,Parameters,clean_json
from die_shift_studio.contact import ContactModel
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.preview import default_motion
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.space_map import space_maps
from die_shift_studio.settings import ViewSettings
from die_shift_studio.plot_geometry import geometry_path
from die_shift_studio.recognition import automatic_parameters

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelcolor':'#405169','text.color':'#30435b','axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','axes.titlepad':12})

def measure(fn,repeats=1):
    elapsed=[]
    for _ in range(repeats):
        start=time.perf_counter();value=fn();elapsed.append((time.perf_counter()-start)*1000)
    return value,dict(median_ms=float(np.median(elapsed)),p95_ms=float(np.quantile(elapsed,.95)),samples_ms=elapsed)
def setup(path,roles=None,params=None):
    d=read_gds(path);roles=roles if roles is not None else json.loads(Path(path).with_suffix('.json').read_text())['roles'];c=build_case(d,roles);m=ContactModel(d,c,roles,c['connections'])
    p=params or Parameters(**automatic_parameters(d,roles)[0]);return d,c,m,p

def fanout_figures():
    project=json.loads((ROOT/'examples/示例D_芯片外扇出.json').read_text());d,c,m,p=setup(ROOT/'examples'/project['gds_path'],project['roles'],Parameters.from_dict(project['parameters']))
    field=space_maps(m,p);cfg=ViewSettings(probability_method='quadrature');prob,probtime=measure(lambda:probability_maps(m.cache,m.bound,m.roles,p,masks=m.interface_masks,settings=cfg))
    frame=m.current(np.array([[65.,0,0],[0,0,0]]),p)
    fig,axes=plt.subplots(1,3,figsize=(13,4.5));fig.subplots_adjust(left=.05,right=.985,bottom=.30,top=.88,wspace=.32)
    ax=axes[0];colors=['#aac7b3','#b9cbe3'];norm=Normalize(min(q['spacing'].min() for q in field['maps']),max(q['spacing'].max() for q in field['maps']))
    cmap=LinearSegmentedColormap.from_list('space',['#f2f4dd','#c4dfcd','#9abed6','#8b85b2'])
    for q in field['maps']:
        a=ax.imshow(q['spacing'],extent=[q['x_edges'][0],q['x_edges'][-1],q['y_edges'][0],q['y_edges'][-1]],origin='lower',cmap=cmap,norm=norm,interpolation='bilinear',zorder=2)
        a.set_clip_path(PathPatch(geometry_path(q['domain']),transform=ax.transData))
        die=c['dies'][q['owner']];ax.add_patch(PathPatch(geometry_path(die),facecolor='none',edgecolor='#657e97',lw=.7,zorder=4))
        ax.text(die.centroid.x,die.centroid.y,f'DIE {q["owner"]+1}',ha='center',va='center',fontsize=9)
    for record in m.records:ax.add_patch(PathPatch(geometry_path(record['poly']),facecolor='#3d6b81',edgecolor='none',zorder=5))
    ax.set(xlim=(-1900,2400),ylim=(-1250,1400),xlabel='X / μm',ylabel='Y / μm',title='a  Connected fanout outside both DIEs');ax.set_aspect('equal')
    fig.colorbar(a,cax=fig.add_axes([.05,.17,.27,.018]),orientation='horizontal',label='Required spacing / μm')
    ax=axes[1]
    for poly in m.cache.layers['5/0']['geometry'][:2]:ax.add_patch(PathPatch(geometry_path(poly),facecolor='#d3dfee',edgecolor='#91a8c3',lw=.6))
    via=m.moved(m.records[0]['poly'],0,[[65,0,0],[0,0,0]]);ax.add_patch(PathPatch(geometry_path(via),facecolor='#3c7384',edgecolor='none'))
    for name,color in [('annulus','#449c95'),('spacing','#aa7baa')]:
        ax.add_patch(PathPatch(geometry_path(frame['regions'][0][name]),facecolor='none',edgecolor=color,ls='--',lw=1))
    for q in frame['intrusions']:ax.add_patch(PathPatch(geometry_path(q['geometry']),facecolor='#de4d5a',alpha=.40,edgecolor='none',zorder=5))
    issue=next(q for q in frame['issues'] if q['scheme']==3)
    ax.plot(*np.array([issue['origin'],issue['point']]).T,ls='--',color='#7c858d',lw=.8,zorder=6)
    ax.scatter(*issue['point'],facecolors='none',edgecolors='#ba2941',s=65,linewidths=1.3,zorder=7)
    ax.plot([0,65],[1000,1000],ls='--',color='#d39045',lw=1)
    ax.set(xlim=(-50,145),ylim=(945,1060),xlabel='X / μm',ylabel='Y / μm',title='b  Scheme 3: actual intrusion area');ax.set_aspect('equal')
    area=sum(q['area_um2'] for q in frame['intrusions']);fig.text(.378,.13,f'DIE 1: DX = 65 μm, DY = 0, θ = 0°\nRed intersection: {area:.3f} μm²',fontsize=8)
    ax=axes[2];q=prob['maps'][0];cmap=LinearSegmentedColormap.from_list('prob',['#f2f7fa','#b7d4e2','#3d7c9a'])
    a=ax.imshow(q['probability'],origin='lower',extent=q['extent'],cmap=cmap,vmin=0,vmax=1,alpha=.75)
    ax.add_patch(PathPatch(geometry_path(m.records[0]['poly']),facecolor='none',edgecolor='#647c91',ls='--',lw=.8))
    ax.set(xlim=(-55,55),ylim=(955,1045),xlabel='X / μm',ylabel='Y / μm',title='c  Whole-via occupancy probability');ax.set_aspect('equal')
    fig.colorbar(a,cax=fig.add_axes([.714,.17,.27,.018]),orientation='horizontal',label='Occupancy probability',ticks=[0,.25,.5,.75,1])
    from matplotlib.ticker import MaxNLocator
    axes[0].xaxis.set_major_locator(MaxNLocator(5))
    for ax in axes:ax.set_anchor('N');ax.tick_params(labelsize=8);ax.grid(alpha=.09);ax.set_axisbelow(True)
    for suffix in ('png','svg'):fig.savefig(ROOT/f'figures/v070_fanout_area.{suffix}',dpi=300,bbox_inches='tight')
    plt.close(fig)
    return dict(name=project['gds_path'],owners=c['ownership']['4/0'].tolist(),interfaces=len(m.records),intrusion_area_um2=area,
        probability=probtime,probability_method=prob['method'],probability_area_checks=[{k:q[k] for k in ('index','via_area_um2','integrated_area_um2','raster_area_relative_error')} for q in prob['maps']],
        space_domains=[dict(die=q['owner']+1,bounds=list(q['domain'].bounds)) for q in field['maps']])

def real_gds(path):
    roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    before=hashlib.sha256(path.read_bytes()).hexdigest();(d,c,m,p),load=measure(lambda:setup(path,roles));timings=dict(read_repair_trace_cache=load)
    prob,timings['whole_via_sobol']=measure(lambda:probability_maps(m.cache,m.bound,roles,p,masks=m.interface_masks),2)
    field,timings['space_with_fanout']=measure(lambda:space_maps(m,p,101),3)
    engine=PreviewEngine(m.cache,c,roles,{k:default_motion(v) for k,v in roles.items()},p,None,c['connections']);frames=[]
    for i in range(21):
        z=np.array([[35+i*.03,-35,.01],[-35,35+i*.02,-.01],[35,-35-i*.02,.01]])
        _,t=measure(lambda:engine.frame(z))
        if i:frames.extend(t['samples_ms'])
    timings['distinct_frame']=dict(median_ms=float(np.median(frames)),p95_ms=float(np.quantile(frames,.95)),samples_ms=frames)
    return dict(source=path.name,sha256=before,source_unchanged=before==hashlib.sha256(path.read_bytes()).hexdigest(),owners=[int((c['ownership']['4/0']==i).sum()) for i in range(3)],
        interfaces=len(m.records),repaired=len(d.repaired_geometry),ignored=len(d.ignored_geometry),resources=m.cache.profile,timings=timings,
        probability_cell_um=prob['cell_um'],angle_groups=prob['angle_groups'],max_footprint_angle_error_um=max(q['orientation_error_um'] for q in prob['maps']),
        max_raster_area_relative_error=max(q['raster_area_relative_error'] for q in prob['maps']),
        note='Actual user GDS, Linux shared host; timings exclude Qt. Probability is whole-via occupancy, not the v0.6 centre histogram.')

if __name__=='__main__':
    record=dict(fanout=fanout_figures());path=Path('/home/niubi/下载/GD55_9-21.gds')
    if path.exists():record['GD55']=real_gds(path)
    (ROOT/'results/evidence_v070.json').write_text(json.dumps(clean_json(record),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
