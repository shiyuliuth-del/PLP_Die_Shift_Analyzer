"""One bounded-normal model for statistical contact and via occupancy.

B is a hard half-range. B/k is the pre-truncation normal scale, not the
standard deviation after conditioning on |Z_j-mu_j| <= B_j.
"""
from functools import lru_cache
import math
import numpy as np
from scipy.special import ndtr,ndtri
from scipy.integrate import quad
from scipy.stats import qmc
from .statistics import mean_and_bounds


def normal_interval(lo,hi):
    lo=np.asarray(lo);hi=np.asarray(hi)
    return np.maximum(0,np.where(lo>=0,ndtr(-lo)-ndtr(-hi),ndtr(hi)-ndtr(lo)))


@lru_cache(maxsize=128)
def normalization(k,rho,has_x=True,has_y=True,has_angle=True):
    if math.isinf(k):return 1.,0.
    marginal=float(normal_interval(-k,k));error=0.
    if has_x and has_y:
        if abs(rho)==1:xy=marginal
        elif rho==0:xy=marginal*marginal
        else:
            scale=math.sqrt(1-rho*rho)
            def f(z):return math.exp(-z*z/2)/math.sqrt(2*math.pi)*float(normal_interval((-k-rho*z)/scale,(k-rho*z)/scale))
            xy,error=quad(f,-k,k,epsabs=1e-13,epsrel=1e-12,points=[-abs(rho)*k,0,abs(rho)*k],limit=100)
    else:xy=marginal if has_x or has_y else 1.
    angle=marginal if has_angle else 1.
    return float(xy*angle),float(error*angle+2e-14)


def normalizer(p,count):
    _,bounds=mean_and_bounds(p,count)
    return normalization(p.scale_nsigma,p.rho_xy,*(bool(b) for b in bounds))


def bounded_poses(p,count,settings):
    mu,b=mean_and_bounds(p,count);n=2**settings.sample_exponent
    if math.isinf(p.target_nsigma):return np.tile(np.zeros(3),(n,1))
    sig=b/p.scale_nsigma;rho=p.rho_xy
    root=np.array([[sig[0],0,0],[rho*sig[1],sig[1]*math.sqrt(max(0,1-rho*rho)),0],[0,0,sig[2]]])
    sobol=qmc.Sobol(3,scramble=True,seed=settings.random_seed) if settings.probability_method=='sobol' else None
    rng=np.random.default_rng(settings.random_seed);chunks=[];total=0
    # Rejection conditions the correlated joint law, rather than independently
    # clipping X/Y (which would destroy the user's correlation).
    while total<n:
        normal=ndtri(np.clip(sobol.random(n),np.finfo(float).eps,1-np.finfo(float).eps)) if sobol else rng.standard_normal((n,3))
        delta=normal@root.T;valid=np.all(abs(delta)<=b+1e-12,axis=1);chunk=mu+delta[valid]
        chunks.append(chunk);total+=len(chunk)
    return np.concatenate(chunks)[:n]
