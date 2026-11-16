"""Explicit signed manufacturing-rule tolerance, separate from contact geometry."""

def effective_rules(parameters,settings=None,role='固定RDL'):
    tolerance=getattr(settings,'drc_tolerance_um',0.)
    width=(parameters.wafer_line_width_um if role=='运动RDL' else parameters.line_width_um)-tolerance
    space=(parameters.wafer_line_space_um if role=='运动RDL' else parameters.line_space_um)-tolerance
    if width<=0 or space<=0:
        raise ValueError(f'尺寸容差 {tolerance:g} μm 必须小于{role}线宽和线距；有效检查门槛须大于0。')
    return width,space
