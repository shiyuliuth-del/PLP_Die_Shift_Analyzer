"""Per-package width events on the source RDL, using the preview's physical gates."""
from collections import defaultdict,OrderedDict
from types import SimpleNamespace
import math
import numpy as np
import shapely
from .neighbor_channels import occupied
from .statistics import mean_and_bounds
from .width_analysis import ADEQUATE,BLOCKED,section_subset
from .width_summary import sweep_sections


def relevant_walls(plane,ids,travel,tol,width,space):
    """Conservative full-input envelope of every retained measurement rectangle."""
    walls=plane['walls'];bounds=shapely.bounds([w.geometry for w in walls])
    guard=tol+64*np.finfo(float).eps*(1+np.max(abs(bounds)))
    sweeps=bounds+travel[:,None]*np.array([-1,-1,1,1])+guard*np.array([-1,-1,1,1])
    features=shapely.bounds(plane['pieces']);queries=[];keep=set()
    for k in ids:
        g=plane['gates'][k];pair=np.asarray(g.pair);keep.update(g.pair)
        ab=features[list(g.features)].copy();delta=travel[pair,None]+guard
        ab[:,:2]-=delta;ab[:,2:]+=delta
        limit=g.nominal_gap+travel[pair].sum()+guard
        low=np.maximum(ab[:,:2],ab[::-1,:2]-limit)
        high=np.minimum(ab[:,2:],ab[::-1,2:]+limit)
        pad=.65*(width+space)+guard
        queries.append(shapely.box(*(low.min(axis=0)-pad),*(high.max(axis=0)+pad)))
    tree=shapely.STRtree(shapely.box(*sweeps.T))
    if queries:keep.update(map(int,tree.query(queries)[1]))
    return keep


class RiskPlane:
    def __init__(self,analyzer,plane,params):
        self.analyzer=analyzer
        walls=plane['walls']
        shapes=np.asarray([w.geometry for w in walls],object)
        owners=np.asarray([w.owner for w in walls],int)
        self.template=analyzer._templates(shapes,owners)
        self.walls=walls
        self.name=plane['keys'][0].split('/')[0]
        gates=plane['gates']
        self.local=None
        if not gates:return
        # The whole-support proof only removes entire source-mouth groups.
        # A third landing can suppress a parent section; its own adjacent pair
        # has a separate catalogue entry and is tested independently.
        count=len(analyzer.model.bound.centers)
        travel=np.zeros(len(walls));hulls=[];inside=[]
        for i,w in enumerate(walls):
            xy=shapely.get_coordinates(w.geometry.convex_hull)
            hulls.append(xy)
            if w.owner>=0:
                mu,b=mean_and_bounds(params.for_die(w.owner),count)
                radius=np.linalg.norm(xy-analyzer.model.bound.centers[w.owner],axis=1).max()
                travel[i]=np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*math.sin(min(math.pi,abs(mu[2])+b[2])/2)
            box=np.array(w.geometry.bounds)+travel[i]*np.array([-1,-1,1,1])
            inside.append(analyzer.inside.covers(shapely.box(*box) if travel[i] else w.geometry))
        convex=analyzer.board.equals(analyzer.board.convex_hull)
        zero=np.zeros((count,3))
        _,_,_,gap,within,_,order,_=analyzer._sections(plane,zero,shapes,False)
        risky=set()
        self.half=.65*(analyzer.width+analyzer.space)
        for k,g in enumerate(gates):
            a,b=g.pair
            need=analyzer.demand(plane,g)
            if travel[a]==travel[b]==0:
                safe=within[k] and order[k] and min(gap[k],g.nominal_gap)>need+analyzer.tol
            elif convex and inside[a] and inside[b] and not any(plane['local'][list(g.features)]):
                axis=(g.ends[1]-g.ends[0])/g.nominal_gap
                clearance=(hulls[b]@axis).min()-(hulls[a]@axis).max()
                relative=0. if owners[a]==owners[b] else travel[a]+travel[b]
                safe=clearance-relative>need+analyzer.tol
            else:safe=False
            if not safe:risky.add((g.pair,g.strand_refs))
        ids=[i for i,g in enumerate(gates) if (g.pair,g.strand_refs) in risky]
        if not ids:return
        kept=relevant_walls(plane,ids,travel,analyzer.tol,analyzer.width,analyzer.space)
        local_travel=travel[sorted(kept)]
        self.local=section_subset(plane,ids,kept)
        self.walls=self.local['walls'];walls=self.walls
        self.template=analyzer._templates(np.asarray([w.geometry for w in walls],object),[w.owner for w in walls])
        self.groups=defaultdict(list)
        self.need=[]
        for i,g in enumerate(self.local['gates']):
            self.groups[g.pair,g.strand_refs].append(i)
            self.need.append(analyzer.demand(self.local,g))
        self.need=np.array(self.need)
        self.group_walls={key:relevant_walls(self.local,indices,local_travel,analyzer.tol,analyzer.width,analyzer.space) for key,indices in self.groups.items()}
        self.contexts=OrderedDict()
        self.owners=tuple(sorted({w.owner for w in walls if w.owner>=0}))

    def events(self,states,red,yellow,site_masks=None):
        if self.local is None:return
        n,ndie,_=states.shape
        local=self.local;gates=local['gates'];tol=self.analyzer.tol
        banks=[np.vstack((np.zeros(3),states[:,k])) for k in range(ndie)]
        grid=SimpleNamespace(locals=banks,radices=[n+1]*ndie,count=ndie)
        offset=0
        for ends,gaps in sweep_sections(self.analyzer,local,grid,self.owners,lambda _:None,paired=True):
            for j in np.flatnonzero(np.any(gaps<=self.need+tol,axis=1)):
                pos=offset+j
                if not states[pos].any():
                    # The nominal preview retains tied nominal stations verbatim.
                    continue
                alert=np.flatnonzero(gaps[j]<=self.need+tol)
                groups=tuple(sorted({(gates[k].pair,gates[k].strand_refs) for k in alert}))
                context=self.contexts.get(groups)
                if context is None:
                    ids=[i for group in groups for i in self.groups[group]]
                    wall_ids=sorted(set().union(*(self.group_walls[group] for group in groups)))
                    mapping={value:i for i,value in enumerate(wall_ids)}
                    objects=[self.walls[i] for i in wall_ids]
                    pairs=np.array([[mapping[k] for k in gates[i].pair] for i in ids])
                    template=self.analyzer._templates(np.asarray([w.geometry for w in objects],object),[w.owner for w in objects])
                    context=(ids,pairs,objects,template);self.contexts[groups]=context
                    if len(self.contexts)>32:self.contexts.popitem(last=False)
                else:self.contexts.move_to_end(groups)
                ids,pairs,objects,template=context
                geo=self.analyzer._placed(template,states[pos],False)
                covered=occupied(ends[j,ids],pairs,objects,geo,self.half,tol)
                candidates=[]
                for station,i in enumerate(ids):
                    if station in covered:continue
                    g=gates[i];a,b=(self.walls[k] for k in g.pair)
                    refs=tuple(sorted({ref for strand in g.strand_refs for ref in strand}))
                    row=self.analyzer._row(self.name,local,a,b,g.nominal_gap,gaps[j,i],ends[j,i].mean(axis=0),
                                           np.zeros(2),g.widths,refs,0,False,False)
                    row['probability_key']=g.event_key
                    candidates.append((g,row))
                for row in self.analyzer._distinct_throats(candidates,self.half):
                    if row['status']!=ADEQUATE:
                        (red[pos] if row['status']==BLOCKED else yellow[pos]).update(row['refs'])
                        if site_masks is not None:
                            key=row['probability_key'];site_masks[key]=site_masks.get(key,0)|(1<<pos)
            offset+=len(gaps)


