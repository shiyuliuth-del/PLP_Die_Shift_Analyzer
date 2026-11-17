"""Explicit omission policy: subtract failed local copper, never invent a bridge."""
import numpy as np
import shapely
from .resources import cancel_check


def omit(model,frame):
    result=frame['reconstruction'];failed={tuple(ref) for ref,_ in result.get('hard_failures',())};tol=max(model.data.dbu_um,1e-6)
    original={(n['key'],n['index']):model.moved(n['poly'],n['owner'],frame['states']) for n in model.nodes}
    nets={(n['key'],n['index']):(n['key'].split('/')[0],n['net']) for n in model.nodes}
    for layer,rows in result.get('replacements',{}).items():
        for row in rows:
            for i in row['indices']:original[layer,i]=row['geometry']
    actual=dict(original);removed={};cutouts={}
    from .width_keepout import forbidden
    keepout=forbidden(model)
    for ref in failed:
        cancel_check()
        if ref not in actual:raise ValueError('失败位置没有明确源图形身份，不能执行局部删除')
        g=actual[ref];holes=[]
        for q in result.get('pieces',()):
            if (q['layer'],q['index'])!=ref:continue
            # A failing adaptive strip is optional under this explicit policy;
            # rigid ends and unrelated branches remain. Empty strips stay empty.
            if q.get('connection_failed') or q.get('hard_unroutable'):holes.append(q['regions'].get('A',shapely.Polygon()))
        for other,h in original.items():
            if other!=ref and nets[other][0]==nets[ref][0] and nets[other]!=nets[ref] and g.intersects(h):holes.append(g.intersection(h).buffer(tol*4))
        if not keepout.is_empty and shapely.relate_pattern(g,keepout,'T********'):holes.append(g.intersection(keepout).buffer(tol*4))
        cut=shapely.union_all(holes);remaining=g.difference(cut)
        polygons=[p for p in shapely.get_parts(remaining) if p.geom_type=='Polygon']
        remaining=shapely.union_all(polygons)
        if not remaining.is_valid or not np.isfinite(shapely.get_coordinates(remaining)).all():raise ValueError('局部删除后仍有无效几何，未输出')
        if remaining.difference(g).area>tol*tol or g.symmetric_difference(remaining).difference(cut).area>tol*tol:raise ValueError('局部删除越出问题范围，未输出')
        actual[ref]=remaining;removed[ref]=g.difference(remaining);cutouts[ref]=cut
    # OAS encodes global nearest-grid vertices. Validate that exact lattice,
    # not floating slivers left by local subtraction. Only explicitly failed
    # sources may lose collapsed area; unaffected source copper is untouched.
    quantization=[]
    def grid_polygon(g):
        snapped=shapely.transform(g,lambda xy:np.rint(xy/tol)*tol)
        pending=[shapely.make_valid(snapped)];polys=[]
        while pending:
            part=pending.pop()
            if part.is_empty:continue
            if part.geom_type=='Polygon':polys.append(part)
            elif part.geom_type.startswith('Multi') or part.geom_type=='GeometryCollection':pending.extend(part.geoms)
        return shapely.union_all(polys)
    lattice={ref:grid_polygon(g) for ref,g in actual.items()}
    for ref in failed:
        before=actual[ref];snapped=lattice[ref]
        delta=before.symmetric_difference(snapped)
        if delta.difference(before.boundary.buffer(tol*2)).area>tol*tol*1.e-6:
            raise ValueError('局部输出量化变化超过2 DBU，拒绝删除正常铜')
        # Long sub-DBU remnants may collapse inside a larger connected polygon.
        # Their disappeared portions are reported too, not just whole components.
        erased=before.difference(snapped.buffer(tol))
        for part in shapely.get_parts(erased):
            if not part.is_empty and part.area>0:
                quantization.append(dict(layer=ref[0],index=ref[1],removed_area_um2=float(part.area),bounds_um=list(part.bounds),reason='局部裁剪残片小于输出DBU',quantization=True))
        actual[ref]=snapped
    # Independently recheck all actual same-plane foreign nets, including
    # unmoved/ignored copper and physical via bodies. No short is waived.
    refs=list(lattice);values=[lattice[r] for r in refs];tree=shapely.STRtree(values)
    for i,g in enumerate(values):
        cancel_check()
        if not g.is_valid:raise ValueError('输出包含无效铜图形')
        for j in tree.query(g,predicate='intersects'):
            j=int(j)
            if j>i and nets[refs[i]][0]==nets[refs[j]][0] and nets[refs[i]]!=nets[refs[j]]:raise ValueError('局部删除后仍有异网接触，未输出')
    replacements={layer:[dict(row) for row in rows] for layer,rows in result.get('replacements',{}).items()}
    for ref in failed:
        layer,index=ref;rows=replacements.setdefault(layer,[])
        # Source IDs are stable even if a legacy replacement listed many IDs.
        rows[:]=[dict(row,indices=[i for i in row['indices'] if i!=index]) for row in rows if any(i!=index for i in row['indices'])]
        rows.append(dict(indices=[index],geometry=actual[ref]))
    out=dict(frame,diagnostic_only=False,partial_output=True,partial_output_validated=True,
             reconstruction=dict(result,replacements=replacements,partial_output=True,omitted_refs=tuple(sorted(failed))))
    return out,[dict(layer=k,index=i,removed_area_um2=float(g.area),bounds_um=list(g.bounds) if not g.is_empty else list(cutouts[k,i].bounds) if not cutouts[k,i].is_empty else list(original[k,i].bounds),already_absent=g.is_empty) for (k,i),g in sorted(removed.items())]+quantization
