"""Whole-input no-contact probability; shared poses within each DIE.

The integration partitions the actual hard input box, not the safe inner box.
A box is safe only when ALL interfaces are safe throughout it, and contacting
only when at least ONE interface is contacting throughout it. Unresolved mass
is retained as an interval. Different DIE random states are assumed independent.
"""
import heapq
import math
from functools import lru_cache
import numpy as np
import shapely
from scipy.integrate import quad
from .statistics import mean_and_bounds,statistical_state
from .distribution import normal_interval,normalizer
from .resources import parallel_map


class PhysicalMass:
    def __init__(self,p,count):
        self.mu,self.b=mean_and_bounds(p,count);self.sig=self.b/p.scale_nsigma;self.rho=p.rho_xy
        self.z,self.zerr=normalizer(p,count)

    @lru_cache(maxsize=8192)
    def xy(self,lx,hx,ly,hy):
        sx,sy=self.sig[:2]
        if sx==0 or sy==0:
            mass=1.
            for lo,hi,s in ((lx,hx,sx),(ly,hy,sy)):
                mass*=float(normal_interval(lo/s,hi/s)) if s else float(lo<=0<=hi)
            return mass,2e-15
        a,b,c,d=lx/sx,hx/sx,ly/sy,hy/sy
        if self.rho==0:return float(normal_interval(a,b)*normal_interval(c,d)),2e-15
        if abs(self.rho)==1:
            if self.rho<0:c,d=-d,-c
            return float(normal_interval(max(a,c),min(b,d))),2e-15
        s=math.sqrt(1-self.rho*self.rho)
        def f(x):return math.exp(-x*x/2)/math.sqrt(2*math.pi)*float(normal_interval((c-self.rho*x)/s,(d-self.rho*x)/s))
        value,error=quad(f,a,b,epsabs=2e-13,epsrel=2e-12,limit=100)
        return value,error+2e-15

    def __call__(self,lo,hi):
        a=lo-self.mu;b=hi-self.mu
        xy,error=self.xy(a[0],b[0],a[1],b[1]);s=self.sig[2]
        angle=float(normal_interval(a[2]/s,b[2]/s)) if s else float(a[2]<=0<=b[2])
        raw=xy*angle;err=error*angle+2e-15
        value=raw/self.z
        lower=max(0.,(raw-err)/(self.z+self.zerr))
        return value,max(0.,value-lower)


class DieEvent:
    def __init__(self,model,p,owner,backend='auto'):
        self.backend=backend
        self.center=np.asarray(model.bound.centers[owner]);self.radius=p.annulus_um+p.line_space_um
        self.tol=max(1e-9,model.data.dbu_um*1e-6);self.polys=[];self.obstacles=[];self.levers=[];self.projections=[]
        count=len(model.bound.centers);mu,cov,_=statistical_state(p,count);_,b=mean_and_bounds(p,count)
        self.mean_angle=mu[2]
        if math.isinf(p.target_nsigma):b=np.zeros(3)
        for r in model.records:
            if r['owner']!=owner or not r['foreign']:continue
            vertices=shapely.get_coordinates(r['poly']);lever=float(np.linalg.norm(vertices-self.center,axis=1).max())
            via=self.move(np.array([r['poly']],object),mu)[0]
            reach=np.linalg.norm(b[:2])+2*lever*math.sin(min(math.pi,b[2])/2)
            from .contact import nearby_foreign
            others=model.node_geometry[nearby_foreign(model,r,via,self.radius+reach+self.tol)]
            if len(others):
                # Only this local part can touch anywhere in the full input box.
                # Huge meandering foreign networks otherwise dominate every
                # distance query despite their remote vertices being irrelevant.
                x0,y0,x1,y1=via.bounds;pad=self.radius+reach+2*self.tol
                domain=shapely.box(x0-pad,y0-pad,x1+pad,y1+pad)
                local=shapely.union_all(shapely.intersection(others,domain))
                if not local.is_empty:
                    self.polys.append(r['poly']);self.obstacles.append(local);self.levers.append(lever)
                    ends=shapely.get_coordinates(shapely.shortest_line(via,local))
                    axis=ends[-1]-ends[0];length=np.linalg.norm(axis)
                    projection=None
                    if length>self.tol:
                        axis/=length
                        center=np.asarray(r['poly'].centroid.coords[0])
                        offset=center-self.center
                        co,si=math.cos(mu[2]),math.sin(mu[2])
                        n0=np.array([co*axis[0]+si*axis[1],-si*axis[0]+co*axis[1]])
                        support=float(np.max((vertices-center)@n0))
                        extent=float(np.linalg.norm(vertices-center,axis=1).max())
                        limit=float((shapely.get_coordinates(local)@axis).min()-self.radius-2*self.tol)
                        projection=(axis,offset,support,extent,limit)
                    self.projections.append(projection)
        self.polys=np.array(self.polys,object);self.obstacles=np.array(self.obstacles,object);self.levers=np.array(self.levers)

    def move(self,polys,state):
        co,si=math.cos(state[2]),math.sin(state[2]);rot=np.array([[co,si],[-si,co]])
        return shapely.transform(polys,lambda xy:(xy-self.center)@rot+self.center+state[:2])

    def classify(self,lo,hi):
        if not len(self.polys):return 1,np.zeros(3)
        half=(hi-lo)/2;state=(lo+hi)/2
        distance=shapely.distance(self.move(self.polys,state),self.obstacles)
        delta=np.linalg.norm(half[:2])+2*self.levers*np.sin(min(math.pi,half[2])/2)
        if np.all(distance>self.radius+delta+self.tol):return 1,np.zeros(3)
        if np.any(distance+delta<=self.radius+self.tol):return -1,np.zeros(3)
        near=distance<=self.radius+delta+self.tol
        radius=float(self.levers[near].max(initial=0.))
        return 0,(hi-lo)*np.array([1.,1.,radius])

    def no_contact(self,states):
        """Exact full-polygon distances; early exit for already contacting poses."""
        safe=np.ones(len(states),bool)
        for poly,obstacle,projection in zip(self.polys,self.obstacles,self.projections):
            indices=np.flatnonzero(safe)
            if not len(indices):break
            pose=states[indices]
            if projection is not None:
                # One separating axis proves safety cheaply for a subset of
                # poses. Every other pose still runs the full polygon predicate.
                n,offset,support,extent,limit=projection
                theta=pose[:,2]
                cx=offset[0]*np.cos(theta)-offset[1]*np.sin(theta)+self.center[0]+pose[:,0]
                cy=offset[0]*np.sin(theta)+offset[1]*np.cos(theta)+self.center[1]+pose[:,1]
                extra=2*extent*np.abs(np.sin((theta-self.mean_angle)/2))
                keep=cx*n[0]+cy*n[1]+np.minimum(extent,support+extra)>=limit
                indices=indices[keep];pose=pose[keep]
                if not len(indices):continue
            from .acceleration import rigid_pose_batch
            xy=shapely.get_coordinates(poly)
            moved=shapely.set_coordinates(np.full(len(pose),poly,object),
                    rigid_pose_batch(xy,self.center,pose,self.backend).reshape(-1,2))
            safe[indices]=~shapely.dwithin(moved,obstacle,self.radius+self.tol)
        return safe


