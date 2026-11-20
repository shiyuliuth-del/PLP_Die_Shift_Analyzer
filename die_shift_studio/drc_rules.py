"""Explicit signed manufacturing-rule tolerance, separate from contact geometry."""

def effective_rules(parameters,settings=None,role='固定RDL'):
    tolerance=getattr(settings,'drc_tolerance_um',0.)
    width=(parameters.wafer_line_width_um if role=='运动RDL' else parameters.line_width_um)-tolerance
    space=(parameters.wafer_line_space_um if role=='运动RDL' else parameters.line_space_um)-tolerance
    if width<=0 or space<=0:
        raise ValueError(f'尺寸容差 {tolerance:g} μm 必须小于{role}线宽和线距；有效检查门槛须大于0。')
    return width,space


def clearance(value,settings=None):
    """Positive tolerance relaxes a clearance; zero still forbids penetration."""
    return max(0.,float(value)-float(getattr(settings,'drc_tolerance_um',0.)))


def effective_clearances(parameters,settings=None):
    from dataclasses import replace
    return replace(parameters,**{name:clearance(getattr(parameters,name),settings) for name in
      ('annulus_um','moving_annulus_um','min_die_gap_um','min_package_clearance_um','package_clearance_um')})


def construction_rules(parameters,settings,dbu):
    """Target nominal dimensions plus quantization slack; check effective rules."""
    width,space=effective_rules(parameters,settings)
    guard=max(float(dbu)*4,1e-7)
    return max(parameters.line_width_um,width)+guard,max(parameters.line_space_um,space)+guard


def violation_priority(kinds):
    # Lexicographic severity: never trade a short for any dimensional benefit.
    return tuple(int(bool(set(kinds)&set(group))) for group in
      (('short','keepout'),('foreign','space','board'),('enclosure',),('width',),('angle',)))


def pad_first(score):
    """Preserve the rule score layout while prioritizing enclosure in choices."""
    return (score[0],score[2],score[1],*score[3:])
