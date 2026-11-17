"""Physical ERROR rectangles for measured OAS, independent of metal geometry."""
import math
import numpy as np
import shapely


def choose_error_layer(source_layers):
    occupied={tuple(map(int,key.split('/'))) for key in source_layers}
    for layer in range(65535,-1,-1):
        if (layer,0) not in occupied:return layer,0
    raise ValueError('没有空闲ERROR图层；未覆盖原始层')


def rectangles(issues,die_count,dbu):
    """Require explicit 1-based DIE attribution; owner 0 means package/static.

    Each input bounds is in the completed pose's engineering coordinates.
    Outward grid rounding prevents a narrow witness disappearing at the DBU.
    """
    if not isinstance(issues,(list,tuple)):raise ValueError('问题标记须为列表')
    groups={};seen=set()
    for issue in issues:
        bounds=np.asarray(issue['bounds_um'],float)
        if bounds.shape!=(4,) or not np.isfinite(bounds).all() or np.max(abs(bounds))>1e9 or np.any(bounds[2:]<bounds[:2]):
            raise ValueError('问题范围须为有限且有序的XY矩形')
        dies=issue.get('die_ids')
        if not isinstance(dies,(list,tuple)) or not dies or any(type(i) is not int or not 0<=i<=die_count for i in dies):
            raise ValueError('问题范围缺少明确DIE归属（1起；0表示封装公共问题）')
        lo=np.floor(bounds[:2]/dbu);hi=np.ceil(bounds[2:]/dbu)
        hi=np.maximum(hi,lo+1)
        box=shapely.box(*(lo*dbu),*(hi*dbu)).wkb
        for die in sorted(set(dies)):
            key=die,tuple(lo),tuple(hi)
            if key not in seen:groups.setdefault(die,[]).append(box);seen.add(key)
    return groups