def sample_die(model,parameters,cfg,owner,progress=lambda *_:None):
    """Independent Monte Carlo with Bonferroni-adjusted binomial intervals.

    All interfaces on a DIE see the same pose. Independent seeds are used for
    separate DIEs. Zero sampled failures never produces a certified 100% bound.
    """
    from dataclasses import replace
    from scipy.stats import beta
    from .distribution import bounded_poses
    p=parameters.for_die(owner);count=len(model.bound.centers)
    if math.isinf(p.target_nsigma):return integrate_die(model,parameters,cfg,owner,progress)
    base=dict(die=owner+1,interfaces=sum(r['owner']==owner for r in model.records))
    if base['interfaces']==0:return dict(base,lower=0.,upper=1.,estimate=None,status='无可验证接口',samples=0,unknown=1.)
    event=DieEvent(model,p,owner,cfg.acceleration);mu,b=mean_and_bounds(p,count);kind,_=event.classify(mu-b,mu+b)
    if kind:
        value=float(kind==1)
        return dict(base,lower=value,upper=value,estimate=value,status='整个输入盒已判定',samples=0,unknown=0.)
    settings=replace(cfg,probability_method='monte_carlo',sample_exponent=cfg.yield_sample_exponent,random_seed=(cfg.random_seed+1009*owner)%(2**32))
    states=bounded_poses(p,count,settings);n=len(states);success=0
    for first in range(0,n,256):
        success+=int(event.no_contact(states[first:first+256]).sum());progress(min(n,first+256),n)
    alpha=.01/max(1,count)
    lower=float(beta.ppf(alpha/2,success,n-success+1)) if success else 0.
    upper=float(beta.ppf(1-alpha/2,success+1,n-success)) if success<n else 1.
    return dict(base,lower=lower,upper=upper,estimate=success/n,status='二项精确区间；整包联合置信度至少99%',samples=n,success=success,unknown=upper-lower)


def binary_die(model,p,owner,hit_owners=None):
    records=[r for r in model.records if r['owner']==owner]
    base=dict(die=owner+1,interfaces=len(records),boxes=0,samples=0)
    if not records:return dict(base,lower=0.,upper=1.,estimate=None,status='无可验证接口',unknown=1.)
    if hit_owners is None:
        limit=p.annulus_um+p.line_space_um+max(1e-9,model.data.dbu_um*1e-6)
        hit=any(np.any(shapely.dwithin(r['poly'],model.node_geometry[r['foreign']],limit)) for r in records if len(r['foreign']))
    else:hit=owner in hit_owners
    value=float(not hit)
    return dict(base,lower=value,upper=value,estimate=value,status='名义状态二值',unknown=0.)

