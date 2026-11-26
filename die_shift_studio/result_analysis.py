"""Explicitly requested probability and continuous safe-range analysis."""
from dataclasses import asdict
from .settings import ViewSettings
from .session_cache import logical_fingerprint


def calculate(model,p,settings=None,cache=None,progress=lambda *_:None,contact=None):
    from .safe_ranges import safe_ranges
    from .model_yield import model_yield
    cfg=settings or ViewSettings();data=model.data;roles=model.roles;order=model.connections.order
    if contact is None or contact.get('mode')=='pending':contact=model.screen(p,settings=cfg,progress=progress)
    geometric=asdict(p)
    for key in ('input_nsigma','target_nsigma','reference_nsigma','rho_xy','sigma_x_um','sigma_y_um','sigma_angle_deg','per_die_sigma_enabled','die_sigmas'):geometric.pop(key,None)
    range_key=('continuous-safe-box',logical_fingerprint((data.geometry_id,data.top_name,roles,order,geometric,
               cfg.safe_box_precision,cfg.safe_box_budget,cfg.safe_box_starts,cfg.safe_box_passes)))
    progress(75,'结果分析 · 连续安全范围')
    make_ranges=lambda:safe_ranges(model,p,cfg,progress)
    ranges=make_ranges() if cache is None else cache.get_or_compute(range_key,make_ranges)
    key=('whole-input-yield',logical_fingerprint((data.geometry_id,data.top_name,roles,order,asdict(p),
         cfg.yield_method,cfg.yield_sample_exponent,cfg.random_seed,cfg.yield_box_budget,cfg.yield_tolerance)))
    progress(90,'结果分析 · 全输入无接触概率')
    make_yield=lambda:model_yield(model,p,cfg,progress,contact=contact)
    value=make_yield() if cache is None else cache.get_or_compute(key,make_yield)
    return dict(safe_ranges=ranges,model_yield=value,contact_analysis=contact,issues=contact['issues'],analysis_complete=True)


def attach(result,values):
    result.update(values);contact=values['contact_analysis']
    for scheme in result['schemes']:
        scheme['contact_gamma']=contact['ratios'][scheme['scheme']]
        status='存在接触超限' if contact['issues'] else '概率待细化' if contact.get('unresolved') else '原接口检查通过'
        scheme['contact_status']=status
        scheme['reasons']=[v for v in scheme['reasons'] if not v.endswith('；独立报告，不并入空间比值U')]
        scheme['reasons'].append(status+'；独立报告，不并入空间比值U')

