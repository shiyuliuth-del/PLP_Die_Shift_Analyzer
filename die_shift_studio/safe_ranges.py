"""Certified continuous contact-free boxes within the hard input limits.

Optimization is multi-start coordinate expansion, not a proof of globally
maximum volume. Acceptance uses enclosing geometry for *every* state, never
an extrema/sample test. Budget exhaustion only rejects a candidate.
"""
import itertools
import math
import time
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from .preview import rigid_transform
from .statistics import mean_and_bounds
from .resources import parallel_map

class BoxCertificate:
    def __init__(self,model,owner,p,budget=512):
        self.center=np.asarray(model.bound.centers[owner]);self.radius=p.annulus_um+p.line_space_um
        self.tol=max(1e-9,model.data.dbu_um*1e-3)
        self.budget=budget;self.items=[];self.calls=0;self.boxes=0;self.unresolved=0;self.witnesses=[];self.memo={};self.arc_hulls={}
        mu,b=mean_and_bounds(p.for_die(owner),len(model.bound.centers));self.mean=mu
        for r in model.records:
            if r['owner']!=owner or not r['foreign']:continue
            shape=r['poly'];vertices=shapely.get_coordinates(shape);lever=float(np.linalg.norm(vertices-self.center,axis=1).max())
            reach=np.linalg.norm(b[:2])+2*lever*math.sin(min(math.pi,b[2])/2)
            mean=shapely.set_coordinates(shape,rigid_transform(vertices,self.center,mu))
            from .contact import nearby_foreign
            others=model.node_geometry[nearby_foreign(model,r,mean,self.radius+reach+self.tol)]
            if len(others):
                tree=STRtree(others);distance=mean.distance(others[tree.nearest(mean)])
                self.items.append((vertices,lever,others,tree,distance,shape))
        self.items.sort(key=lambda item:(item[4]-self.radius)/max(np.linalg.norm(b[:2])+item[1]*b[2],1e-12))
        self.nominal_gap=np.array([item[4] for item in self.items])
        self.levers=np.array([item[1] for item in self.items])

    def swept(self,vertices,lever,lo,hi):
        """Convex hull of sampled rotation arcs plus translation rectangle.

        Add a circumscribed disk whose radius bounds every arc/chord sagitta.
        Holes/concavities may be filled, so this is an outer bound only.
        """
        angle=float(hi[2]-lo[2])
        # A coarser envelope is still rigorously enclosed by its sagitta.
        # Refine only undecidable state cells instead of oversampling every arc.
        key=(vertices.tobytes(),lo[2],hi[2])
        stored=self.arc_hulls.get(key)
        if stored is None:
            segments=max(1,min(256,math.ceil(angle/math.radians(.25))))
            ts=np.linspace(lo[2],hi[2],segments+1);local=vertices-self.center
            co=np.cos(ts[:,None]);si=np.sin(ts[:,None])
            xy=np.stack((co*local[:,0]-si*local[:,1],si*local[:,0]+co*local[:,1]),axis=-1).reshape(-1,2)+self.center
            points=shapely.get_coordinates(shapely.convex_hull(shapely.multipoints(xy)))
            unit_sagitta=2*math.sin(min(2*math.pi,angle/segments)/4)**2
            if len(self.arc_hulls)>=256:self.arc_hulls.pop(next(iter(self.arc_hulls)))
            self.arc_hulls[key]=(points,unit_sagitta)
        else:points,unit_sagitta=stored
        shifts=np.array([[lo[0],lo[1]],[lo[0],hi[1]],[hi[0],lo[1]],[hi[0],hi[1]]])
        envelope=shapely.convex_hull(shapely.multipoints((points[:,None,:]+shifts).reshape(-1,2)))
        sagitta=lever*unit_sagitta
        return envelope.buffer((sagitta+self.tol)/math.cos(math.pi/32),quad_segs=8)

    def check(self,lo,hi):
        lo=np.asarray(lo,float);hi=np.asarray(hi,float);key=tuple(np.r_[lo,hi])
        if key in self.memo:return self.memo[key]
        self.calls+=1
        # A previously observed actual contact can reject, but never prove safe.
        if self.witnesses:
            known=np.asarray(self.witnesses)
            if np.any(np.all(known>=lo,axis=1)&np.all(known<=hi,axis=1)):return 'contact'
        tested=0
        # A rigid displacement from the input mean cannot close a larger
        # nominal margin. The exact subdivision below sees all remaining vias.
        reach=np.maximum(np.abs(lo-self.mean),np.abs(hi-self.mean))
        travel=np.linalg.norm(reach[:2])+2*self.levers*math.sin(min(math.pi,reach[2])/2)
        for index in np.flatnonzero(self.nominal_gap<=self.radius+travel+self.tol):
            vertices,lever,others,tree,_,shape=self.items[index]
            queue=[(lo,hi)]
            while queue:
                a,b=queue.pop();mid=(a+b)/2;half=(b-a)/2
                xy=rigid_transform(vertices,self.center,mid)
                via=shapely.set_coordinates(shape,xy) if shape.interiors else Polygon(xy)
                distance=(via.distance(others[0]) if len(others)==1 else tree.query_nearest(via,all_matches=False,return_distance=True)[1][0])
                tested+=1;self.boxes+=1
                if distance<=self.radius+self.tol:
                    self.witnesses.append(mid);self.memo[key]='contact';return 'contact'
                delta=np.linalg.norm(half[:2])+2*lever*math.sin(min(math.pi,half[2])/2)
                if distance>self.radius+delta+self.tol:continue
                if tested==1 or np.array_equal(a,lo) and np.array_equal(b,hi):
                    corners=np.array(list(itertools.product(*zip(a,b))))
                    local=vertices-self.center;co=np.cos(corners[:,2,None]);si=np.sin(corners[:,2,None])
                    xy=np.stack((co*local[:,0]-si*local[:,1],si*local[:,0]+co*local[:,1]),axis=-1)+self.center+corners[:,None,:2]
                    shapes=shapely.set_coordinates(np.full(len(corners),shape,object),xy.reshape(-1,2)) if shape.interiors else shapely.polygons(xy)
                    hits=tree.query(shapes,predicate='dwithin',distance=self.radius+self.tol)
                    if hits.size:
                        self.witnesses.append(corners[int(hits[0,0])]);self.memo[key]='contact';return 'contact'
                envelope=self.swept(vertices,lever,a,b)
                if not len(tree.query(envelope,predicate='dwithin',distance=self.radius+self.tol)):continue
                if tested>=self.budget or np.max(half*np.array([1.,1.,lever]))<=self.tol:
                    self.unresolved+=1;self.memo[key]='unresolved';return 'unresolved'
                # Search only unresolved pose boxes; exact midpoint contact above
                # is a witness, distance/envelope tests certify whole boxes.
                axis=int(np.argmax(half*np.array([1.,1.,lever])))
                lower=b.copy();lower[axis]=mid[axis];upper=a.copy();upper[axis]=mid[axis]
                queue.extend(((a,lower),(upper,b)))
        self.memo[key]='safe';return 'safe'


