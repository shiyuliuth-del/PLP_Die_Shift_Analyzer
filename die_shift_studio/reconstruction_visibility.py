"""Interior visibility fallback for disconnected/discretised medial graphs."""
import heapq
import math
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry.polygon import orient
from .resources import cancel_check


def trace(poly,start,end,width):
    for margin in (width*.25,width*.05,0.):
        cancel_check();core=poly.buffer(-margin,join_style=2) if margin else poly
        points=[np.asarray(start),np.asarray(end)]
        for shape in shapely.get_parts(core):
            if shape.geom_type!='Polygon':continue
            shape=orient(shape.simplify(max(width*.015,1e-5),preserve_topology=True),sign=1.)
            for ring in (shape.exterior,*shape.interiors):
                xy=np.asarray(ring.coords)[:-1]
                if len(xy)<3:continue
                a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
                # With free copper on the left, reflex corners are the only
                # boundary vertices required by a polygon visibility path.
                cross=a[:,0]*b[:,1]-a[:,1]*b[:,0]
                points.extend(xy[cross<0])
        xy=np.unique(np.asarray(points),axis=0)
        if len(xy)<3 or len(xy)>4096:continue
        src=int(np.argmin(np.linalg.norm(xy-start,axis=1)));dst=int(np.argmin(np.linalg.norm(xy-end,axis=1)))
        tree=cKDTree(xy);tested=set();graph=[[] for _ in xy]
        for neighbours in (24,96,len(xy) if len(xy)<=800 else 256):
            cancel_check();_,near=tree.query(xy,k=min(neighbours,len(xy)))
            candidates=[]
            for i,row in enumerate(near):
                for j in np.atleast_1d(row):
                    pair=(min(i,int(j)),max(i,int(j)))
                    if i!=j and pair not in tested:tested.add(pair);candidates.append(pair)
            for offset in range(0,len(candidates),512):
                cancel_check();pairs=np.asarray(candidates[offset:offset+512],int)
                lines=shapely.linestrings(xy[pairs]);inside=shapely.covers(poly,lines)
                for i,j in pairs[inside]:
                    length=float(np.linalg.norm(xy[i]-xy[j]));graph[i].append((int(j),length));graph[j].append((int(i),length))
            todo=[(0.,src)];distance={src:0.};previous={}
            while todo:
                value,i=heapq.heappop(todo)
                if value!=distance.get(i):continue
                if i==dst:break
                for j,length in graph[i]:
                    next_value=value+length
                    if next_value<distance.get(j,math.inf):distance[j]=next_value;previous[j]=i;heapq.heappush(todo,(next_value,j))
            if dst in previous:
                order=[dst]
                while order[-1]!=src:order.append(previous[order[-1]])
                result=shapely.LineString(xy[order[::-1]])
                if poly.covers(result):return result
    return None
