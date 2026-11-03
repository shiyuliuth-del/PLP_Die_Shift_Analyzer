"""Exact full-grid summary on the original RDL's pad channels.

Endpoint poses determine a throat's section. Only walls whose swept envelopes
reach that section can change third-pad occupancy or the split-lane evidence.
Independent DIE therefore remain free digits rather than multiplying solves.
"""
from collections import defaultdict
from dataclasses import replace
from itertools import product
import math
import numpy as np
import shapely
from .width_analysis import ADEQUATE,BLOCKED,NARROW
from .edge_clearance import edge_violations
from .statistics import ExtremeCatalog

SEVERITY={NARROW:1,BLOCKED:2}


def strongest(found,refs,status,event,binary):
    if event and event.get('low'):return
    evidence='binary' if binary else 'exceeded' if event and event.get('lower',0)>event['threshold'] else 'undetermined'
    rank=(SEVERITY[status],evidence!='undetermined')
    for ref in refs:
        old=found.get(ref)
        if old is None or rank>(SEVERITY[old[0]],old[1]!='undetermined'):
            found[ref]=(status,evidence)


def subset(plane,ids,wall_ids):
    """Compact indices keep a local solve proportional to participating geometry."""
    walls=plane['walls'];wall_ids=sorted(wall_ids)
    wall_map={v:i for i,v in enumerate(wall_ids)}
    features=sorted({f for k in ids for f in plane['gates'][k].features})
    fmap={v:i for i,v in enumerate(features)}
    gates=[replace(plane['gates'][k],pair=tuple(wall_map[i] for i in plane['gates'][k].pair),
                   features=tuple(fmap[i] for i in plane['gates'][k].features)) for k in ids]
    return dict(gates=gates,walls=[walls[i] for i in wall_ids],keys=plane['keys'],
                metal_count=sum(i<plane['metal_count'] for i in wall_ids),pieces=plane['pieces'][features],
                parents=np.array([wall_map[plane['parents'][i]] for i in features],int),local=plane['local'][features])


def combinations(grid,owners):
    for digits in product(*(range(grid.radices[i]) for i in owners)):
        states=np.zeros((grid.count,3))
        for i,d in zip(owners,digits):states[i]=grid.locals[i][d]
        yield digits,states


