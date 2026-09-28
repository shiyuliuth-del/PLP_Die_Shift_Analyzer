"""Auditable geometric screening: required space divided by available space.

U <= 1 is a necessary screening condition under the documented escape-side
assignment, never a routed or manufacturing certificate. Hard input limits,
not probabilities, define demand. No empirical weighting or cost coefficients.
"""
import math
import numpy as np
import shapely
from .statistics import mean_and_bounds


def displacement_bound(poly,center,p,count):
    mu,b=mean_and_bounds(p,count)
    radius=float(np.linalg.norm(shapely.get_coordinates(poly)-center,axis=1).max())
    angle=min(math.pi,abs(mu[2])+b[2])
    return float(np.linalg.norm(np.abs(mu[:2])+b[:2])+2*radius*math.sin(angle/2))


def band_ratio(records,info,layers,width,space,travel,expand=False):
    """One distinct geometric network consumes one track on an assigned side."""
    angle=math.radians(info['angle']);rot=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
    center=np.array([info['cx'],info['cy']]);half=np.array([info['width'],info['height']])/2
    low=-half;high=half
    if expand and records:
        vertices=np.vstack([shapely.get_coordinates(r['poly']) for r in records]);local=(vertices-center)@rot
        low=np.minimum(low,local.min(axis=0));high=np.maximum(high,local.max(axis=0))
    nets=[set() for _ in range(4)]
    for r in records:
        pt=(np.array(r['poly'].centroid.coords[0])-center)@rot
        side=int(np.argmin([abs(pt[0]-low[0]),abs(high[0]-pt[0]),abs(pt[1]-low[1]),abs(high[1]-pt[1])]))
        nets[side].add(r['net'])
    counts=[len(n) for n in nets];lengths=[high[1]-low[1]]*2+[high[0]-low[0]]*2
    per_layer=[math.ceil(n/layers) if layers else math.inf if n else 0 for n in counts]
    demands=[n*width+max(0,n-1)*space+2*travel if n else 0. for n in per_layer]
    ratios=[d/l if l>0 else math.inf if d else 0. for d,l in zip(demands,lengths)]
    return dict(ratio=float(max(ratios)),counts=counts,tracks_per_layer=per_layer,lengths_um=list(map(float,lengths)),
                demand_um=demands,travel_um=travel,layers=layers,width_um=width,space_um=space,
                cut_min_local_um=low.tolist(),cut_max_local_um=high.tolist(),
                hypothesis='每个几何网络一条轨道，按最近切边分配并均分到可用层；同DIE束边界只留一对Q，非每条线留2Q')


