"""Display-only broken copper. Never certify or manufacture this snapshot."""
from collections import defaultdict

import numpy as np
import shapely


class DiagnosticFailure(ValueError):
    def __init__(self,message,frame,details=None):super().__init__(message);self.frame=frame;self.details=details or {}


def display(result,centers,states):
    if not result.get('hard_failures'):return result
    failed={tuple(ref) for ref,_ in result['hard_failures']};pieces=[];holes={};segments=[]
    healthy=defaultdict(list)
    for piece in result.get('pieces',()):
        ref=(piece['layer'],piece['index'])
        if ref in failed and not piece.get('connection_failed') and not piece['regions']['A'].is_empty:
            healthy[ref].append(piece['regions']['A'])
    healthy={ref:shapely.union_all(parts) for ref,parts in healthy.items()}
    empty=shapely.Polygon()
    for source in result.get('pieces',()):
        q=dict(source,regions=dict(source['regions']))
        ref=(q['layer'],q['index'])
        if ref not in failed or not q.get('connection_failed'):
            pieces.append(q);continue
        q.update(connection_failed=True,valid=False,diagnostic=True)
        active=q['regions'].get('A',empty)
        protected=healthy.get(ref,empty)
        witness=active if not active.is_empty else q.get('missing_region',active)
        # Fallback diagnostic regions can cover the whole source shape. The
        # unaffected transitions are still real copper with their own checks.
        if not protected.is_empty:
            active=active.difference(protected)
            witness=witness.difference(protected)
        holes.setdefault(ref,[]).append(active)
        q['missing_region']=witness
        outline=shapely.boundary(witness)
        if outline is None:
            outline=shapely.union_all(shapely.boundary(shapely.get_parts(witness)))
        q['diagnostic_outline']=outline.difference(protected) if not protected.is_empty else outline
        q['regions']['A']=empty;q['bridge']=empty;q['bridge_area_um2']=0.
        for member in q.get('graph_members',(q,)):
            plan=member.get('route_plan')
            if plan is None:continue
            if member.get('strict_box') is not None:
                from .manual_regions import partition
                part=partition(plan,member['strict_box'])
            else:part=plan.partition(*member['interval'],member.get('boundary_angles',(0.,0.)))
            if part is None:continue
            try:
                from .connection_curves import connection_data
                center=centers[q['die']]
                _,_,_,_,near,far,*_=connection_data(part,plan,center,q.get('relative_pose',states[q['die']]))
                if 'base_pose' in q:
                    from .preview import rigid_transform
                    near=rigid_transform(near,center,q['base_pose'])
                    far=rigid_transform(far,center,q['base_pose'])
                for a,b in zip(near,far):
                    line=shapely.LineString([a,b])
                    if not protected.is_empty:
                        line=line.difference(protected)
                    segments.extend(dict(layer=q['layer'],points=np.asarray(part.coords,float))
                                    for part in shapely.get_parts(line) if part.geom_type=='LineString' and part.length>0)
            except (ValueError,shapely.errors.GEOSException):continue
        pieces.append(q)
    overrides={}
    for layer,rows in result.get('replacements',{}).items():
        out=[]
        for row in rows:
            holes_here=[g for index in row['indices'] for g in holes.get((layer,index),())]
            g=row['geometry'].difference(shapely.union_all(holes_here)) if holes_here else row['geometry']
            out.append(dict(row,geometry=g))
        overrides[layer]=out
    return dict(result,pieces=pieces,replacements=overrides,diagnostic_only=True,failed_segments=segments)


def markers(result,dbu):
    out=[]
    for ref,reason in result.get('hard_failures',()):
        parts=[q.get('missing_region',q['regions']['A']) for q in result.get('pieces',()) if (q['layer'],q['index'])==tuple(ref) and q.get('connection_failed')]
        shape=shapely.union_all(parts)
        if shape.is_empty:
            shape=shapely.union_all([r['geometry'] for r in result.get('replacements',{}).get(ref[0],()) if ref[1] in r['indices']])
            if result.get('diagnostic_only') and not shape.is_empty:
                # Keep a locator for a source-wide diagnostic with no local
                # witness, without outlining its successfully rebuilt copper.
                shape=shape.representative_point()
        if shape.is_empty:continue
        out.append(dict(layer=ref[0],layers=(ref[0],),refs=(tuple(ref),),kind='重构连接失败：'+reason,geometry=shape,connection_failure=True))
    return out