def solve_die(model,p,cfg,owner):
    start=time.perf_counter();mu,bounds=mean_and_bounds(p.for_die(owner),len(model.bound.centers))
    records=[r for r in model.records if r['owner']==owner]
    base=dict(die=owner+1,center=mu.tolist(),input_min=(mu-bounds).tolist(),input_max=(mu+bounds).tolist(),
              units=['μm','μm','rad'],interfaces=len(records),rotation_center_um=list(map(float,model.bound.centers[owner])))
    if not records:return dict(base,status='无可验证接口',certified=False,minimum=None,maximum=None)
    cert=BoxCertificate(model,owner,p,cfg.safe_box_budget)
    if cert.check(mu,mu)!='safe':return dict(base,status='均值位置已接触；不存在包含均值的安全盒',certified=False,minimum=None,maximum=None)
    precision=cfg.safe_box_precision;active=np.flatnonzero(bounds>0)
    def physical(v):return mu-bounds*v[0],mu+bounds*v[1]
    def safe(v):return cert.check(*physical(v))=='safe'
    full=np.ones((2,3));full[:,bounds==0]=0
    best=np.zeros((2,3));history=[]
    def volume(v):return float(np.prod((v[0,active]+v[1,active])/2)) if len(active) else 1.
    if safe(full):best=full
    else:
        # Symmetric starting box; monotonicity permits conservative bisection.
        lower=0.;upper=1.
        while upper-lower>precision:
            t=(lower+upper)/2
            if safe(full*t):lower=t
            else:upper=t
        seed=full*lower;best=seed.copy()
        axes=list(map(int,active));orders=list(itertools.permutations(axes)) or [()]
        starts=min(cfg.safe_box_starts,len(orders))
        # Cover distinct first axes before reverse orders.
        orders=sorted(orders,key=lambda q:(q[0] if q else 0,q))
        chosen=[orders[round(i*(len(orders)-1)/max(1,starts-1))] for i in range(starts)]
        seeds=[(seed,chosen[0])]+[(np.zeros((2,3)),order) for order in chosen]
        for initial,order in seeds:
            v=initial.copy()
            def expand(v,axis_order):
                v=v.copy()
                for axis in axis_order:
                    for side in (0,1):
                        low=v[side,axis];high=1.
                        candidate=v.copy();candidate[side,axis]=high
                        if safe(candidate):v=candidate;continue
                        while high-low>precision:
                            mid=(low+high)/2;candidate=v.copy();candidate[side,axis]=mid
                            if safe(candidate):low=mid
                            else:high=mid
                        v[side,axis]=low
                return v
            v=expand(v,order)
            for _ in range(cfg.safe_box_passes-1):
                improved=v
                # Free one axis to permit larger ranges on the other two.
                # Every trial contains the same mean and is certified again.
                for axis in order:
                    trial=v.copy();trial[:,axis]*=.8
                    trial=expand(trial,tuple(j for j in order if j!=axis)+(axis,))
                    if volume(trial)>volume(improved):improved=trial
                if volume(improved)<=volume(v)+1e-12:break
                v=improved
            history.append(volume(v))
            if volume(v)>volume(best):best=v.copy()
    lo,hi=physical(best)
    # Round inward for auditable displayed bounds, then certify those exact values.
    unit=np.array([1e-6,1e-6,math.pi/180*1e-8])
    lo=np.where(bounds>0,np.minimum(mu,np.ceil(lo/unit)*unit),mu)
    hi=np.where(bounds>0,np.maximum(mu,np.floor(hi/unit)*unit),mu)
    assert cert.check(lo,hi)=='safe','安全区最终证书丢失'
    return dict(base,status='已证安全内盒',certified=True,minimum=lo.tolist(),maximum=hi.tolist(),
                minimum_display=[float(lo[0]),float(lo[1]),math.degrees(lo[2])],maximum_display=[float(hi[0]),float(hi[1]),math.degrees(hi[2])],
                volume_fraction=volume(best),normalized_precision=precision,
                endpoint_precision=[*(bounds[:2]*precision),math.degrees(bounds[2]*precision)],
                at_full_input=bool(np.array_equal(best,full)),certificate_boxes=cert.boxes,
                candidate_checks=cert.calls,unresolved_candidates=cert.unresolved,
                seconds=time.perf_counter()-start,search_volume_fractions=history,
                optimization='包含输入均值；多起点逐端点扩张；已找到较大体积内盒，未证明全局最大')


