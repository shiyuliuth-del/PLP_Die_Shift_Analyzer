"""Read-only real-GDS verification, benchmark and publication-style figures."""
from pathlib import Path
import sys,json,time,hashlib,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import gdstk
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap,Normalize
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry import Polygon
from shapely.ops import unary_union
from die_shift_studio.core import read_gds,build_case,Parameters,clean_json
from die_shift_studio.geometry_errors import repair_gds_polygon
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.contact import ContactModel
from die_shift_studio.preview import default_motion
from die_shift_studio.space_map import space_maps
from die_shift_studio.probability_overlay import probability_maps
from die_shift_studio.recognition import automatic_parameters
ROOT=Path(__file__).resolve().parents[1]

def main(path):
    before=hashlib.sha256(path.read_bytes()).hexdigest();start=time.perf_counter();d=read_gds(path);read_seconds=time.perf_counter()-start
    roles={k:'不参与' for k in d.layers};roles.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'})
    start=time.perf_counter();case=build_case(d,roles);cache=GeometryCache(d);con=case['connections'];p=Parameters(**automatic_parameters(d,roles)[0]);model=ContactModel(d,case,roles,con,cache);setup_seconds=time.perf_counter()-start
    start=time.perf_counter();maps=space_maps(model,p);space_seconds=time.perf_counter()-start
    start=time.perf_counter();prob=probability_maps(cache,model.bound,roles,p,masks=model.interface_masks);probability_seconds=time.perf_counter()-start
    e=PreviewEngine(cache,case,roles,{k:default_motion(v) for k,v in roles.items()},p,None,con);times=[]
    for i in range(21):
        state=np.array([[35+i*.01,-35,.01],[-35,35+i*.01,-.01],[35,-35-i*.01,.01]])
        start=time.perf_counter();e.frame(state);elapsed=time.perf_counter()-start
        if i:times.append(elapsed)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.labelcolor':'#405169','text.color':'#30435b','axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})
    raw=[q.points for q in gdstk.read_gds(path,unit=1e-6).top_level()[0].get_polygons() if (q.layer,q.datatype)==(5,0)]
    missing=[a for a in raw if not Polygon(a).is_valid];old=[a for a in raw if Polygon(a).is_valid]
    fig,axes=plt.subplots(1,2,figsize=(11,4.3),layout='constrained')
    for ax,arrays,title in zip(axes,[old,d.layers['5/0']],['a  Previous import: skipped winding contour','b  Repaired import: holes and copper preserved']):
        ax.add_collection(PolyCollection(arrays,facecolors='#c1d2e7',edgecolors='none'))
        ax.set(xlim=(-3300,3300),ylim=(-1800,1800),xlabel='X / μm',ylabel='Y / μm',title=title);ax.set_aspect('equal');ax.tick_params(labelsize=8)
    repaired,_=repair_gds_polygon(missing[0],d.dbu_um);shape=unary_union([Polygon(a) for a in repaired])
    for ring in [shape.exterior,*shape.interiors]:xy=np.asarray(ring.coords);axes[1].plot(xy[:,0],xy[:,1],color='#475b76',lw=.65)
    for suffix in ('png','svg'):fig.savefig(ROOT/f'figures/v060_gds_repair.{suffix}',dpi=300)
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,5.7),layout='constrained')
    cmap=LinearSegmentedColormap.from_list('space',['#f2f4dd','#c4dfcd','#9abed6','#8b85b2'])
    lo=min(q['spacing'].min() for q in maps['maps']);hi=max(q['spacing'].max() for q in maps['maps']);norm=Normalize(lo,hi)
    for q in maps['maps']:
        artist=ax.pcolormesh(q['x_edges'],q['y_edges'],q['spacing'],cmap=cmap,norm=norm,rasterized=True)
        artist.set_clip_path(PathPatch(MplPath(q['die']),transform=ax.transData))
        die=case['dies'][q['owner']];xy=np.asarray(die.exterior.coords);ax.plot(xy[:,0],xy[:,1],color='#758797',lw=.7)
        ax.text(die.centroid.x,die.centroid.y,f'DIE{q["owner"]+1}',ha='center',va='center',fontsize=10,color='#41566b')
    xy=np.asarray(case['board'].exterior.coords);ax.plot(xy[:,0],xy[:,1],color='#8c99a7',lw=.8)
    ax.set(xlim=(-3450,3450),ylim=(-1900,1900),xlabel='X / μm',ylabel='Y / μm',title='Space Map · total spacing required at each position');ax.set_aspect('equal')
    fig.colorbar(artist,ax=ax,shrink=.7,pad=.025,label='Fixed spacing + conservative shift allowance / μm')
    fig.text(.5,.005,'Target: 6σ per package  |  Translation: ±35 μm at 6σ  |  Rotation: ±1° at 6σ  |  Base spacing: 5 μm',ha='center',fontsize=8,color='#65758a')
    for suffix in ('png','svg'):fig.savefig(ROOT/f'figures/v060_space_requirement.{suffix}',dpi=300)
    plt.close(fig)
    metrics=dict(source=str(path),source_sha256=before,source_unchanged=before==hashlib.sha256(path.read_bytes()).hexdigest(),
        repaired=d.repaired_geometry,ignored=len(d.ignored_geometry),layer_5_union_area_um2=unary_union([Polygon(a) for a in d.layers['5/0']]).area,
        klayout_0308_layer_5_area_um2=7001062.03804,connections=con.summary(),owners_4_0=[int((case['ownership']['4/0']==i).sum()) for i in range(3)],
        space_map=[dict(die=q['owner']+1,min_um=float(q['spacing'].min()),max_sampled_um=float(q['spacing'].max()),grid_shape=list(q['spacing'].shape)) for q in sorted(maps['maps'],key=lambda x:x['owner'])],
        performance=dict(read_and_repair_ms=1000*read_seconds,trace_and_cache_ms=1000*setup_seconds,space_map_ms=1000*space_seconds,probability_ms=1000*probability_seconds,
            distinct_frame_median_ms=1000*float(np.median(times)),distinct_frame_p95_ms=1000*float(np.quantile(times,.95)),samples=len(times),resources=cache.profile),
        notes=['Real GDS in current Linux Python runtime; frame timings exclude Qt rendering','Grid extrema are sampled values, not corner maxima','Only confirmed 3/4/5/6 plus borders participate; lowest participating 3/0 is the chip-contact plane'])
    (ROOT/'results/GD55_导入与性能_v060.json').write_text(json.dumps(clean_json(metrics),ensure_ascii=False,indent=2))
    print(json.dumps(metrics['performance'],ensure_ascii=False,indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('gds',type=Path);args=parser.parse_args();main(args.gds)
