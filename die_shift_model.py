"""DIE-First shift model; all distances in um and all internal angles in radians.

Independent mathematical reference, not a router/DRC or a production-yield model.
Run: python3 die_shift_model.py --input-nsigma 6 --target-nsigma 6
Dependencies: numpy, scipy, matplotlib. Outputs are explicitly modeled examples.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.stats import chi2, ncx2, norm

ROOT = Path(__file__).resolve().parent


def positive(value, name):
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def rotation(theta):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s], [s, c]])


def displacement(points, states):
    """Exact rigid displacement. states: (samples,3), points: (points,2).

    Every point uses the SAME die state in a sample, preserving correlation.
    """
    points, states = np.asarray(points, float), np.asarray(states, float)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("points must have shape (n,2)")
    if states.ndim != 2 or states.shape[1] != 3:
        raise ValueError("states must have shape (samples,3)")
    c, s = np.cos(states[:, 2:3]), np.sin(states[:, 2:3])
    ax, ay = points[:, 0][None, :], points[:, 1][None, :]
    return np.stack((states[:, 0:1] + (c-1)*ax-s*ay,
                     states[:, 1:2] + s*ax+(c-1)*ay), axis=-1)


def linear_moments(point, mean, covariance):
    """First-order propagation of a full (DX,DY,theta) covariance."""
    a, mu, cov = np.asarray(point), np.asarray(mean), np.asarray(covariance)
    b = rotation(mu[2]) @ a
    jac = np.array([[1., 0., -b[1]], [0., 1., b[0]]])
    return mu[:2] + b-a, jac @ cov @ jac.T


def exact_moments_independent(point, mean_d, cov_d, mean_theta, sigma_theta):
    """Exact moments when D and theta are independent (DX/DY may correlate)."""
    if sigma_theta < 0:
        raise ValueError("sigma_theta must be nonnegative")
    a, mu_d, cd = np.asarray(point), np.asarray(mean_d), np.asarray(cov_d)
    v = sigma_theta**2
    b = rotation(mean_theta) @ a
    jb = np.array([-b[1], b[0]])
    # Stable expressions even when theta is very small.
    vc = 0.5 * np.expm1(-v)**2
    vs = -0.5 * np.expm1(-2*v)
    mean = mu_d + np.exp(-v/2)*b-a
    cov = cd + vc*np.outer(b,b) + vs*np.outer(jb,jb)
    return mean, cov


def normal_two_sided_failure(nsigma):
    """No conventional 1.5-sigma mean-shift assumption is added."""
    return float(2*norm.sf(positive(nsigma, "target_nsigma")))


def exact_radial_sf(margin, radius, sigma_translation, sigma_theta):
    """P(||D + (R(theta)-I)a|| > margin), exact nonlinear model.

    D~N(0,st^2 I), theta~N(0,so^2), independent. ||a||=radius.
    Deterministic quadrature of the conditional noncentral chi-square survival.
    Numerical truncation at |theta/so|=10 omits at most 1.524e-23 probability.
    The far-tail shortcut omits at most exp(-72) conditional probability.
    """
    st = positive(sigma_translation, "sigma_translation")
    if radius < 0 or sigma_theta < 0 or not np.isfinite(radius+sigma_theta):
        raise ValueError("radius and sigma_theta must be finite and nonnegative")
    if margin < 0:
        return 1.
    if radius == 0 or sigma_theta == 0:
        return float(np.exp(-0.5*(margin/st)**2))
    ms = margin/st

    def integrand(z):
        noncentral_radius = abs(2*radius*np.sin(sigma_theta*z/2))/st
        gap = ms-noncentral_radius
        if gap >= 12:
            tail = 0.
        elif gap <= -12:
            tail = 1.
        else:
            tail = ncx2.sf(ms*ms, 2, noncentral_radius**2)
        return 2*norm.pdf(z)*tail

    val, _ = quad(integrand, 0, 10, epsabs=1e-14, epsrel=2e-8, limit=180)
    return float(np.clip(val, 0., 1.))


def exact_radial_quantile(failure, radius, sigma_translation, sigma_theta):
    if not 1e-12 <= failure < 1:
        raise ValueError("Reference quadrature supports 1e-12 <= failure < 1")
    st = positive(sigma_translation, "sigma_translation")
    upper = np.sqrt(-2*np.log(failure))*np.hypot(st, radius*sigma_theta)
    upper = max(upper, st)
    while exact_radial_sf(upper, radius, st, sigma_theta) > failure:
        upper *= 2
    return float(brentq(lambda m: exact_radial_sf(m, radius, st, sigma_theta)-failure,
                        0., upper, xtol=1e-7))


def gaussian_circle_bound(failure, radius, sigma_translation, sigma_theta):
    """Conservative enclosing circle for the LINEARIZED Gaussian ellipse."""
    return float(np.sqrt(-2*np.log(failure))*
                 np.hypot(sigma_translation, radius*sigma_theta))


def die_simultaneous_bound(failure_package, die_count, radius,
                           sigma_translation, sigma_theta):
    """All specified points/dies, no residual errors, zero mean model.

    Union bound allocates package failure/die_count. A 3D input ellipsoid
    covers the whole die. The second-order remainder makes the rigid bound
    valid beyond the first-order approximation. Inter-die independence is
    NOT needed for the union bound; each die needs the given marginal law.
    """
    if die_count < 1:
        raise ValueError("die_count must be positive")
    k = np.sqrt(chi2.isf(failure_package/die_count, 3))
    linear = k*np.hypot(sigma_translation, radius*sigma_theta)
    remainder = 0.5*radius*(k*sigma_theta)**2
    return float(linear+remainder)


def cp_interval(successes, trials, alpha=.05):
    """Exact Clopper-Pearson two-sided interval for independent package trials."""
    from scipy.stats import beta
    lo = 0. if successes == 0 else beta.ppf(alpha/2, successes, trials-successes+1)
    hi = 1. if successes == trials else beta.ppf(1-alpha/2, successes+1, trials-successes)
    return [float(lo), float(hi)]


def simulate_capture_package(points_by_die, margins_by_die, mean, covariance,
                             trials=100_000, seed=20260923):
    """Capture-only package calculation with full inter-die covariance.

    mean/covariance use ordered coordinates DX1,DY1,theta1,DX2,... .
    Does NOT assess routing, metal spacing, lithography residuals, or reliability.
    Pass those through a real CAD/DRC flow before using the selection score.
    """
    nd = len(points_by_die)
    mu, cov = np.asarray(mean, float), np.asarray(covariance, float)
    if nd == 0 or len(margins_by_die) != nd or mu.shape != (3*nd,) or cov.shape != (3*nd,3*nd):
        raise ValueError("inconsistent die/state dimensions")
    if not np.all(np.isfinite(mu)) or not np.all(np.isfinite(cov)):
        raise ValueError("nonfinite state parameters")
    if not np.allclose(cov,cov.T) or np.linalg.eigvalsh(cov).min() < -1e-10:
        raise ValueError("covariance must be symmetric positive semidefinite")
    if int(trials) != trials or trials <= 0:
        raise ValueError("trials must be a positive integer")
    rng, passed = np.random.default_rng(seed), 0
    for start in range(0, trials, 4096):
        count = min(4096, trials-start)
        z = rng.multivariate_normal(mu, cov, count, check_valid="raise")
        ok = np.ones(count, dtype=bool)
        for i, (points, margins) in enumerate(zip(points_by_die, margins_by_die)):
            points, margins = np.asarray(points, float), np.asarray(margins, float)
            if len(points) == 0 or margins.shape != (len(points),) or np.any(margins <= 0):
                raise ValueError("each die needs points and a positive margin per point")
            d = displacement(points, z[:,3*i:3*i+3])
            ok &= (np.linalg.norm(d,axis=-1) <= margins[None,:]).all(axis=1)
        passed += int(ok.sum())
    return {"capture_only": True, "trials": trials, "successes": passed,
            "yield": passed/trials, "yield_95pct_interval": cp_interval(passed,trials),
            "warning": "Not total process yield; ordinary MC does not certify six sigma."}


def selection_score(expected_cost, total_yield, geometric_yield_lower,
                    geometric_yield_target, hard_rules_pass=True):
    """Expected cost per good package; reject insufficiently qualified routes."""
    if expected_cost < 0 or not np.isfinite(expected_cost):
        raise ValueError("expected cost must be finite and nonnegative")
    if not 0 <= total_yield <= 1 or not 0 <= geometric_yield_lower <= 1:
        raise ValueError("yield must be in [0,1]")
    if not 0 < geometric_yield_target <= 1:
        raise ValueError("target must be in (0,1]")
    if not hard_rules_pass or geometric_yield_lower < geometric_yield_target or total_yield == 0:
        return float("inf")
    return expected_cost/total_yield


def self_test():
    np.testing.assert_allclose(displacement([[10.,0.]], [[0.,0.,np.pi/2]])[0,0],[-10.,10.],atol=1e-12)
    np.testing.assert_allclose(displacement([[20.,-30.]], [[4.,7.,0.]])[0,0],[4.,7.],atol=1e-12)
    # Nonzero mean / larger angle tests the nonlinear exact moments.
    rng = np.random.default_rng(7352)
    point = np.array([350.,-220.])
    mean = np.array([2.,-3.,.1])
    covariance = np.diag([9.,16.,.25**2])
    states = rng.multivariate_normal(mean,covariance,400_000)
    d = displacement([point],states)[:,0,:]
    em, ec = exact_moments_independent(point,mean[:2],covariance[:2,:2],mean[2],.25)
    np.testing.assert_allclose(d.mean(0),em,atol=.45)
    np.testing.assert_allclose(np.cov(d.T),ec,rtol=.012,atol=.4)
    assert abs(exact_radial_sf(12.,0.,4.,.03)-np.exp(-4.5)) < 1e-14
    q = exact_radial_quantile(.01,5000.,25/6,np.deg2rad(1/6))
    state2 = rng.normal(size=(300_000,3))*[25/6,25/6,np.deg2rad(1/6)]
    empirical = (np.linalg.norm(displacement([[5000.,0.]],state2)[:,0,:],axis=1)>q).mean()
    assert abs(empirical-.01) < .0007, empirical
    q6 = exact_radial_quantile(normal_two_sided_failure(6),5000.,25/6,np.deg2rad(1/6))
    np.testing.assert_allclose(exact_radial_sf(q6,5000.,25/6,np.deg2rad(1/6)),normal_two_sided_failure(6),rtol=2e-6)
    # A different quadrature rule cross-checks the six-sigma tail calculation.
    from scipy.special import roots_legendre
    nodes, weights = roots_legendre(800)
    z = 5*(nodes+1)
    nc = (10000*np.sin(np.deg2rad(1/6)*z/2)/(25/6))**2
    legendre_tail = float(np.dot(5*weights,2*norm.pdf(z)*ncx2.sf((q6/(25/6))**2,2,nc)))
    np.testing.assert_allclose(legendre_tail,normal_two_sided_failure(6),rtol=2e-6)
    assert selection_score(100,.98,.99999,.999)==100/.98
    assert np.isinf(selection_score(100,.98,.98,.999))
    # Duplicating a point must not introduce independent failure probabilities.
    pars = dict(mean=np.zeros(3),covariance=np.diag([9.,9.,.001**2]),trials=20_000)
    p1=simulate_capture_package([[[1000.,0.]]],[[6.]],**pars)
    p2=simulate_capture_package([[[1000.,0.],[1000.,0.]]],[[6.,6.]],**pars)
    assert p1["successes"]==p2["successes"]
    return {"status":"PASS", "checks": ["rigid transforms", "exact moments vs Monte Carlo",
            "Rayleigh special case", "nonlinear radial quantile vs Monte Carlo at 99%",
            "six-sigma numerical tail inversion", "independent 800-node quadrature at six sigma",
            "feasibility score", "shared-die point correlation"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-nsigma",type=float,default=6.,help="given 25/35 um and 1 degree are +/- this times sigma")
    parser.add_argument("--target-nsigma",type=float,default=6.,help="target two-sided normal-equivalent coverage; no 1.5 sigma drift")
    args=parser.parse_args()
    n=positive(args.input_nsigma,"input_nsigma")
    failure=normal_two_sided_failure(args.target_nsigma)
    if failure < 1e-12:
        parser.error("target beyond this reference quadrature's 1e-12 tail resolution")
    output=ROOT/"results"
    output.mkdir(exist_ok=True)
    rows=[]
    for label,bound in [("single",25.),("multi_per_die",35.)]:
        st,so=bound/n,np.deg2rad(1/n)
        for radius in (0.,1000.,2000.,5000.,8000.,10000.):
            capture_failure=exact_radial_sf(70.,radius,st,so)
            rows.append({"case":label,"input_nsigma":n,"target_nsigma":args.target_nsigma,
                "radius_um":radius,"sigma_translation_um":st,"sigma_theta_deg":1/n,
                "sigma_tangential_linear_um":float(np.hypot(st,radius*so)),
                "q_exact_point_um":exact_radial_quantile(failure,radius,st,so),
                "ellipse_circle_bound_linear_um":gaussian_circle_bound(failure,radius,st,so),
                "capture_failure_at_margin70_um":("<1e-12 (numerical reporting floor)"
                    if capture_failure<1e-12 else capture_failure)})
    thresholds=[]
    for label,bound in [("single",25.),("multi_per_die",35.)]:
        st,so=bound/n,np.deg2rad(1/n)
        for margin in (30.,50.,70.,100.,150.):
            if exact_radial_sf(margin,0.,st,so)>failure:
                rmax=None
            else:
                rmax=float(brentq(lambda r:exact_radial_sf(margin,r,st,so)-failure,0.,1e6))
            kval=np.sqrt(-2*np.log(failure))
            rscreen=None if margin<kval*st else float(np.sqrt((margin/kval)**2-st**2)/so)
            thresholds.append({"case":label,"margin_um":margin,"rmax_exact_point_um":rmax,
                               "rmax_linear_screen_um":rscreen})
    summary={"assumptions":{"given_values_are_symmetric_sigma_bounds":True,
        "input_nsigma":n,"target_nsigma":args.target_nsigma,"target_failure":failure,
        "means":0,"independent_translation_and_rotation":True,"equal_xy_sigma":True,
        "overlay_and_distortion": "excluded; must be added for a production decision",
        "interpretation":"multichip rows describe one point on one die, not whole-package yield"},
        "verification":self_test(),"point_results":rows,"margin_thresholds":thresholds,
        "simultaneous_example": {"single_r5000_um":die_simultaneous_bound(failure,1,5000.,25/n,np.deg2rad(1/n)),
        "four_die_r5000_um":die_simultaneous_bound(failure,4,5000.,35/n,np.deg2rad(1/n))}}
    (output/"model_results.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family":"Noto Sans CJK JP","axes.unicode_minus":False,"font.size":11})
    radii=np.linspace(0,12000,85)
    fig,axes=plt.subplots(1,2,figsize=(13,5.3),layout="constrained")
    for label,bound,color in [("单芯片",25.,"#739cb7"),("多芯片中的单颗",35.,"#b294ab")]:
        st,so=bound/n,np.deg2rad(1/n)
        qq=[exact_radial_quantile(failure,r,st,so) for r in radii]
        axes[0].plot(radii,qq,label=label,color=color,lw=2)
        axes[1].plot(radii,np.hypot(st,radii*so),label=label,color=color,lw=2)
    axes[0].axhline(70,color="#606b73",ls="--",lw=1.5,label="示例净容差 70 μm")
    axes[0].set(title=f"单个接口所需容差：等效 {args.target_nsigma:g}σ 覆盖",xlabel="接口距所属芯片中心 r（μm）",ylabel="精确径向分位数（μm）")
    axes[1].plot(radii,radii*np.deg2rad(1/n),color="#999999",ls="--",label="旋转单独贡献 r·σθ")
    axes[1].set(title="旋转使远端接口误差增大",xlabel="接口距所属芯片中心 r（μm）",ylabel="切向标准差，小角度近似（μm）")
    for ax in axes:
        ax.grid(alpha=.2)
        ax.legend(frameon=False,loc="upper left")
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle(f"建模示例：25 / 35 μm、1° 解释为 ±{n:g}σ；零均值、独立误差",fontsize=15)
    figdir=ROOT/"figures"
    figdir.mkdir(exist_ok=True)
    fig.savefig(figdir/"radius_vs_capture.png",dpi=300)
    fig.savefig(figdir/"radius_vs_capture.svg")
    plt.close(fig)
    print(json.dumps({"verification":summary["verification"],"assumptions":summary["assumptions"],
                      "point_results":rows,"margin_thresholds":thresholds,
                      "simultaneous_example":summary["simultaneous_example"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