def scheme_metrics(model,p):
    case=model.case;count=len(case['dies']);model.configure_parameters(p)
    fixed_planes={k.split('/')[0] for k,v in model.roles.items() if v=='固定RDL'}
    moving_planes={k.split('/')[0] for k,v in model.roles.items() if v=='运动RDL'}
    # Actual distinct metal planes, never count two datatypes twice.
    fixed_layers=min(len(fixed_planes),p.panel_layers);moving_layers=min(len(moving_planes),p.wafer_layers)
    rows=[];net_owners={}
    for r in model.records:net_owners.setdefault(r['net'],set()).add(r['owner'])
    captures=[]
    for node in model.nodes:
        if model.roles[node['key']]!='固定过孔':continue
        owner_set=net_owners.get(node['net'],set())
        if len(owner_set)!=1:continue
        owner=next(iter(owner_set));key=node['key'];index=node['index'];targets=[]
        for links,side in [(model.connections.outgoing[key].get(index,[]),'lower'),(model.connections.incoming[key].get(index,[]),'upper')]:
            for link in links:
                if model.roles.get(link[side])=='固定RDL':targets.append(link[side].split('/')[0])
        if not targets:continue
        foreign=[j for plane in set(targets) for j in model.planes[plane]
                 if model.roles[model.nodes[j]['key']]=='固定RDL' and model.nodes[j]['net']!=node['net']]
        foreign_distance=min((node['poly'].distance(model.nodes[j]['poly']) for j in foreign),default=math.inf)
        free=foreign_distance-p.annulus_um-p.line_space_um
        terms=dict(foreign_gap_um=foreign_distance,foreign_margin_um=free,package_margin_um=node['poly'].distance(case['board'].boundary)-p.annulus_um if case['board'].covers(node['poly']) else -math.inf,keepout_margin_um=math.inf,pair_half_margin_um=math.inf)
        # Capture-pad growth is also bounded by package/keepout geometry.
        free=min(free,terms['package_margin_um'])
        for obstacle in case['obstacles']:terms['keepout_margin_um']=min(terms['keepout_margin_um'],node['poly'].distance(obstacle)-p.annulus_um-p.line_space_um)
        free=min(free,terms['keepout_margin_um'])
        q=displacement_bound(node['poly'],model.bound.centers[owner],p.for_die(owner),count)
        captures.append(dict(die=owner+1,key=key,index=index,net=node['net'],planes=set(targets),poly=node['poly'],need_um=q,free_um=free,margin_terms=terms))
    # Two enlarged pads share one gap; allocate half to each. This deliberately
    # sacrifices asymmetrical optimal growth for a simple conservative budget.
    for i,a in enumerate(captures):
        for b in captures[:i]:
            if a['net']==b['net'] or not a['planes'].intersection(b['planes']):continue
            half=(a['poly'].distance(b['poly'])-2*p.annulus_um-p.line_space_um)/2
            a['free_um']=min(a['free_um'],half);b['free_um']=min(b['free_um'],half)
            for item in (a,b):item['margin_terms']['pair_half_margin_um']=min(item['margin_terms']['pair_half_margin_um'],half)
    for i,info in enumerate(case['infos']):
        records=[r for r in model.records if r['owner']==i];local=p.for_die(i);center=model.bound.centers[i]
        q=max((displacement_bound(r['poly'],center,local,count) for r in records),default=0.)
        route=band_ratio(records,info,fixed_layers,p.line_width_um,p.line_space_um,q)
        fanout=band_ratio(records,info,fixed_layers,p.line_width_um,p.line_space_um,q,expand=True)
        # Lowest moving metal touching the DIE is the only observed source-port
        # evidence; each independent geometry net is counted once per side.
        terminals=[]
        if model.connections.groups:
            terminal_keys=set(model.connections.groups[-1])
            terminals=[n for n in model.nodes if n['key'] in terminal_keys and n['owner']==i and n['poly'].intersects(case['dies'][i])]
        pre=band_ratio(terminals or records,info,moving_layers,p.wafer_line_width_um,p.wafer_line_space_um,0.)
        cap=[dict(die=c['die'],layer=c['key'],index=c['index'],need_um=c['need_um'],free_um=c['free_um'],
                  ratio=c['need_um']/c['free_um'] if c['free_um']>0 else math.inf,margin_terms=c['margin_terms']) for c in captures if c['die']==i+1]
        u2=max((c['ratio'] for c in cap),default=math.inf)
        rows.append(dict(die=i+1,route=route,fanout=fanout,pre_escape=pre,captures=cap,
                         u1=route['ratio'],u2=u2,u3=max(fanout['ratio'],pre['ratio'])))
    return dict(rows=rows,fixed_layers=fixed_layers,moving_layers=moving_layers,
                formula='U1=max(Wchip/ℓchip); U2=max(Qfixed/Mfixed); U3=max(Wfanout/ℓfanout,Wmoving/ℓchip)',
                ranking='最小U优先；差值≤1e−9视为同分，按2→1→3减少工序；U≤1仍须布线/型变验证',
                scope='固定端捕获预算含异网、封装边界与相邻固定孔盘共享间距；整段RDL随动的全层DRC和电性能需另验证；运动RDL分类不证明晶圆预制')
