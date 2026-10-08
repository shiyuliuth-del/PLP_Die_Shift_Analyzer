"""Explicit signed manufacturing-rule tolerance, separate from contact geometry."""

def effective_rules(parameters,settings=None):
    tolerance=getattr(settings,'drc_tolerance_um',0.)
    width=parameters.line_width_um-tolerance;space=parameters.line_space_um-tolerance
    if width<=0 or space<=0:
        raise ValueError(f'DRC容差 {tolerance:g} μm 必须小于固定线宽和线距；有效检查门槛须大于0。')
    return width,space
