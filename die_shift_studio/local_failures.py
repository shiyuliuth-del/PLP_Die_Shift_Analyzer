"""Attach connection failures to the affected transition, not every zone on a net."""
import shapely
import numpy as np

def locate(rows,copper,ports,foreign,keepout,tol):
    witnesses=[]
    if not keepout.is_empty:
        g=copper.intersection(keepout)
        if not g.is_empty:witnesses.append(g)
    witnesses.extend(copper.intersection(g) for g in foreign if copper.intersects(g))
    witnesses.extend(g for g in ports if g.intersection(copper).area<=tol*tol)
    parts=[g for g in shapely.get_parts(copper) if g.geom_type=='Polygon']
    # Minimum spanning component connectors localize discontinuities without
    # marking unrelated, successfully joined branch domains.
    if len(parts)>1:
        reached={0}
        while len(reached)<len(parts):
            _,i,j=min((parts[i].distance(parts[j]),i,j) for i in reached for j in range(len(parts)) if j not in reached)
            witnesses.append(shapely.shortest_line(parts[i],parts[j]).buffer(tol*4));reached.add(j)
    bad=set(i for i,q in enumerate(rows) if q.get('connection_failed'))
    shapes=[q.get('missing_region') if q['regions']['A'].is_empty else q['regions']['A'] for q in rows]
    shapes=[g if g is not None and not g.is_empty else q.get('nominal_regions',{}).get('A',shapely.Polygon()) for q,g in zip(rows,shapes)]
    for g in witnesses:
        hit=[i for i,a in enumerate(shapes) if not a.is_empty and a.distance(g)<=tol*8]
        if hit:bad.update(hit)
        elif rows:
            i=min(range(len(rows)),key=lambda i:shapes[i].distance(g) if not shapes[i].is_empty else float('inf'))
            # A rigid-end failure is still reported in full DRC, but cannot
            # paint a remote transition which has no failed material.
            if not shapes[i].is_empty and shapes[i].distance(g)<=max(tol*8,np.sqrt(max(g.area,0.))):bad.add(i)
    if not witnesses and not bad:
        # Unknown topological defect: use actual missing nominal material,
        # not every transition of this source as an invented failing site.
        for i,a in enumerate(shapes):
            if not a.is_empty and a.intersection(copper).area<=tol*tol:bad.add(i)
    return bad,shapely.union_all(witnesses)
