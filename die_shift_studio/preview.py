"""Boundary-state previews. No routing or implicit deformation is performed."""
import math
import numpy as np
from scipy.stats import chi2,norm
from shapely.geometry import Polygon
from shapely.ops import nearest_points

from .statistics import statistical_state,ExtremeCatalog,PREVIEW_SCOPE

def default_motion(role):
    return 'auto' if role in ('芯片边框','运动过孔','运动RDL') else 'fixed'

def extreme_cases(case,p):
    return ExtremeCatalog(case,p)

def rigid_transform(points,center,state):
    dx,dy,theta=state;c,s=math.cos(theta),math.sin(theta)
    rot=np.array([[c,-s],[s,c]])
    return (np.asarray(points)-center)@rot.T+center+[dx,dy]

def layer_owners(data,case,motions,keys=None):
    """Use the same downward metal trace as evaluation and cached previews."""
    roles=case.get('roles',{});result={}
    for key in (keys if keys is not None else data.layers):
        if default_motion(roles.get(key))=='fixed':result[key]=np.full(len(data.layers[key]),-1,dtype=int)
        elif roles[key]=='芯片边框':
            result[key]=np.array([next(i for i,d in enumerate(case['dies']) if d.equals(Polygon(a))) for a in data.layers[key]],int)
        else:result[key]=case.get('ownership',{}).get(key,np.full(len(data.layers[key]),-2,dtype=int)).copy()
    return result

def transformed_layers(data,case,motions,states,keys=None,owners=None,parameters=None):
    from .pivots import rotation_center
    keys=list(data.layers) if keys is None else list(keys)
    owners=layer_owners(data,case,motions,keys) if owners is None else owners
    output={}
    for key in keys:
        output[key]=[points if owner<0 else rigid_transform(points,rotation_center(case,owner,parameters),states[owner])
                     for points,owner in zip(data.layers[key],owners[key])]
    return output
