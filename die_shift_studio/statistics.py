"""Shared normal-confidence model and lazy multi-die boundary combinations, in μm and radians."""
import itertools
import math
import numpy as np
from scipy.stats import chi2,norm

def mean_and_bounds(p,count):
    mu=np.array([p.mean_x_um,p.mean_y_um,math.radians(p.mean_angle_deg)])
    bounds=np.array([p.single_bound_x_um if count==1 else p.multi_bound_x_um,
                     p.single_bound_y_um if count==1 else p.multi_bound_y_um,
                     math.radians(p.bound_angle_deg)])
    return np.where(bounds>0,mu,0.),bounds

def statistical_state(p,count):
    mu,bounds=mean_and_bounds(p,count)
    if math.isinf(p.target_nsigma):mu=np.zeros(3)
    sx,sy,st=bounds/p.scale_nsigma
    cov=np.array([[sx*sx,p.rho_xy*sx*sy,0],[p.rho_xy*sx*sy,sy*sy,0],[0,0,st*st]])
    from .distribution import normalizer
    k=math.inf if p.target_nsigma==0 else 0. if math.isinf(p.target_nsigma) else math.sqrt(chi2.isf(2*norm.sf(p.target_nsigma)*normalizer(p,count)[0]/count,3))
    return mu,cov,k

class ExtremeCatalog:
    """All discrete poses, indexed by mixed radix without allocating the product.

    Local digit 0 is always physical zero. Each active axis adds its distinct
    lower and upper endpoints; a zero-amplitude axis is locked at zero.
    """
    def __init__(self,case,p):
        self.count=len(case['dies']);self.k=1.;self.locals=[];self.labels=[];self.sign_digits=[]
        for owner in range(self.count):
            mu,b=mean_and_bounds(p.for_die(owner),self.count)
            values=[{0:0.,-1:float(mu[j]-b[j]) if b[j] else 0.,1:float(mu[j]+b[j]) if b[j] else 0.} for j in range(3)]
            digits={};lookup={};local=[];labels=[]
            for signs in itertools.product((0,-1,1),repeat=3):
                pose=tuple(axis[sign] for axis,sign in zip(values,signs))
                if pose not in digits:
                    digits[pose]=len(local);local.append(pose)
                    labels.append(self._label(tuple(sign if x!=0 else 0 for sign,x in zip(signs,pose))))
                lookup[signs]=digits[pose]
            self.locals.append(np.asarray(local));self.labels.append(labels);self.sign_digits.append(lookup)
        self.radices=[len(v) for v in self.locals]
        self.local_states=self.locals[0];self.local_labels=self.labels[0];self.radix=self.radices[0]
        self.total=math.prod(self.radices)-1

    @staticmethod
    def _label(signs):
        return ' '.join(name+('+' if sign>0 else '−') for name,sign in zip(('X','Y','θ'),signs) if sign) or '不动'

    def index(self,digits):
        n=0
        for radix,d in zip(self.radices,digits):n=n*radix+d
        return n-1

    def choices(self,index):
        if not isinstance(index,int) or not 0<=index<self.total:raise IndexError(index)
        n=index+1;digits=[0]*self.count
        for die in range(self.count-1,-1,-1):n,digits[die]=divmod(n,self.radices[die])
        return digits

    def __getitem__(self,index):
        digits=self.choices(index)
        states=np.array([v[d] for v,d in zip(self.locals,digits)])
        labels=[v[d] for v,d in zip(self.labels,digits)]
        detail='；'.join(f'DIE {i+1}: {labels[i]}' for i in range(self.count) if self.radices[i]>1)
        return dict(name=(labels[0] if self.count==1 else f'组合 {index+1}'),states=states,choices=digits,
                    per_die_labels=labels,detail=detail,kind='离散极端组合',k=self.k)

    def page(self,page,size=64):
        if page<0 or size<1:raise ValueError('页码或每页行数无效')
        start=page*size
        return [(i,self[i]) for i in range(start,min(start+size,self.total))]

    def __len__(self):return self.total
    def __bool__(self):return self.total!=0


PREVIEW_SCOPE=('活动轴取物理零位及均值±输入幅度，零幅度轴锁在零位。空白首行为全包不动，其余组合按页展开。'
               '原图单芯片规则与可能接近的芯片对分别校验，合成各行的离散判定。连续接触概率与良率独立积分。')
