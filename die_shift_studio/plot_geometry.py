"""Matplotlib paths preserving disconnected components and polygon holes."""
import numpy as np
from shapely.geometry.polygon import orient
from matplotlib.path import Path

def geometry_path(geometry,transform=None):
    vertices=[];codes=[]
    def add(part):
        if part.is_empty:return
        if part.geom_type=='Polygon':
            part=orient(part,sign=1.)
            for ring in [part.exterior,*part.interiors]:
                xy=np.asarray(ring.coords)
                if transform is not None:xy=transform(xy)
                vertices.extend(xy);codes.extend([Path.MOVETO]+[Path.LINETO]*(len(xy)-2)+[Path.CLOSEPOLY])
        elif hasattr(part,'geoms'):
            for child in part.geoms:add(child)
    add(geometry)
    return Path(np.asarray(vertices).reshape(-1,2),np.asarray(codes,dtype=np.uint8))
