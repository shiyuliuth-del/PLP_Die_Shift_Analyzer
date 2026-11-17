"""Screen-tolerant vertex/edge snapping in the currently displayed geometry."""
import math
import numpy as np
import shapely
from .selection import inverse_geometry
from .reconstruction import transform

def point(w,xy,start=None,key=''):
    xy=np.asarray(xy,float);key=(key or '').lower();direction=None
    if start is not None and ('shift' in key or 'ctrl' in key or 'control' in key):
        v=xy-start;step=math.pi/2 if 'shift' in key else math.pi/4
        angle=round(math.atan2(v[1],v[0])/step)*step;direction=np.array([math.cos(angle),math.sin(angle)])
        xy=start+direction*max(0.,float(v@direction))
    if not w.data:return xy
    pixels=w.ax.transData.transform(xy);tol=np.linalg.norm(w.ax.transData.inverted().transform(pixels+[10,0])-xy)
    if tol<=0:return xy
    probe=shapely.Point(xy).buffer(tol);w.selection.indexed();replacements=w.selection.replacements()
    hidden={(k,i) for k,rows in replacements.items() for row in rows for i in row['indices']};geos=[]
    centers=w.rotation_centers() if w.shown_states is not None else None
    for layer,groups in w.selection.groups.items():
        if not w.visible_boxes[layer].isChecked():continue
        for owner,ids,tree in groups:
            local=inverse_geometry(probe,centers[owner],w.shown_states[owner]) if centers is not None and owner>=0 else probe
            for i in tree.query(local,predicate='intersects'):
                if (layer,int(ids[i])) in hidden:continue
                g=tree.geometries[i]
                geos.append(transform(g,centers[owner],w.shown_states[owner]) if centers is not None and owner>=0 else g)
        geos.extend(row['geometry'] for row in replacements.get(layer,()) if row['geometry'].intersects(probe))
    candidates=[]
    for g in geos:
        if direction is not None:
            ray=shapely.LineString([xy-direction*tol,xy+direction*tol])
            points=shapely.get_coordinates(g.boundary.intersection(ray))
            candidates.extend((float(np.linalg.norm(v-xy)),0,v) for v in points if np.linalg.norm(v-xy)<=tol)
        else:
            vertices=shapely.get_coordinates(g);dist=np.linalg.norm(vertices-xy,axis=1)
            candidates.extend((float(d),0,v) for d,v in zip(dist,vertices) if d<=tol)
            edge=shapely.get_point(shapely.shortest_line(shapely.Point(xy),g.boundary),1)
            if edge.distance(shapely.Point(xy))<=tol:candidates.append((edge.distance(shapely.Point(xy)),1,np.asarray(edge.coords[0])))
    if not candidates:return xy
    # Prefer a visible vertex within the cursor aperture, then nearest edge.
    return min(candidates,key=lambda q:(q[1],q[0]))[2]