def integrate_die(model,parameters,cfg,owner,progress=lambda *_:None):
    if math.isinf(parameters.target_nsigma):return binary_die(model,parameters,owner)
    p=parameters.for_die(owner);count=len(model.bound.centers)
    base=dict(die=owner+1,interfaces=sum(r['owner']==owner for r in model.records))
    if base['interfaces']==0:return dict(base,lower=0.,upper=1.,estimate=None,status='无可验证接口',boxes=0,unknown=1.)
    event=DieEvent(model,p,owner,cfg.acceleration)
    mass=PhysicalMass(p,count);lo=mass.mu-mass.b;hi=mass.mu+mass.b
    queue=[];safe=[];hit=[];boxes=0;serial=0
    def push(a,b):
        nonlocal boxes,serial
        weight,error=mass(a,b)
        if weight<=0:return
        kind,score=event.classify(a,b);boxes+=1
        certain=max(0.,weight-error)
        if kind==1:safe.append(certain)
        elif kind==-1:hit.append(certain)
        else:
            axis=int(np.argmax(score));serial+=1
            heapq.heappush(queue,(-weight,serial,a,b,axis))
    # A whole-support certificate avoids numerical CDF loss for exact 0/100%.
    kind,_=event.classify(lo,hi)
    if kind:
        value=float(kind==1)
        return dict(base,lower=value,upper=value,estimate=value,status='整个输入盒已判定',boxes=1,unknown=0.)
    push(lo,hi)
    while queue and boxes+2<=cfg.yield_box_budget:
        lower=math.fsum(safe);upper=1-math.fsum(hit)
        if upper-lower<=cfg.yield_tolerance:break
        for _ in range(min(16,len(queue),max(1,(cfg.yield_box_budget-boxes)//2))):
            _,_,a,b,axis=heapq.heappop(queue);mid=(a[axis]+b[axis])/2
            if mid in (a[axis],b[axis]):continue
            left=b.copy();left[axis]=mid;right=a.copy();right[axis]=mid
            push(a,left);push(right,b)
        progress(boxes,cfg.yield_box_budget)
    lower=max(0.,min(1.,math.fsum(safe)));upper=max(lower,min(1.,1-math.fsum(hit)))
    return dict(base,lower=lower,upper=upper,estimate=(lower+upper)/2,unknown=upper-lower,boxes=boxes,
                status='达到误差目标' if upper-lower<=cfg.yield_tolerance else '预算结束；保留未定概率')


def model_yield(model,p,settings,progress=lambda *_:None,contact=None):
    model.configure_parameters(p);count=len(model.bound.centers)
    done=np.zeros(count);last=[0.]
    import threading,time
    lock=threading.Lock()
    def update(owner,n,budget):
        with lock:
            done[owner]=n/max(1,budget);now=time.monotonic()
            if now-last[0]>.1:
                unit='状态盒' if settings.yield_method=='partition' else '样本'
                progress(97+round(2*float(done.mean())),f'全输入无接触概率积分：DIE {owner+1}，{unit} {n}/{budget}');last[0]=now
    integrate=integrate_die if settings.yield_method=='partition' else sample_die
    if math.isinf(p.target_nsigma):
        hits={issue['die']-1 for issue in contact['issues']} if contact is not None else None
        rows=[binary_die(model,p,i,hits) for i in range(count)]
    else:rows=parallel_map(lambda owner:integrate(model,p,settings,owner,lambda n,b:update(owner,n,b)),range(count))
    lower=math.prod(q['lower'] for q in rows);upper=math.prod(q['upper'] for q in rows)
    if model.connections.ownership_notes or not rows:lower=0.;upper=1.
    target_failure=p.failure_threshold
    unavailable=bool(model.connections.ownership_notes or not rows or any(q['interfaces']==0 for q in rows))
    estimate=None if unavailable else (lower+upper)/2 if settings.yield_method=='partition' else math.prod(q['estimate'] for q in rows)
    if unavailable:lower=0.;upper=1.
    binary=math.isinf(p.target_nsigma)
    return dict(lower=lower,upper=upper,estimate=estimate,uncertainty=upper-lower,rows=rows,
                target_failure=target_failure,target_yield=1-target_failure,target_sigma=p.target_nsigma,available=not unavailable,
                interval_kind='确定状态' if binary else '确定性积分区间' if settings.yield_method=='partition' else '99%联合置信区间',
                method='当前名义状态二值判定' if binary else '确定性全输入状态盒积分；区间中点估计' if settings.yield_method=='partition' else '全输入独立蒙特卡洛；Clopper–Pearson精确二项区间与Bonferroni联合保护',
                scope='原始固定RDL的所有已识别运动过孔接口；同DIE所有孔共享同一状态，各DIE随机状态相互独立。不含型变后的动态布线、其他制造缺陷或全套DRC。',
                definition='当前名义姿态下全部接口无异网接触取100%，否则取0%。' if binary else '积分整个输入范围内所有接口均无异网接触的概率，不是安全内盒概率，也不是各过孔良率独立相乘。')
