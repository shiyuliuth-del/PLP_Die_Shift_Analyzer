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
from .statistics import mean_and_bounds,std_devs


def normal_interval(lo,hi):
    lo=np.asarray(lo);hi=np.asarray(hi)
    return np.maximum(0,np.where(lo>=0,ndtr(-lo)-ndtr(-hi),ndtr(hi)-ndtr(lo)))


@lru_cache(maxsize=256)
def normalization_axes(kx,ky,kt,rho,has_x=True,has_y=True,has_angle=True):
    kx=float(kx);ky=float(ky);kt=float(kt)
    error=0.
    if has_x and has_y:
        if abs(rho)==1:
            k_eff=min(kx,ky)
            xy=1. if math.isinf(k_eff) else float(normal_interval(-k_eff,k_eff))
        elif rho==0:
            mx=1. if math.isinf(kx) else float(normal_interval(-kx,kx))
            my=1. if math.isinf(ky) else float(normal_interval(-ky,ky))
            xy=mx*my
        else:
            if math.isinf(kx) and math.isinf(ky):
                xy=1.
            else:
                # If kx is infinite and ky is finite, swap roles so the finite bound is the outer integral.
                a_k,b_k=(ky,kx) if math.isinf(kx) else (kx,ky)
                scale=math.sqrt(1-rho*rho)
                if math.isinf(b_k):
                    xy=float(normal_interval(-a_k,a_k))
                else:
                    pts=sorted({p for p in (-b_k/abs(rho),-abs(rho)*b_k,0.,abs(rho)*b_k,b_k/abs(rho)) if -a_k<p<a_k})
                    def f(z):return math.exp(-z*z/2)/math.sqrt(2*math.pi)*float(normal_interval((-b_k-rho*z)/scale,(b_k-rho*z)/scale))
                    xy,error=quad(f,-a_k,a_k,epsabs=1e-13,epsrel=1e-12,points=pts or None,limit=100)
    else:
        if has_x:xy=1. if math.isinf(kx) else float(normal_interval(-kx,kx))
        elif has_y:xy=1. if math.isinf(ky) else float(normal_interval(-ky,ky))
        else:xy=1.
    angle=(1. if math.isinf(kt) else float(normal_interval(-kt,kt))) if has_angle else 1.
    return float(xy*angle),float(error*angle+2e-14)


def normalization(k,rho,has_x=True,has_y=True,has_angle=True):
    return normalization_axes(k,k,k,rho,has_x,has_y,has_angle)


def normalizer(p,count):
    _,bounds=mean_and_bounds(p,count)
    sig=np.where(bounds>0,std_devs(p),0.)
    kx=float(bounds[0]/sig[0]) if bounds[0]>0 and sig[0]>0 else math.inf
    ky=float(bounds[1]/sig[1]) if bounds[1]>0 and sig[1]>0 else math.inf
    kt=float(bounds[2]/sig[2]) if bounds[2]>0 and sig[2]>0 else math.inf
    return normalization_axes(kx,ky,kt,p.rho_xy,bool(bounds[0] and sig[0]),bool(bounds[1] and sig[1]),bool(bounds[2] and sig[2]))


def bounded_poses(p,count,settings):
    mu,b=mean_and_bounds(p,count);n=2**settings.sample_exponent
    if math.isinf(p.target_nsigma) or not b.any():return np.zeros((n,3))
    sig=np.where(b>0,std_devs(p),0.);rho=p.rho_xy
    if not sig.any():return np.tile(mu,(n,1))
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
