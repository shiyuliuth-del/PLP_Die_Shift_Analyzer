"""Copper-to-package spacing for displayed moving / fixed RDL, including holes."""
import math
import numpy as np
import shapely


def edge_violations(model,states,parameters,settings,replacements=None):
    replacements=replacements or {}
    board=getattr(model,'case',{}).get('board');out=[]
    if board is None:return out
    for key,role in model.roles.items():
        if role not in ('固定RDL','运动RDL'):
            continue
        rule=(parameters.line_space_um if role=='固定RDL' else parameters.wafer_line_space_um)-getattr(settings,'drc_tolerance_um',0.)
        if rule<=0:
            raise ValueError(f'图层 {key} 的有效线距必须大于零')
        if not hasattr(model,'_edge_interiors'):model._edge_interiors={}
        if rule not in model._edge_interiors:
            error=min(rule*.001,max(model.data.dbu_um/4,1.e-8))
            segments=max(32,math.ceil(math.pi/(4*math.acos(max(0.,1-error/rule)))))
            inner=board.buffer(-rule,quad_segs=segments)
            shapely.prepare(inner)
            model._edge_interiors[rule]=inner
        inner=model._edge_interiors[rule]
        items=[];hidden={i for q in replacements.get(key,[]) for i in q['indices']}
        for i,poly in enumerate(model.cache.layers[key]['geometry']):
            if i in hidden:continue
            node=model.nodes[model.ids[key,i]]
            geo=model.moved(poly,node['owner'],states) if node['owner']>=0 and np.any(states[node['owner']]) else poly
            items.append(((i,),geo,node['net']))
        for q in replacements.get(key,[]):
            ids=tuple(q['indices']);items.append((ids,q['geometry'],model.nodes[model.ids[key,ids[0]]]['net']))
        if not items:continue
        geometries=np.array([g for _,g,_ in items],object)
        boundary=board.boundary
        candidates=shapely.dwithin(geometries,boundary,rule) | ~shapely.covers(board,geometries)
        for i in np.flatnonzero(candidates):
            ids,g,net=items[i]
            outside=not board.covers(g)
            gap=0. if outside else g.distance(board.boundary)
            if not outside and gap>=rule-max(1.e-9,model.data.dbu_um*1.e-6):continue
            bad=g.difference(inner)
            if bad.is_empty:
                # Exact distance decides. Preserve a real copper witness when a
                # tessellated corner would mask a sub-DBU intrusion.
                witness=shapely.get_point(shapely.shortest_line(g,boundary),0)
                bad=g.intersection(witness.buffer((rule-gap)/2))
            if not bad.is_empty:
                out.append(dict(kind='封装边缘线距不足',layer=key,layers=(key,),indices=ids,net=net,
                                geometry=bad,point=list(bad.representative_point().coords[0]),gap_um=float(gap),required_um=rule))
    return out
