"""Full-DIE field of fixed-layer spacing plus simultaneous displacement bound."""
import numpy as np
from shapely.ops import unary_union
from .core import uncertainty_bound
from .statistics import statistical_state

def space_maps(model,p,resolution=81):
    model.configure_parameters(p)
    case=model.case;owners={}
    for record in model.records:owners.setdefault(record['owner'],set()).add(record['key'])
    maps=[]
    for owner,layers in owners.items():
        die=case['dies'][owner];c=model.bound.centers[owner];local=p.for_die(owner);state=statistical_state(local,len(case['dies']))
        fanout=[n['poly'] for n in model.nodes if n['owner']==owner and model.roles[n['key']] in ('运动RDL','运动过孔')]
        domain=unary_union([die,*fanout]);x0,y0,x1,y1=domain.bounds
        dx=x1-x0;dy=y1-y0;nx=max(25,round(resolution*dx/max(dx,dy)));ny=max(25,round(resolution*dy/max(dx,dy)))
        xe=np.linspace(x0,x1,nx+1);ye=np.linspace(y0,y1,ny+1)
        xx,yy=np.meshgrid((xe[1:]+xe[:-1])/2,(ye[1:]+ye[:-1])/2)
        points=np.column_stack((xx.ravel(),yy.ravel()))
        extra=uncertainty_bound(points,c,local,len(case['dies']),state=state).reshape(ny,nx)
        maps.append(dict(owner=owner,layers=sorted(layers),x_edges=xe,y_edges=ye,
                         spacing=extra+p.line_space_um,extra=extra,die=np.asarray(die.exterior.coords),domain=domain))
    return dict(maps=maps,base_spacing_um=p.line_space_um,target_sigma=p.target_nsigma,
                definition='S(P)=固定线距+Q(P)；Q为整包目标σ下相对固定层的保守位移；不含孔径或最小包围量',
                interface_count=len(model.records))


def package_map(field,board,centers,states,visible_layers=None,resolution=101):
    """Package grid: base spacing off moving domains, maximum at overlaps.

    Interpolate the already computed requirement field in each DIE's local
    coordinates. The domain test, not the rectangular image extent, selects it.
    """
    from shapely import intersects_xy,polygons,intersects,prepare
    from shapely.affinity import affine_transform
    from scipy.ndimage import map_coordinates
    x0,y0,x1,y1=board.bounds;dx=x1-x0;dy=y1-y0
    nx=max(25,round(resolution*dx/max(dx,dy)));ny=max(25,round(resolution*dy/max(dx,dy)))
    xe=np.linspace(x0,x1,nx+1);ye=np.linspace(y0,y1,ny+1);xx,yy=np.meshgrid((xe[1:]+xe[:-1])/2,(ye[1:]+ye[:-1])/2)
    values=np.full(xx.shape,field['base_spacing_um']);parts=[]
    # Cell intersections preserve narrow metal even when no sample centre lands on it.
    corners=np.stack([np.stack((xx+sx*np.diff(xe)[0]/2,yy+sy*np.diff(ye)[0]/2),axis=-1) for sx,sy in [(-1,-1),(1,-1),(1,1),(-1,1)]],axis=-2)
    for q in field['maps']:
        c=centers[q['owner']];z=states[q['owner']] if states is not None else np.zeros(3);co,si=np.cos(z[2]),np.sin(z[2])
        ax=xx-c[0]-z[0];ay=yy-c[1]-z[1];x=co*ax+si*ay+c[0];y=-si*ax+co*ay+c[1]
        domain=affine_transform(q['domain'],[co,-si,si,co,c[0]+z[0]-co*c[0]+si*c[1],c[1]+z[1]-si*c[0]-co*c[1]])
        local=corners-c-z[:2];local=local@np.array([[co,-si],[si,co]])+c
        prepare(q['domain']);touched=intersects(q['domain'],polygons(local.reshape(-1,4,2))).reshape(xx.shape)
        row=(y[touched]-q['y_edges'][0])/np.diff(q['y_edges'])[0]-.5;col=(x[touched]-q['x_edges'][0])/np.diff(q['x_edges'])[0]-.5
        sampled=map_coordinates(q['spacing'],[row,col],order=1,mode='nearest') if np.isfinite(q['spacing']).all() else np.full(row.shape,np.inf)
        parts.append((q['layers'],domain.intersection(board),touched,sampled))
        inside=intersects_xy(q['domain'],x,y)
        if not inside.any():continue
        a=q['spacing'];row=(y[inside]-q['y_edges'][0])/np.diff(q['y_edges'])[0]-.5;col=(x[inside]-q['x_edges'][0])/np.diff(q['x_edges'])[0]-.5
        sampled=map_coordinates(a,[row,col],order=1,mode='nearest') if np.isfinite(a).all() else np.full(row.shape,np.inf)
        values[inside]=np.maximum(values[inside],sampled)
    values=np.ma.masked_where(~intersects_xy(board,xx,yy),values)
    field=dict(spacing=values,parts=parts,base=field['base_spacing_um'],extent=[x0,x1,y0,y1],x_edges=xe,y_edges=ye)
    return package_display(field,visible_layers)


def package_display(full,visible_layers):
    display=np.full(full['spacing'].shape,full['base'],dtype=float)
    domains=[]
    for layers,domain,mask,sampled in full['parts']:
        if visible_layers is not None and not set(layers).intersection(visible_layers):continue
        display[mask]=np.maximum(display[mask],sampled);domains.append(domain)
    return dict(full,display_spacing=display,domain=unary_union(domains),active=bool(domains))
