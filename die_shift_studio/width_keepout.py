"""Fixed forbidden regions for width routing. Boundary contact is allowed."""
import numpy as np
import shapely


def regions(model):
    """Merge overlapping keepouts while retaining source-layer visibility links."""
    if hasattr(model,'_width_keepout_regions'):
        return model._width_keepout_regions
    sources=[((key,i),g) for key,role in model.roles.items() if role=='禁布区'
             for i,g in enumerate(model.cache.layers[key]['geometry']) if not g.is_empty]
    result=[]
    if sources:
        originals=np.asarray([g for _,g in sources],object)
        tree=shapely.STRtree(originals)
        for g in shapely.get_parts(shapely.union_all(originals)):
            if g.geom_type!='Polygon' or g.is_empty:continue
            refs=tuple(sources[int(i)][0] for i in tree.query(g,predicate='intersects'))
            result.append((g,tuple(sorted(refs))))
    model._width_keepout_regions=result
    return result


def forbidden(model):
    if not hasattr(model,'_width_keepout_union'):
        model._width_keepout_union=shapely.union_all([g for g,_ in regions(model)])
    return model._width_keepout_union


def violations(model,states,replacements=None,*,replacements_only=False):
    """Report positive-area copper intrusion; touching a boundary never fails.

    Uses current moving RDL and accepted replacement copper. Keepouts are fixed,
    independent of display checkboxes and with no DRC spacing/tolerance buffer.
    """
    objects=regions(model)
    if not objects:return []
    if not hasattr(model,'_width_keepout_tree'):
        model._width_keepout_tree=shapely.STRtree([g for g,_ in objects])
    tree=model._width_keepout_tree
    replacements=replacements or {};out=[]
    for key,role in model.roles.items():
        if role not in ('固定RDL','运动RDL'):continue
        changed=replacements.get(key,[])
        hidden={int(i) for q in changed for i in q['indices']}
        items=[]
        if not replacements_only:
            for i,g in enumerate(model.cache.layers[key]['geometry']):
                if i in hidden:continue
                node=model.nodes[model.ids[key,i]]
                if node['owner']>=0:g=model.moved(g,node['owner'],states)
                items.append(((i,),g,node['net']))
        for q in changed:
            ids=tuple(q['indices']);items.append((ids,q['geometry'],model.nodes[model.ids[key,ids[0]]]['net']))
        if not items:continue
        geometries=np.asarray([g for _,g,_ in items],object)
        src,obstacle=tree.query(geometries,predicate='intersects')
        if not len(src):continue
        # Interior/interior intersection excludes a common edge or isolated point.
        enter=shapely.relate_pattern(geometries[src],tree.geometries[obstacle],'T********')
        src,obstacle=src[enter],obstacle[enter]
        for index in np.unique(src):
            ids,g,net=items[int(index)]
            targets=obstacle[src==index]
            area=g.intersection(shapely.union_all(tree.geometries[targets]))
            if area.is_empty or area.area<=0:continue
            refs=tuple(sorted({r for j in targets for r in objects[int(j)][1]}))
            out.append(dict(kind='禁布区侵入',layer=key,layers=(key,),indices=ids,net=net,
                            geometry=area,point=list(area.representative_point().coords[0]),
                            gap_um=0.,required_um=0.,intrusion_area_um2=float(area.area),
                            boundary_refs=refs,boundary_layers=tuple(sorted({k for k,_ in refs}))))
    return out


def routing_violations(model,states,parameters,settings,replacements=None,*,replacements_only=False):
    from .edge_clearance import edge_violations
    return (edge_violations(model,states,parameters,settings,replacements,replacements_only=replacements_only)
            +violations(model,states,replacements,replacements_only=replacements_only))
