"""Identify unambiguous geometry; process rules are never inferred from minima."""
import numpy as np
from shapely.geometry import Polygon
from .resources import parallel_map

# Exact semantic tags may be stored as GDS TEXT on the same layer.
TAGS={'PACKAGE_OUTLINE':'封装边框','DIE_OUTLINE':'芯片边框','MOVING_VIA':'运动过孔',
      'FIXED_VIA':'固定过孔','WAFER_OUTPUT':'运动过孔','WAFER_RDL':'运动RDL',
      'PANEL_RDL':'固定RDL','WAFER_VIA':'运动过孔','PANEL_VIA':'固定过孔','NON_METAL':'不参与','KEEPOUT':'禁布区'}
from .layer_roles import ALIASES
TAGS.update(ALIASES)
TAGS.update({'MOVING_RDL':'运动RDL','FIXED_RDL':'固定RDL'})
TAGS.update({v:v for v in list(TAGS.values())})

def recognize(data,tags=None):
    roles={k:'不参与' for k in data.layers};notes=[]
    for key,role in (tags or {}).items():
        if key in roles and role in TAGS.values():roles[key]=role
    def inspect(item):
        key,polys=item;rects=[]
        for a in polys:
            lo=a.min(axis=0);hi=a.max(axis=0)
            if min(hi-lo)<499:rects.append(False);continue
            poly=Polygon(a);rect=poly.minimum_rotated_rectangle
            edge=np.diff(np.array(rect.exterior.coords),axis=0);lens=np.linalg.norm(edge,axis=1)
            rects.append(poly.is_valid and abs(poly.area-rect.area)<max(1e-5,poly.area*1e-7)
                         and min(lens)>=499 and max(lens)<=12001)
        return key,rects
    checks=dict(parallel_map(inspect,data.layers.items()))
    if '封装边框' not in roles.values():
        single=[(k,Polygon(v[0])) for k,v in data.layers.items() if len(v)==1]
        candidates=[]
        for key,poly in single:
            if not poly.is_valid or abs(poly.area-poly.minimum_rotated_rectangle.area)>max(1e-5,poly.area*1e-7):continue
            if all(poly.buffer(data.dbu_um).covers(Polygon(a)) for k,values in data.layers.items() if k!=key for a in values):candidates.append(key)
        if len(candidates)==1:roles[candidates[0]]='封装边框';notes.append('封装边框：唯一包含全部图形的闭合矩形')
    if '芯片边框' not in roles.values():
        candidates=[k for k,v in checks.items() if roles[k]=='不参与' and v and all(v)]
        if len(candidates)==1:roles[candidates[0]]='芯片边框';notes.append('芯片边框：唯一符合芯片尺寸的矩形集合')
    notes.append('无语义标注的运动/固定过孔无法仅凭图形可靠区分；只保留必要的图层用途选择。')
    return roles,notes

def automatic_parameters(data,roles):
    from .layer_roles import canonical_roles
    roles=canonical_roles(roles)
    values={};sources={}
    for role,key in [('固定RDL','panel_layers'),('运动RDL','wafer_layers')]:
        layers={layer.split('/')[0] for layer,r in roles.items() if r==role}
        values[key]=len(layers);sources[key]=f'来自{len(layers)}个GDS物理层号（同层不同datatype不重复计数）'
    # A containing circle also encloses non-circular moving-via polygons.
    diameters=[]
    for key,polys in data.layers.items():
        if roles.get(key)=='运动过孔':
            for points in polys:
                center=np.array(Polygon(points).centroid.coords)[0]
                diameters.append(2*float(np.linalg.norm(points-center,axis=1).max()))
    if diameters:
        values['via_diameter_um']=max(diameters)
        sources['via_diameter_um']='来自运动过孔几何的最大外接圆直径；非圆孔按包络保守计算'
    return values,sources