def sweep_sections(analyzer,plane,grid,owners,progress):
    """Vectorize independent endpoint pairs in bounded blocks of discrete poses."""
    gates,walls=plane['gates'],plane['walls']
    orig=np.array([g.ends for g in gates]);feat=np.array([g.features for g in gates])
    pairs=np.array([g.pair for g in gates]);owner=np.array([w.owner for w in walls])
    def bank(geos,ids):
        out=np.empty((max(grid.radices),len(geos)),object);out[0]=geos
        for o in np.unique(ids):
            ix=np.flatnonzero(ids==o)
            if o<0:continue
            template=analyzer._templates(geos[ix],ids[ix])
            for j,pose in enumerate(grid.locals[o][1:],1):
                z=np.zeros((grid.count,3));z[o]=pose
                out[j,ix]=analyzer._placed(template,z,False)
        return out
    pieces=bank(plane['pieces'],owner[plane['parents']])
    geos=bank(np.array([w.geometry for w in walls],object),owner)
    radices=tuple(grid.radices[i] for i in owners);count=math.prod(radices)
    steps=max(1,min(128,4096//len(gates)))
    for start in range(0,count,steps):
        progress(start/count)
        size=min(steps,count-start);digits=np.zeros((size,grid.count),int)
        if owners:digits[:,list(owners)]=np.asarray(np.unravel_index(np.arange(start,start+size),radices)).T
        ids=[];shapes=[]
        for side in (0,1):
            o=owner[pairs[:,side]]
            d=np.where(o<0,0,digits[:,np.maximum(o,0)])
            ids.append(d);shapes.append(pieces[d,feat[:,side]])
        changed=(ids[0]!=0)|(ids[1]!=0)
        lines=np.broadcast_to(shapely.linestrings(orig),(size,len(orig))).copy()
        lines[changed]=shapely.shortest_line(shapes[0][changed],shapes[1][changed])
        kk,gg=np.nonzero(changed & np.array([g.anchored for g in gates]))
        if len(gg):
            points=[]
            for side in (0,1):
                o=owner[pairs[gg,side]];v=orig[gg,side].copy()
                for die in np.unique(o[o>=0]):
                    which=o==die;pose=grid.locals[die][ids[side][kk[which],gg[which]]]
                    pivot=analyzer.model.bound.centers[die];xy=v[which]-pivot
                    cs=np.cos(pose[:,2]);sn=np.sin(pose[:,2])
                    v[which]=pivot+pose[:,:2]+np.column_stack((cs*xy[:,0]-sn*xy[:,1],sn*xy[:,0]+cs*xy[:,1]))
                points.append(shapely.points(v))
            left=shapely.shortest_line(points[0],shapes[1][kk,gg]);right=shapely.shortest_line(shapes[0][kk,gg],points[1])
            alt=np.where(shapely.length(left)<=shapely.length(right),left,right)
            gap=shapely.length(lines[kk,gg]);use=(gap>analyzer.tol)&(shapely.length(alt)<=gap+analyzer.tol)
            lines[kk[use],gg[use]]=alt[use]
        inside=shapely.covers(analyzer.inside,lines)
        for side in (0,1):
            ix=np.flatnonzero(plane['local'][feat[:,side]])
            if len(ix):
                inside[:,ix] &= ~shapely.relate_pattern(lines[:,ix],geos[ids[side][:,ix],pairs[ix,side]],'T********')
        ends=shapely.get_coordinates(lines.ravel()).reshape(size,-1,2,2)
        ref=np.broadcast_to(orig[:,1]-orig[:,0],(size,len(gates),2)).copy()
        common=(owner[pairs[:,0]]==owner[pairs[:,1]]) & (owner[pairs[:,0]]>=0)
        for die in owners:
            ix=np.flatnonzero(common & (owner[pairs[:,0]]==die))
            if len(ix):
                ang=grid.locals[die][digits[:,die],2,None];cs,sn=np.cos(ang),np.sin(ang)
                x=ref[:,ix,0].copy();y=ref[:,ix,1].copy()
                ref[:,ix,0]=cs*x-sn*y;ref[:,ix,1]=sn*x+cs*y
        order=np.einsum('...i,...i->...',ends[:,:,1]-ends[:,:,0],ref)>0
        yield ends,np.where(inside & order,shapely.length(lines),0.)


def _sweeps(analyzer,plane,grid,progress):
    walls=plane['walls'];gates=plane['gates'];n=len(gates)
    sweeps=np.zeros((len(walls),4));travel=np.zeros(len(walls))
    hulls=[shapely.get_coordinates(w.geometry.convex_hull) for w in walls]
    for i,w in enumerate(walls):
        if w.owner<0:sweeps[i]=w.geometry.bounds;continue
        xy=hulls[i]
        from .preview import rigid_transform
        points=np.vstack([rigid_transform(xy,analyzer.model.bound.centers[w.owner],v) for v in grid.locals[w.owner]])
        sweeps[i]=[*points.min(axis=0),*points.max(axis=0)]
        travel[i]=np.linalg.norm(points.reshape(-1,len(xy),2)-xy,axis=2).max()
    tree=shapely.STRtree(shapely.box(*sweeps.T))
    scope=lambda g:tuple(sorted({walls[i].owner for i in g.pair if walls[i].owner>=0 and grid.radices[walls[i].owner]>1}))
    groups=defaultdict(list)
    bounds=np.tile([np.inf,np.inf,-np.inf,-np.inf],(n,1));risk=np.zeros(n,bool)
    half=.65*(analyzer.width+analyzer.space)
    convex=analyzer.board.equals(analyzer.board.convex_hull)
    interior=[analyzer.inside.covers(shapely.box(*sweeps[i]) if travel[i] else w.geometry) for i,w in enumerate(walls)]
    bounds_pieces=shapely.bounds(plane['pieces'])
    for k,g in enumerate(gates):
        ia,ib=g.pair;fa,fb=g.features;axes=scope(g)
        need=sum(max(analyzer.width,v) for v in g.widths)+(len(g.widths)+1)*analyzer.space
        if not axes:
            bounds[k]=[*g.ends.min(axis=0)-half-analyzer.tol,*g.ends.max(axis=0)+half+analyzer.tol]
            risk[k]=g.nominal_gap<=need+analyzer.tol
            continue
        protected=False
        if convex and interior[ia] and interior[ib]:
            if walls[ia].owner==walls[ib].owner and not plane['local'][fa] and not plane['local'][fb]:
                protected=g.nominal_gap>need+analyzer.tol
            elif not plane['local'][fa] and not plane['local'][fb]:
                normal=(g.ends[1]-g.ends[0])/g.nominal_gap
                sep=np.min(hulls[ib]@normal)-np.max(hulls[ia]@normal)
                protected=sep-travel[ia]-travel[ib]>need+analyzer.tol
        if not protected:
            groups[axes].append(k);continue
        # Bound every possible parent section, including tied parallel
        # stations. Its endpoints lie on their swept features and at most L
        # apart by the Hausdorff triangle inequality.
        ab=bounds_pieces[[fa,fb]].copy()
        delta=travel[[ia,ib],None];ab[:,:2]-=delta;ab[:,2:]+=delta
        limit=g.nominal_gap+travel[ia]+travel[ib]+analyzer.tol
        low=np.maximum(ab[:,:2],ab[::-1,:2]-limit)
        high=np.minimum(ab[:,2:],ab[::-1,2:]+limit)
        bounds[k]=[*low.min(axis=0)-half-analyzer.tol,*high.max(axis=0)+half+analyzer.tol]
    for j,(owners,ids) in enumerate(groups.items()):
        sub=subset(plane,ids,{w for i in ids for w in gates[i].pair})
        demand=np.array([sum(max(analyzer.width,v) for v in g.widths)+(len(g.widths)+1)*analyzer.space for g in sub['gates']])
        b=bounds[ids];possible=risk[ids]
        for ends,gaps in sweep_sections(analyzer,sub,grid,owners,lambda f:progress((j+f)/len(groups),'检查截面可达范围')):
            b[:,:2]=np.minimum(b[:,:2],ends.min(axis=(0,2))-half-analyzer.tol)
            b[:,2:]=np.maximum(b[:,2:],ends.max(axis=(0,2))+half+analyzer.tol)
            possible |= np.any(gaps<=demand+analyzer.tol,axis=0)
        bounds[ids]=b;risk[ids]=possible
    hits=tree.query(shapely.box(*bounds.T))
    wallsets=[set(g.pair) for g in gates]
    for k,w in hits.T:wallsets[k].add(int(w))
    return risk,wallsets,bounds


def _target_groups(analyzer,plane,grid,progress):
    gates,walls=plane['gates'],plane['walls']
    if not gates:return
    possible,wallsets,bounds=_sweeps(analyzer,plane,grid,progress)
    groups=defaultdict(list)
    for i,g in enumerate(gates):groups[(g.pair,g.strand_refs)].append(i)
    refs=[frozenset(v for lane in g.strand_refs for v in lane) for g in gates]
    for (pair,_),ids in groups.items():
        if not possible[ids].any():continue
        wanted=refs[ids[0]];partners=set().union(*(wallsets[k] for k in ids))-set(pair)
        # Split evidence can invalidate a probability certificate without
        # changing the physical number of required or available lanes.
        # Refuse certification where a swept parent can lock these strands.
        bb=bounds[ids];lo=bb[:,:2].min(axis=0);hi=bb[:,2:].max(axis=0);pad=1.3*(analyzer.width+analyzer.space)+analyzer.tol
        ambiguous=any(pair[0] in potential and pair[1] in potential and wanted<=refs[k]
                      and np.all(bounds[k,:2]<=hi+pad) and np.all(bounds[k,2:]>=lo-pad)
                      and any(walls[w].contacts & refs[k] for w in potential-set(gates[k].pair))
                      for k,potential in enumerate(wallsets) if k not in ids)
        yield subset(plane,ids,set(pair)),partners,ambiguous


class OccupancyBank:
    """One spatial index for every digit of one independent obstruction owner."""
    def __init__(self,model,grid,owner,walls):
        self.count=grid.radices[owner] if owner>=0 else 1
        nominal=np.asarray([w.geometry for w in walls],object)
        xy=shapely.get_coordinates(nominal);shapes=[nominal]
        if owner>=0:
            from .preview import rigid_transform
            c=model.bound.centers[owner]
            shapes.extend(shapely.set_coordinates(nominal.copy(),rigid_transform(xy,c,z)) for z in grid.locals[owner][1:])
        self.tree=shapely.STRtree(np.concatenate(shapes));self.per_digit=len(walls)

    def masks(self,ends,half,tol,allowed):
        delta=ends[:,1]-ends[:,0];length=np.linalg.norm(delta,axis=1)
        direction=delta/np.maximum(length,tol)[:,None];side=direction[:,[1,0]]*[-half,half]
        rect=shapely.polygons(np.stack((ends[:,0]-side,ends[:,1]-side,ends[:,1]+side,ends[:,0]+side),axis=1))
        k,ids=self.tree.query(rect,predicate='intersects');digits=ids//self.per_digit
        keep=(length[k]>tol)&np.isin(digits,allowed)
        k,ids,digits=k[keep],ids[keep],digits[keep]
        masks=[0]*self.count
        if len(k):
            pieces,src=shapely.get_parts(shapely.intersection(rect[k],self.tree.geometries[ids]),return_index=True)
            xy,at=shapely.get_coordinates(pieces,return_index=True)
            along=np.einsum('ij,ij->i',xy-ends[k[src[at]],0],direction[k[src[at]]])
            lo=np.full(len(pieces),np.inf);hi=np.full(len(pieces),-np.inf)
            np.minimum.at(lo,at,along);np.maximum.at(hi,at,along)
            for j in np.unique(src[(hi>tol)&(lo<length[k[src]]-tol)]):
                masks[digits[j]] |= 1<<int(k[j])
        classes=defaultdict(list)
        for digit in allowed:classes[masks[digit]].append(digit)
        return classes


def _solve_throat(analyzer,name,plane,grid,target,partners,ambiguous,found,clauses,binary,progress):
    from .extreme_filter import PoseClause
    gates=target['gates'];walls=target['walls'];half=.65*(analyzer.width+analyzer.space)
    owners=tuple(sorted({w.owner for w in walls if w.owner>=0 and grid.radices[w.owner]>1}))
    template=analyzer._templates(np.asarray([w.geometry for w in walls],object),[w.owner for w in walls])
    other=defaultdict(list)
    for w in sorted(partners):other[plane['walls'][w].owner].append(plane['walls'][w])
    placements={};total=math.prod(grid.radices[i] for i in owners)
    demand=np.asarray([sum(max(analyzer.width,v) for v in g.widths)+(len(g.widths)+1)*analyzer.space for g in gates])
    for step,(digits,z) in enumerate(combinations(grid,owners)):
        if step%32==0:progress(step/total)
        nominal=not np.any(z)
        _,_,ends,gaps,inside,ref,order,_=analyzer.sections(target,z,analyzer._placed(template,z,nominal),nominal)
        now=np.where(inside & order,gaps,0.)
        if np.all(now>demand+analyzer.tol):continue
        base=[]
        for i,g in enumerate(gates):
            a,b=(walls[w] for w in g.pair);gap=gaps[i]
            axis=(ends[i,1]-ends[i,0])/gap if gap>analyzer.tol else ref[i]/np.linalg.norm(ref[i])
            refs=tuple(sorted({v for lane in g.strand_refs for v in lane}))
            r=analyzer._row(name,target,a,b,g.nominal_gap,now[i],ends[i].mean(axis=0),axis,g.widths,refs,0,False,min(a.owner,b.owner)<-1)
            r['nominal_witness_um']=g.ends.tolist()
            base.append(r)
        if all(r['status']==ADEQUATE for r in base):continue
        # For these fixed endpoints, a third owner matters only through the
        # set of stations that its rigid copper occupies. Digits with the same
        # mask are exactly equivalent for every line and for the filter.
        conditions=dict(zip(owners,[(d,) for d in digits]))
        fixed_mask=0;free=[]
        for o,objects in other.items():
            options=(conditions[o] if o in conditions else range(grid.radices[o]) if o>=0 else (0,))
            if o not in placements:placements[o]=OccupancyBank(analyzer.model,grid,o,objects)
            classes=placements[o].masks(ends,half,analyzer.tol,options)
            if len(classes)==1:
                fixed_mask |= next(iter(classes))
            else:free.append((o,classes))
        resolved={}
        for choices in product(*(groups.items() for o,groups in free)):
            mask=fixed_mask
            for bits,_ in choices:mask |= bits
            if mask not in resolved:
                direct=[(g,dict(r)) for i,(g,r) in enumerate(zip(gates,base)) if not mask>>i&1]
                rows=analyzer._distinct_throats(direct,half);red=False
                for r in rows:
                    if r['status']==ADEQUATE:continue
                    red |= r['status']==BLOCKED
                    event=None
                    if analyzer.probability and not ambiguous:
                        event=analyzer.probability.classify(r,[next(w for w in walls if w.name==key) for key in r['pair']])
                    strongest(found,r['refs'],r['status'],event,binary)
                resolved[mask]=red
            if resolved[mask]:
                allowed=dict(conditions)
                for (o,_),(_,ds) in zip(free,choices):allowed[o]=tuple(ds)
                clauses.add(PoseClause(tuple(sorted(allowed.items()))))
    return total


def summarize(analyzer,p,progress=lambda *_:None):
    grid=ExtremeCatalog(analyzer.model.case,p);binary=p.target_nsigma==0 or math.isinf(p.target_nsigma)
    found={};factors=[];nchecks=0;clauses=set()
    analyzer.frame({'states':np.zeros((grid.count,3))},lambda n,t:progress(round(n*.08),t))
    for j,(name,plane) in enumerate(analyzer.planes.items()):
        def update(f,text):progress(8+int(86*(j+f)/len(analyzer.planes)),text)
        groups=list(_target_groups(analyzer,plane,grid,lambda f,t:update(.6*f,t)))
        for k,(target,partners,ambiguous) in enumerate(groups):
            nchecks+=_solve_throat(analyzer,name,plane,grid,target,partners,ambiguous,found,clauses,binary,
                                   lambda f:update(.6+.4*(k+f)/len(groups),'核对独立姿态与第三方孔盘占位'))
    factors.extend(sorted(clauses,key=lambda c:c.allowed))
    channel_lines=found.copy();channel_factors=list(factors)
    # Board spacing has only one motion owner. A diagonal batch visits every
    # local pose without mixing independent DIE probabilities.
    model=analyzer.model
    edge_flags=[np.zeros(r,bool) for r in grid.radices];fixed=False
    for digit in range(max(grid.radices)):
        z=np.asarray([v[digit] if digit<len(v) else v[0] for v in grid.locals])
        for row in edge_violations(model,z,p,analyzer.settings):
            key=row['layer'];indices=row['indices']
            node=model.nodes[model.ids[key,indices[0]]];owner=node['owner']
            if owner>=0:edge_flags[owner][digit if digit<grid.radices[owner] else 0]=True
            else:fixed=True
            event=analyzer.probability.edge(key,indices[0],node['poly'],owner,row['required_um']) if analyzer.probability else None
            strongest(found,[(key,i) for i in indices],BLOCKED,event,binary)
        progress(94+round(6*(digit+1)/max(grid.radices)),'汇总线路与封装边距')
    factors.extend(((i,),flag) for i,flag in enumerate(edge_flags) if np.any(flag))
    if fixed:factors.append(((),True))
    return dict(lines=found,red_factors=factors,channel_lines=channel_lines,channel_factors=channel_factors,
                grid_key=tuple(v.tobytes() for v in grid.locals),package_states=grid.total+1,local_checks=nchecks,complete=True)
