"""Interior visibility fallback for disconnected/discretised medial graphs."""
import heapq
import math
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry.polygon import orient
from .resources import cancel_check


def trace(poly,start,end,width):
    # Thousands of visibility edges query the SAME polygon. Preparing its
    # segment index once avoids repeatedly scanning every foreign-net ring.
    # Prepared GEOS indexes are lazily mutated. Never attach one to a
    # source polygon shared by foreground and background reconstruction.
    poly=shapely.from_wkb(poly.wkb)
    endpoints=shapely.MultiPoint([start,end])
    # A path certified by exact covers cannot begin outside its domain or
    # cross disconnected components. Reject before constructing large graphs.
    if not poly.covers(endpoints):return None
    if poly.geom_type=='MultiPolygon':
        poly=next((g for g in poly.geoms if g.covers(endpoints)),None)
        if poly is None:return None
    shapely.prepare(poly)
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
        tree=cKDTree(xy);tested={};n=len(xy)
        heuristic=np.linalg.norm(xy-xy[dst],axis=1)
        for neighbours in (24,96,n if n<=800 else 256):
            cancel_check();_,near=tree.query(xy,k=min(neighbours,n))
            near=np.asarray(near,dtype=np.int64).reshape(n,-1)
            left=np.broadcast_to(np.arange(n,dtype=np.int64)[:,None],near.shape)
            lo=np.minimum(left,near).ravel();hi=np.maximum(left,near).ravel();keep=lo!=hi
            codes=np.unique(lo[keep]*n+hi[keep]);pairs=np.column_stack((codes//n,codes%n))
            # Exact undirected k-neighbour graph, but visibility is evaluated
            # only when A* reaches a node. This avoids checking every remote
            # edge of a large free-space polygon before finding a nearby path.
            directed=np.concatenate((pairs,pairs[:,::-1]));directed=directed[np.argsort(directed[:,0],kind='stable')]
            offsets=np.r_[0,np.cumsum(np.bincount(directed[:,0],minlength=n))]
            todo=[(float(heuristic[src]),0.,src)];distance={src:0.};previous={}
            while todo:
                cancel_check();_,value,i=heapq.heappop(todo)
                if value!=distance.get(i):continue
                if i==dst:break
                neighbours_i=np.unique(np.r_[directed[offsets[i]:offsets[i+1],1],dst])
                neighbours_i=neighbours_i[neighbours_i!=i]
                edges=np.minimum(i,neighbours_i)*n+np.maximum(i,neighbours_i)
                missing=np.array([q for q,code in enumerate(edges) if int(code) not in tested],dtype=int)
                for offset in range(0,len(missing),512):
                    cancel_check();indices=missing[offset:offset+512];targets=neighbours_i[indices]
                    lines=shapely.linestrings(np.stack((np.repeat(xy[i][None,:],len(targets),axis=0),xy[targets]),axis=1))
                    inside=shapely.covers(poly,lines)
                    tested.update((int(edges[j]),bool(ok)) for j,ok in zip(indices,inside))
                for j,code in zip(neighbours_i,edges):
                    if not tested[int(code)]:continue
                    j=int(j);next_value=value+float(np.linalg.norm(xy[i]-xy[j]))
                    if next_value<distance.get(j,math.inf):
                        distance[j]=next_value;previous[j]=i;heapq.heappush(todo,(next_value+float(heuristic[j]),next_value,j))
            if dst in previous:
                order=[dst]
                while order[-1]!=src:order.append(previous[order[-1]])
                result=shapely.LineString(xy[order[::-1]])
                if poly.covers(result):return result
    return None
