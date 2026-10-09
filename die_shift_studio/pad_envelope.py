"""Smooth convex landing-island reconstruction from actual terminal footprints.

This is a contour generator, not an assertion that overlapping artificial rigid
landing disks admit a diffeomorphism. Only small, nearly convex, unbranched
source islands qualify. General nets continue through the traced field solver.
"""
import math
import numpy as np
import shapely
from shapely.geometry import Polygon


class PadEnvelope:
    mode='pad_envelope'
    def __init__(self,source,records,anchors,centers,states,settings,radii,smoothing):
        self.source=source;self.records=records;self.anchors=anchors;self.centers=centers;self.states=states;self.settings=settings
        self.radii=np.asarray(radii);self.smoothing=smoothing;self.inner=self.radii[:1]
        self.support=source;self.span=float(np.linalg.norm(np.asarray(records[0]['center'])-np.asarray(anchors[0].centroid.coords[0])))
    def candidate_geometry(self,tolerance,dbu):
        from .preview import rigid_transform
        r=self.records[0];moving=rigid_transform(np.asarray([r['center']]),self.centers[r['owner']],self.states[r['owner']])[0]
        centers=np.array([moving,self.anchors[0].centroid.coords[0]])
        origin=centers.mean(axis=0);centers-=origin
        # log-sum-exp support is >= each disk support. Its curvature radius
        # h+h'' = mean(r) + entropy/beta + beta*Var(h') is strictly positive.
        beta=math.log(2)/self.smoothing
        def boundary(phi):
            n=np.column_stack((np.cos(phi),np.sin(phi)));t=np.column_stack((-n[:,1],n[:,0]))
            support=n@centers.T+self.radii
            top=support.max(axis=1);raw=np.exp(beta*(support-top[:,None]));w=raw/raw.sum(axis=1)[:,None]
            h=top+np.log(raw.sum(axis=1))/beta
            dh=np.sum(w*(t@centers.T),axis=1)
            return origin+h[:,None]*n+dh[:,None]*t
        phi=np.linspace(0,2*math.pi,129)
        for _ in range(12):
            xy=boundary(phi);middle=(phi[:-1]+phi[1:])/2
            error=np.linalg.norm(boundary(middle)-(xy[:-1]+xy[1:])/2,axis=1)
            bad=error>max(dbu*.25,tolerance*.5)
            if not bad.any():break
            phi=np.sort(np.r_[phi,middle[bad]])
        else:raise ValueError('平滑孔盘轮廓细分未收敛')
        candidate=Polygon(boundary(phi))
        if not candidate.is_valid:raise ValueError('平滑孔盘轮廓无效')
        return candidate


def pad_envelopes(source,records,anchors,centers,states,settings,annulus,width,dbu):
    if source.geom_type!='Polygon' or source.interiors or len(records)!=1:return
    anchors=list({g.wkb:g for g in anchors}.values())
    if len(anchors)!=1:return
    if source.area/source.convex_hull.area<.97:return
    xy=shapely.get_coordinates(source);size=np.ptp(xy,axis=0)
    if max(size)>8*max(min(size),width):return
    r=records[0];center=np.asarray(r['center']);fixed=np.asarray(anchors[0].centroid.coords[0])
    from shapely.geometry import Point
    mr=max(float(np.linalg.norm(shapely.get_coordinates(r['poly'])-center,axis=1).max())+annulus,source.boundary.distance(Point(center)))
    fr=max(float(np.linalg.norm(shapely.get_coordinates(anchors[0])-fixed,axis=1).max()),source.boundary.distance(Point(fixed)))
    # Guard against inscribed chords and snapping, then independently check
    # exact enclosure, copper connectivity, unchanged anchor and all neighbours.
    radii=np.array([mr,fr])+max(4*dbu,2*settings.deformation_tolerance_um)
    for smoothing in (max(width*.2,dbu*8),max(width*.05,dbu*8)):
        yield PadEnvelope(source,records,anchors,centers,states,settings,radii,smoothing)