class WidthEvents:
    def __init__(self,analyzer,params,progress):
        self.analyzer=analyzer;self.params=params;self.model=analyzer.model
        n=len(self.model.bound.centers);zero=np.zeros((n,3))
        nominal=analyzer.frame({'states':zero},progress)
        self.nominal=[set(),set()];self.nominal_sites=set()
        for row in nominal['rows']:
            if row['status']!=ADEQUATE:
                self.nominal[row['status']!=BLOCKED].update(row['refs']);self.nominal_sites.add(row['probability_key'])
        self.planes=[RiskPlane(analyzer,p,params) for p in analyzer.planes.values()]
        # Build exact boundary candidates once; the displacement envelope includes
        # translated, rotated copper, external fan-out and off-centre pivots.
        from .width_keepout import forbidden
        self.keepout=forbidden(self.model);self.intrusions=[]
        self.edges=[];board=self.model.case['board'];self.boundary=board.boundary
        for node in self.model.nodes:
            key=node['key'];role=self.model.roles[key]
            if role not in ('运动RDL','固定RDL'):continue
            rule=(params.line_space_um if role=='固定RDL' else params.wafer_line_space_um)-analyzer.settings.drc_tolerance_um
            geo=node['poly'];o=node['owner'];travel=0.
            if o>=0:
                mu,b=mean_and_bounds(params.for_die(o),n)
                lever=np.linalg.norm(shapely.get_coordinates(geo)-self.model.bound.centers[o],axis=1).max()
                travel=np.linalg.norm(abs(mu[:2])+b[:2])+2*lever*math.sin(min(math.pi,abs(mu[2])+b[2])/2)
            if not board.covers(geo) or geo.distance(self.boundary)<=rule+travel+analyzer.tol:
                self.edges.append((key,node['index'],geo,o,rule))
            if not self.keepout.is_empty and geo.distance(self.keepout)<=travel+analyzer.tol:
                self.intrusions.append((key,node['index'],geo,o))

    def classify(self,states,with_sites=False):
        red=[set() for _ in states];yellow=[set() for _ in states];site_masks={} if with_sites else None
        for plane in self.planes:plane.events(states,red,yellow,site_masks)
        for i in np.flatnonzero(~np.any(states,axis=(1,2))):
            red[i].update(self.nominal[0]);yellow[i].update(self.nominal[1])
            if with_sites:
                for key in self.nominal_sites:site_masks[key]=site_masks.get(key,0)|(1<<int(i))
        tol=max(1e-9,self.model.data.dbu_um*1e-6)
        for key,index,geo,owner,rule in self.edges:
            placed=([self.model.moved(geo,owner,z) for z in states] if owner>=0 else [geo]*len(states))
            fail=~shapely.covers(self.model.case['board'],placed) | (shapely.distance(placed,self.boundary)<rule-tol)
            for i in np.flatnonzero(fail):red[i].add((key,index))
            if with_sites and fail.any():site_masks['edge',key,index]=sum(1<<int(i) for i in np.flatnonzero(fail))
        for key,index,geo,owner in self.intrusions:
            placed=([self.model.moved(geo,owner,z) for z in states] if owner>=0 else [geo]*len(states))
            fail=shapely.relate_pattern(placed,self.keepout,'T********')
            for i in np.flatnonzero(fail):red[i].add((key,index))
            if with_sites and fail.any():site_masks['keepout',key,index]=sum(1<<int(i) for i in np.flatnonzero(fail))
        return (red,yellow,site_masks) if with_sites else (red,yellow)
