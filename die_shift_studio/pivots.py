"""Rotation origins in drawing coordinates; ownership remains geometric/connectivity based."""
import numpy as np

PIVOT_CHOICES=(('正中心','center'),('左上角','top_left'),('右上角','top_right'),('左下角','bottom_left'),('右下角','bottom_right'),('自定义','custom'))

def rotation_center(case,owner,parameters=None):
    info=case['infos'][owner];center=np.array([info['cx'],info['cy']],float)
    if parameters is None:return center
    p=parameters.for_die(owner);mode=p.rotation_center
    if mode=='center':return center
    if mode=='custom':return center+np.array([p.rotation_x_um,p.rotation_y_um])
    # Actual minimum rectangle vertices, named by the unrotated drawing axes.
    # Sort the two upper/lower vertices; this also handles a rotated rectangle.
    points=np.asarray(case['dies'][owner].minimum_rotated_rectangle.exterior.coords)[:-1]
    order=np.lexsort((points[:,0],points[:,1]));pair=points[order[-2:] if mode.startswith('top') else order[:2]]
    return pair[np.argmax(pair[:,0]) if mode.endswith('right') else np.argmin(pair[:,0])].copy()

def rotation_centers(case,parameters=None):
    return np.array([rotation_center(case,i,parameters) for i in range(len(case['dies']))])
