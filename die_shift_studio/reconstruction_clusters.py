"""Separate exposed rows of terminals on a common ground plane."""
import numpy as np
import shapely


def anchor_clusters(source,anchors,land,width,tolerance):
    """Nearby fixed terminals in one pad are one constraint, not two branches."""
    if len(anchors)<2:return anchors
    tree=shapely.STRtree(anchors);bridges=[]
    for i,g in enumerate(anchors):
        for j in tree.query(g,predicate='dwithin',distance=max(2*width,g.area**.5)):
            if j<=i or g.distance(anchors[j])>max(2*width,min(g.area,anchors[j].area)**.5):continue
            line=shapely.shortest_line(g,anchors[j])
            if source.covers(line) and not line.intersects(land):bridges.append(line.buffer(tolerance*2))
    return [p for p in shapely.get_parts(shapely.union_all([*anchors,*bridges])) if p.geom_type=='Polygon']


def inward_routes(source,moving):
    """Axis-normal cuts for wide buses; route endpoints need not be via centres.

    All land/anchor material is still tested by RoutePlan.prefix. This only
    removes a diagonal centre-to-centre bias in choosing a section direction.
    """
    size=np.hypot(source.bounds[2]-source.bounds[0],source.bounds[3]-source.bounds[1])+1
    centers=np.array([v['center'] for v in moving]);mean=centers.mean(axis=0)
    starts=sorted(moving,key=lambda v:np.linalg.norm(v['center']-mean))[:3]
    pairs=[]
    for via in starts:
        start=via['center']
        for direction in (np.array([1.,0.]),np.array([0.,1.])):
            chord=source.intersection(shapely.LineString([start-direction*size,start+direction*size]))
            for line in shapely.get_parts(chord):
                if line.geom_type!='LineString' or line.distance(shapely.Point(start))>1e-7:continue
                for end in (np.asarray(line.coords[0]),np.asarray(line.coords[-1])):
                    end=start+(end-start)*.99
                    if np.linalg.norm(end-start)>1e-3:pairs.append((start,end))
    return pairs


def terminal_clusters(source,vias,annulus,tolerance):
    if len(vias)<2:return [vias]
    centers=np.array([v['center'] for v in vias]);normals=[];radii=[]
    for v in vias:
        edge=shapely.shortest_line(v['poly'],source.boundary)
        direction=np.asarray(edge.coords[-1])-v['center'];length=np.linalg.norm(direction)
        normals.append(direction/max(length,tolerance));radii.append(np.sqrt(v['poly'].area/np.pi)+annulus)
    normals=np.asarray(normals);groups=[];left=set(range(len(vias)))
    while left:
        group={left.pop()};pending=list(group)
        while pending:
            i=pending.pop()
            neighbours=[j for j in left if normals[i]@normals[j]>.9 and
                        np.linalg.norm(centers[i]-centers[j])<=4*(radii[i]+radii[j]) and
                        source.covers(shapely.LineString([centers[i],centers[j]]))]
            left.difference_update(neighbours);group.update(neighbours);pending.extend(neighbours)
        groups.append([vias[i] for i in sorted(group)])
    return groups
