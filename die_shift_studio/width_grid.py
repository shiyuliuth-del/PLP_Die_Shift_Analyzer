"""Exhaust every node of a finite physical grid, factor shared DIE events exactly."""
from collections import defaultdict
from functools import lru_cache
from itertools import product
import math
import numpy as np
from .statistics import mean_and_bounds
from .model_yield import PhysicalMass
from .extreme_filter import ProblemIndex,PoseClause
from .width_keepout import routing_violations
from .width_summary import _target_groups,_solve_throat


class WidthGrid:
    def __init__(self,params,count,nodes):
        self.count=count;self.locals=[];self.weights=[];self.mass_error=0.;self.nodes=nodes
        self.steps=[]
        for owner in range(count):
            p=params.for_die(owner);mu,b=mean_and_bounds(p,count);law=PhysicalMass(p,count)
            axes=[np.linspace(m-d,m+d,nodes) if d else np.array([0.]) for m,d in zip(mu,b)]
            edges=[np.r_[a[0],(a[:-1]+a[1:])/2,a[-1]] if len(a)>1 else np.array([0.,0.]) for a in axes]
            poses=[np.zeros(3)];weights=[0.];locations={tuple(poses[0]):0};error=0.
            for digits in product(*(range(len(a)) for a in axes)):
                z=np.array([a[d] for a,d in zip(axes,digits)])
                lo=np.array([a[d] for a,d in zip(edges,digits)])
                hi=np.array([a[d+1] for a,d in zip(edges,digits)])
                mass,err=law(lo,hi);error+=err
                if mass==0:continue
                key=tuple(z)
                if key not in locations:locations[key]=len(poses);poses.append(z);weights.append(0.)
                weights[locations[key]]+=mass
            weights=np.array(weights);normal=weights.sum()
            if normal<=0:raise ArithmeticError('网格的概率质量为0')
            self.locals.append(np.array(poses));self.weights.append(weights/normal)
            self.mass_error+=error+abs(1-normal);self.steps.append([float(2*d/(nodes-1)) for d in b])
        self.radices=list(map(len,self.locals));self.total=math.prod(self.radices)-1
        self.combinations=math.prod(int(np.count_nonzero(w)) for w in self.weights)

    def index(self,digits):
        number=0
        for digit,radix in zip(digits,self.radices):number=number*radix+int(digit)
        return number-1

    def choices(self,index):
        number=index+1;out=[0]*self.count
        for i in reversed(range(self.count)):number,out[i]=divmod(number,self.radices[i])
        return out


def weighted_pair(index,weights):
    """Accumulate success and failure separately, retaining sub-epsilon tails."""
    @lru_cache(None)
    def at(node):
        if node<2:return (float(node==0),float(node==1))
        axis,children=index.nodes[node]
        q=[at(child) for child in children];w=weights[axis]
        return math.fsum(v[0]*p for v,p in zip(q,w)),math.fsum(v[1]*p for v,p in zip(q,w))
    return at(index.root)


def benefit_mass(all_index,ignored_index,weights):
    @lru_cache(None)
    def at(a,b):
        if a==0 or b==1:return 0.
        if a==1 and b==0:return 1.
        ka=all_index.nodes[a][0] if a>=2 else len(weights)
        kb=ignored_index.nodes[b][0] if b>=2 else len(weights);axis=min(ka,kb)
        ca=all_index.nodes[a][1] if ka==axis else [a]*len(weights[axis])
        cb=ignored_index.nodes[b][1] if kb==axis else [b]*len(weights[axis])
        return math.fsum(p*at(x,y) for p,x,y in zip(weights[axis],ca,cb))
    return at(all_index.root,ignored_index.root)


def measure_grid(analyzer,params,cfg,progress):
    grid=WidthGrid(params,len(analyzer.model.bound.centers),cfg.width_grid_nodes)
    progress(2,f'全情况网格：{grid.combinations:,} 个组合，按实际依赖分解')
    analyzer.probability=None
    analyzer.frame({'states':np.zeros((grid.count,3))},lambda n,t:progress(2+round(n*.06),t))
    records=defaultdict(set);sites=defaultdict(set);checks=0
    def collect(clause,refs,keys=()):
        for ref in refs:records[ref].add(clause)
        for key in keys:sites[key].add(clause)
    for j,(name,plane) in enumerate(analyzer.planes.items()):
        def update(f,text):progress(8+int(84*(j+f)/len(analyzer.planes)),text)
        groups=list(_target_groups(analyzer,plane,grid,lambda f,t:update(.5*f,t)))
        for k,(target,partners,ambiguous) in enumerate(groups):
            checks+=_solve_throat(analyzer,name,plane,grid,target,partners,ambiguous,{},None,set(),True,
                         lambda f:update(.5+.5*(k+f)/max(1,len(groups)),'穷举相邻壁与第三方占位'),collect=collect)
    # Boundary and keepout events have only one moving owner.
    for digit in range(max(grid.radices)):
        z=np.asarray([a[digit] if digit<len(a) else a[0] for a in grid.locals])
        for row in routing_violations(analyzer.model,z,params,cfg):
            key=row['layer'];owner=analyzer.model.nodes[analyzer.model.ids[key,row['indices'][0]]]['owner']
            ds=digit if owner>=0 and digit<grid.radices[owner] else 0
            clause=PoseClause(((owner,(ds,)),) if owner>=0 else ())
            from .width_event_ids import edge_keys
            collect(clause,[(key,i) for i in row['indices']],edge_keys(row))
        progress(92+round(6*(digit+1)/max(grid.radices)),'穷举封装边距与禁布区')
    clauses=sorted(set().union(*records.values()) if records else set(),key=lambda c:c.allowed)
    # Store an immutable numerical decision graph. Repeated ignore changes
    # only combine event clauses and never solve the grid geometry again.
    from types import SimpleNamespace
    index=ProblemIndex(grid,clauses);all_index=SimpleNamespace(nodes=index.nodes,root=index.root)
    progress(100,'全情况网格完成；连续空间精度由网格分辨率决定')
    return dict(method='grid',total=grid.combinations,grid=grid,all_index=all_index,
                by_line={r:tuple(sorted(v,key=lambda c:c.allowed)) for r,v in records.items()},
                by_site={r:tuple(sorted(v,key=lambda c:c.allowed)) for r,v in sites.items()},
                scale_nsigma=params.scale_nsigma,local_checks=checks,
                grid_nodes=cfg.width_grid_nodes,mass_error=grid.mass_error,steps=grid.steps)


def grid_yields(bank,ignored):
    grid=bank['grid'];clauses={c for r in ignored for c in bank['by_line'].get(r,())}
    index=ProblemIndex(grid,sorted(clauses,key=lambda c:c.allowed))
    a,af=weighted_pair(index,grid.weights);b,bf=weighted_pair(bank['all_index'],grid.weights)
    gain=benefit_mass(bank['all_index'],index,grid.weights)
    return dict(values=np.array([a,b]),failure_probability=np.array([af,bf]),
                proven=[not ignored or grid.total==0,grid.total==0],intervals=None,failure_intervals=None,
                samples=bank['total'],total=bank['total'],method='grid',gain_probability=gain,
                equality_proven=not gain,grid_nodes=bank['grid_nodes'],mass_error=bank['mass_error'],
                local_checks=bank['local_checks'],steps=bank['steps'])