def safe_ranges(model,p,settings=None,progress=lambda *_:None):
    from .settings import ViewSettings
    cfg=settings or ViewSettings();model.configure_parameters(p)
    rows=parallel_map(lambda i:solve_die(model,p,cfg,i),range(len(model.bound.centers)),
                      progress=lambda done,total:progress(96+round(3*done/max(1,total)),f'连续安全范围验证 DIE {done}/{total}'))
    uncertain=bool(model.connections.ownership_notes)
    if uncertain:
        for row in rows:
            row.update(certified=False,status='运动归属未完整识别，不能认证全体接口',minimum=None,maximum=None,minimum_display=None,maximum_display=None)
    return dict(rows=rows,scope='原始固定RDL；已识别运动过孔控制区与其相邻固定RDL其他网络；不包含预演型变、芯片机械碰撞或完整RDL DRC',
                guarantee='每颗DIE整个闭区间盒均通过连续包络分支验证；所有DIE可独立组合，因为被检查固定RDL不随状态变化；相切也拒绝',
                objective='包含各DIE输入均值，分别扩张X/Y/角度的正负端点，最大化找到的归一化盒体积',
                optimality='多起点坐标搜索的安全内解；预算未定候选拒绝，不声明全局最优；不使用概率阈值豁免接触')
