"""Normalize GDS winding contours without losing holes or changing the source."""
import numpy as np
import shapely
import gdstk
from shapely.geometry import Polygon,LineString
from shapely.ops import split,unary_union


def polygon_parts(geometry):
    if geometry.geom_type=='Polygon':return [geometry]
    return [p for g in getattr(geometry,'geoms',[]) for p in polygon_parts(g)]


def hole_free_parts(poly):
    """Exact planar cuts, not hole filling; keeps the array renderer lossless."""
    pending=[poly];out=[]
    while pending:
        p=pending.pop()
        if not p.interiors:out.append(p);continue
        hole=Polygon(p.interiors[0]);x=hole.representative_point().x
        x0,y0,x1,y1=p.bounds;line=LineString([(x,y0-max(1.,y1-y0)),(x,y1+max(1.,y1-y0))])
        parts=polygon_parts(split(p,line))
        if len(parts)<2 or sum(len(q.interiors) for q in parts)>=len(p.interiors):
            raise ValueError('孔洞分解未收敛，保留异常标记')
        pending.extend(parts)
    return out


def repair_gds_polygon(points,dbu):
    """GDS nonzero winding union supplies the reference fill, including cut lines."""
    merged=gdstk.boolean([gdstk.Polygon(points)],[],'or',precision=max(dbu,1e-9))
    if not merged:raise ValueError('Merge后没有可恢复的面')
    expected=sum(p.area() for p in merged)
    reference=unary_union([Polygon(p.points).buffer(0) for p in merged])
    tolerance=max(1e-8,expected*1e-10)
    if not reference.is_valid or reference.area<=0 or abs(reference.area-expected)>tolerance:
        raise ValueError('Merge与修复的填充面积不一致')
    parts=[piece for p in polygon_parts(reference) for piece in hole_free_parts(p)]
    restored=unary_union(parts)
    if not all(p.is_valid and p.area>0 for p in parts) or restored.symmetric_difference(reference).area>tolerance:
        raise ValueError('分解后几何不一致')
    arrays=[np.asarray(p.exterior.coords)[:-1].copy() for p in parts]
    return arrays,dict(method='GDS非零绕数Merge → buffer(0) → 保留孔洞的平面分解',
                      area_um2=reference.area,holes=sum(len(p.interiors) for p in polygon_parts(reference)),parts=len(parts))


def sanitize_data(data):
    token=tuple((k,id(v),len(v)) for k,v in data.layers.items())
    if getattr(data,'_sanitized_token',None)==token:return data
    if not hasattr(data,'ignored_geometry'):data.ignored_geometry=[]
    if not hasattr(data,'repaired_geometry'):data.repaired_geometry=[]
    for key,arrays in list(data.layers.items()):
        valid=[]
        for index,array in enumerate(arrays):
            points=np.asarray(array,dtype=float);reason=''
            try:
                if points.ndim!=2 or points.shape[1]!=2 or len(points)<3:raise ValueError('无法构成二维多边形')
                if not np.isfinite(points).all():raise ValueError('含非有限坐标')
                if np.max(np.abs(points))>1e9:raise ValueError('坐标超出支持范围 ±1e9 μm')
                poly=Polygon(points)
                if poly.is_valid and poly.area>0:valid.append(array);continue
                reason=shapely.is_valid_reason(poly)
                repaired,info=repair_gds_polygon(points,data.dbu_um)
                valid.extend(repaired)
                data.repaired_geometry.append(dict(layer=key,index=index+1,reason=reason,bounds=list(poly.bounds),**info))
                continue
            except (ValueError,RuntimeError,shapely.errors.GEOSException) as error:reason+='；'+str(error)
            finite=points[np.isfinite(points).all(axis=1)] if points.ndim==2 and points.shape[1]==2 else np.empty((0,2))
            if len(finite):bounds=[*finite.min(axis=0),*finite.max(axis=0)]
            else:bounds=[0.,0.,0.,0.];reason+='；无可定位有限坐标，原点提示框'
            data.ignored_geometry.append(dict(layer=key,index=index+1,reason=reason,bounds=list(map(float,bounds)),points=points.copy()))
        data.layers[key]=valid
    data._sanitized_token=tuple((k,id(v),len(v)) for k,v in data.layers.items())
    return data
