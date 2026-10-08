"""Read process geometry from explicit layer roles; never assign import roles."""
import numpy as np
from shapely.geometry import Polygon

# Exact semantic tags may be stored as GDS TEXT on the same layer.
TAGS={'PACKAGE_OUTLINE':'封装边框','DIE_OUTLINE':'芯片边框','MOVING_VIA':'运动过孔',
      'FIXED_VIA':'固定过孔','WAFER_OUTPUT':'运动过孔','WAFER_RDL':'运动RDL',
      'PANEL_RDL':'固定RDL','WAFER_VIA':'运动过孔','PANEL_VIA':'固定过孔','NON_METAL':'不参与','KEEPOUT':'禁布区'}
from .layer_roles import ALIASES
TAGS.update(ALIASES)
TAGS.update({'MOVING_RDL':'运动RDL','FIXED_RDL':'固定RDL'})
TAGS.update({v:v for v in list(TAGS.values())})

def recognize(data,tags=None):
    # New GDS imports deliberately have no roles, even with semantic TEXT.
    # Saved projects supply the user's explicit choices separately.
    return {k:'不参与' for k in data.layers}, ['新GDS不自动设置用途；请手动指定封装边框、芯片边框及金属层。']


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
