"""Incremental material transport between rigid terminals.

The one-step distance field can fold where terminal influences meet. Advecting
the original material in smaller steps keeps the original rings and rejects
every intermediate fold. This is a geometric candidate, not a DRC exemption.
"""
import numpy as np
import shapely
from .resources import cancel_check


def domain(source,controls,fractions,center,pose,width,tolerance):
    from .reconstruction import transform
    from .preview import rigid_transform
    fractions=np.asarray(fractions,float);center=np.asarray(center,float);pose=np.asarray(pose,float)
    # Overlapping rigid reservations demanding different motions cannot both
    # be Dirichlet constraints. Reject them before trying 124 integration steps;
    # the caller then uses the real, non-overlapping material interfaces.
    clips=[g.intersection(source) for g in controls]
    for i,a in enumerate(clips):
        for j in range(i):
            if abs(fractions[i]-fractions[j])>1e-12 and a.intersection(clips[j]).area>tolerance*tolerance:return None,0
    mesh=shapely.segmentize(source,max(tolerance*16,min(width/3,5.)))
    holes=sum(len(p.interiors) for p in shapely.get_parts(source) if p.geom_type=='Polygon')
    for steps in (4,8,16,32,64):
        current=mesh;xy=shapely.get_coordinates(current);valid=True
        for number in range(steps):
            cancel_check();time=number/steps
            placed=[transform(g,center,pose*(w*time)) for g,w in zip(controls,fractions)]
            points=shapely.points(xy)
            distances=np.asarray([shapely.distance(points,g) for g in placed])
            weights=np.maximum(distances,tolerance*1e-6)**-2
            weights/=weights.sum(axis=0)
            # Exact Dirichlet transport on a seam; don't blend in a nonzero
            # opposite-terminal displacement due to floating-distance noise.
            nearest=np.argmin(distances,axis=0);on=distances[nearest,np.arange(len(xy))]<=tolerance*1e-5
            weights[:,on]=0.;weights[nearest[on],np.flatnonzero(on)]=1.
            moved=np.zeros_like(xy)
            for i,w in enumerate(fractions):
                old=pose*(w*time);next_pose=pose*(w*(number+1)/steps)
                co,si=np.cos(old[2]),np.sin(old[2]);rotation=np.array([[co,-si],[si,co]])
                nominal=(xy-center-old[:2])@rotation+center
                moved+=weights[i,:,None]*rigid_transform(nominal,center,next_pose)
            candidate=shapely.set_coordinates(current,moved)
            if not candidate.is_valid:
                from .reconstruction_route import parts
                polygons=parts(shapely.make_valid(candidate))
                if polygons:
                    main=max(polygons,key=lambda g:g.area);scraps=[g for g in polygons if g is not main]
                    if sum(g.area for g in scraps)<=tolerance**2*4 and all(g.distance(main)<=tolerance*2 for g in scraps):candidate=main
            if not candidate.is_valid or candidate.is_empty:
                valid=False;break
            current=candidate;xy=shapely.get_coordinates(current)
        if valid and sum(len(p.interiors) for p in shapely.get_parts(candidate) if p.geom_type=='Polygon')==holes:
            return candidate,steps
    return None,0
