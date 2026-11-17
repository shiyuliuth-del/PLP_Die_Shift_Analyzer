"""Physical ERROR rectangles for measured OAS, independent of metal geometry."""
import math
import numpy as np
import shapely


def choose_error_layer(source_layers,*,reserved=()):
    occupied={tuple(map(int,key.split('/'))) for key in source_layers}
    occupied.update(tuple(pair) for pair in reserved)
    for layer in range(65535,-1,-1):
        if (layer,0) not in occupied:return layer,0
    raise ValueError('没有空闲ERROR图层；未覆盖原始层')


def _grid(points,dbu):
    xy=np.rint(np.asarray(points)/dbu)
    if not np.isfinite(xy).all() or np.max(abs(xy),initial=0)>2**52:
        raise ValueError('ERROR标记超出安全输出格点范围')
    ring=tuple((int(x),int(y)) for x,y in xy)
    if sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(ring,ring[1:]+ring[:1]))<0: ring=ring[::-1]
    start=min(range(len(ring)),key=ring.__getitem__);ring=ring[start:]+ring[:start]
    return shapely.Polygon(np.asarray(ring)*dbu),ring


def _oriented(issue,bounds,dbu):
    corners=np.asarray(issue['marker_corners_um'],float)
    if corners.shape!=(4,2) or not np.isfinite(corners).all() or np.max(abs(corners))>1e9:
        raise ValueError('旋转ERROR标记须为当前姿态的四个有限XY顶点')
    u,v=corners[1]-corners[0],corners[3]-corners[0]
    a,b=np.linalg.norm(u),np.linalg.norm(v)
    tolerance=max(dbu*1e-6,np.max(abs(corners),initial=1)*np.finfo(float).eps*32)
    witness=shapely.Polygon(corners)
    if (a<=0 or b<=0 or not witness.is_valid or witness.area<=0
        or np.max(abs(corners[0]+corners[2]-corners[1]-corners[3]))>tolerance
        or abs(np.dot(u,v))>tolerance*(a+b)):
        raise ValueError('旋转ERROR标记须为有序、非退化矩形')
    if np.any(corners.min(axis=0)>bounds[:2]+tolerance) or np.any(corners.max(axis=0)<bounds[2:]-tolerance):
        raise ValueError('旋转ERROR标记与当前问题范围不一致')
    if 'marker_angle_deg' in issue:
        angle=issue['marker_angle_deg']
        if isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle):
            raise ValueError('ERROR角度须为有限度数')
    # Coordinates already contain the DIE pose. Never rotate/translate them
    # again or replace the quadrilateral with its axis-aligned bounds.
    polygon,key=_grid(corners,dbu)
    if polygon.is_valid and polygon.area>0 and polygon.covers(witness):return polygon.wkb,key
    # A grid-rounded vertex moves <= sqrt(0.5)*DBU. Expanding along both
    # rectangle axes by one DBU conservatively covers that error, including
    # subgrid-width witnesses, without throwing away the rectangle direction.
    u,v=u/a,v/b
    expanded=corners+dbu*np.asarray([-u-v,u-v,u+v,-u+v])
    polygon,key=_grid(expanded,dbu)
    if not polygon.is_valid or polygon.area<=0 or not polygon.covers(witness):
        raise ValueError('旋转ERROR标记无法在输出格点上完整保留')
    return polygon.wkb,key


def rectangles(issues,die_count,dbu):
    """Group current-pose markers by explicit DIE ownership (0 is common).

    V50 ordered corners are authoritative and already transformed. Legacy
    bounds-only records retain their outward-rounded axis-aligned rectangles.
    """
    if not isinstance(issues,(list,tuple)):raise ValueError('问题标记须为列表')
    if not math.isfinite(dbu) or dbu<=0:raise ValueError('ERROR输出DBU须为有限正数')
    groups={};seen=set()
    for issue in issues:
        bounds=np.asarray(issue['bounds_um'],float)
        if bounds.shape!=(4,) or not np.isfinite(bounds).all() or np.max(abs(bounds))>1e9 or np.any(bounds[2:]<bounds[:2]):
            raise ValueError('问题范围须为有限且有序的XY矩形')
        dies=issue.get('die_ids')
        if not isinstance(dies,(list,tuple)) or not dies or any(type(i) is not int or not 0<=i<=die_count for i in dies):
            raise ValueError('问题范围缺少明确DIE归属（1起；0表示封装公共问题）')
        if 'marker_corners_um' in issue:
            blob,ring=_oriented(issue,bounds,dbu)
        else:
            lo=np.floor(bounds[:2]/dbu);hi=np.maximum(np.ceil(bounds[2:]/dbu),lo+1)
            polygon,ring=_grid(np.asarray([[lo[0],lo[1]],[hi[0],lo[1]],[hi[0],hi[1]],[lo[0],hi[1]]])*dbu,dbu)
            blob=polygon.wkb
        for die in sorted(set(dies)):
            key=die,ring
            if key not in seen:groups.setdefault(die,[]).append(blob);seen.add(key)
    return groups
