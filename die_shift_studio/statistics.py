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
    return mu,bounds

def statistical_state(p,count):
    mu,bounds=mean_and_bounds(p,count)
    if math.isinf(p.target_nsigma):mu=np.zeros(3)
    sx,sy,st=bounds/p.scale_nsigma
    cov=np.array([[sx*sx,p.rho_xy*sx*sy,0],[p.rho_xy*sx*sy,sy*sy,0],[0,0,st*st]])
    from .distribution import normalizer
    k=math.inf if p.target_nsigma==0 else 0. if math.isinf(p.target_nsigma) else math.sqrt(chi2.isf(2*norm.sf(p.target_nsigma)*normalizer(p,count)[0]/count,3))
    return mu,cov,k

class ExtremeCatalog:
    """Full lazy product of per-die mean and 26 signed input-boundary states.

    Counts use arbitrary-precision integers. The nominal all-zero state is
    represented by the GUI blank row and excluded from numbered combinations.
    """
    def __init__(self,case,p):
        self.count=len(case['dies']);self.k=1.;self.locals=[];self.labels=[]
        for owner in range(self.count):
            mu,bounds=mean_and_bounds(p.for_die(owner),self.count);local=[mu];labels=['均值']
            for signs in itertools.product((-1,0,1),repeat=3):
                if not any(signs):continue
                z=mu+bounds*np.asarray(signs)
                if any(np.allclose(z,v,atol=1e-11,rtol=1e-12) for v in local):continue
                local.append(z);labels.append(self._label(signs))
            self.locals.append(np.array(local));self.labels.append(labels)
        self.local_states=self.locals[0];self.local_labels=self.labels[0];self.radix=len(self.local_states)
        self.radices=[len(v) for v in self.locals]
        self.skip_nominal=int(all(np.allclose(v[0],0,atol=1e-12,rtol=0) for v in self.locals))
        self.total=math.prod(self.radices)-self.skip_nominal
    @staticmethod
    def _label(signs):
        return ' '.join(name+('+' if sign>0 else '−') for name,sign in zip(('X','Y','θ'),signs) if sign) or '均值'
    def __getitem__(self,index):
        if not isinstance(index,int) or not 0<=index<self.total:raise IndexError(index)
        digits=[0]*self.count;n=index+self.skip_nominal
        for die in range(self.count-1,-1,-1):n,digits[die]=divmod(n,self.radices[die])
        states=np.array([v[d] for v,d in zip(self.locals,digits)])
        descriptions=[f'DIE {i+1}: {self.labels[i][digit]}' for i,digit in enumerate(digits)]
        return dict(name=f'组合 {index+1}',states=states,choices=digits,detail='；'.join(descriptions),
                    kind='输入幅度边界全组合',k=self.k)
    def page(self,page,size=64):
        if page<0:raise ValueError('页码不可为负')
        start=page*size
        return [(i,self[i]) for i in range(start,min(start+size,self.total))]
    def __len__(self):
        # Python's len is limited to Py_ssize_t; large catalogues use .total.
        return self.total

PREVIEW_SCOPE=('每颗芯片含均值及26个输入幅度一/二/三参数组合，对所有芯片取完整笛卡尔积，按页生成，无组合截断。'
               '预演不受目标σ放大；窗口判定另覆盖连续置信域；有限预演不等同穷尽连续分布。'
               '单次几何问题的0/100%表示是否违规，不是随机事件发生概率。')
