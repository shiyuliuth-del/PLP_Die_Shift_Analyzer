"""One source-preserving area filter for display, computation and export.

Indices in saved projects stay in the unfiltered merged source namespace.
Filtered indices are transient; no hidden tiny polygons remain in calculations.
"""
import copy
import hashlib
import json
import numpy as np
import shapely
from .gds_merge import filled_polygon,winding_outline


def normalize_deletions(rows):
    """Deletion records name unfiltered objects and their original contours."""
    import re
    if not isinstance(rows,(list,tuple)):raise ValueError('删除图形记录必须为列表')
    result={}
    for row in rows:
        if (not isinstance(row,(list,tuple)) or len(row)!=3 or not isinstance(row[0],str)
                or type(row[1]) is not int or row[1]<0 or not isinstance(row[2],str)
                or not re.fullmatch('[0-9a-f]{64}',row[2])):
            raise ValueError('删除记录须包含图层、源图形编号和轮廓指纹')
        ref=tuple(row[:2])
        if ref in result and result[ref]!=row[2]:raise ValueError('同一图形的删除记录不一致')
        result[ref]=row[2]
    return tuple((*ref,result[ref]) for ref in sorted(result))


def remap_project(project,mapping):
    p=copy.deepcopy(project);cfg=p.setdefault('settings',{})
    def ref(k,i):return mapping.get((k,int(i)))
    def via_refs(rows):
        if rows and isinstance(rows[0],(tuple,list)):
            return tuple((k,ref(k,i)) for k,i in rows if ref(k,i) is not None)
        return rows
    for name in ('ignored_width_lines','ignored_reconstruction'):
        if p.get(name):p[name]['refs']=[(k,ref(k,i)) for k,i in p[name]['refs'] if ref(k,i) is not None]
    for name in ('drc_waived_refs','drc_retained_refs','reconstruction_ignored','reconstruction_following','reconstruction_box_excluded','reconstruction_box_starts','reconstruction_fixed_via_follows','reconstruction_between_overrides','reconstruction_forced','reconstruction_added',
                 'reconstruction_angles','reconstruction_deleted','reconstruction_intervals',
                 'reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts','reconstruction_dropped_cuts'):
        rows=[]
        for row in cfg.get(name,()):
            k,i,*tail=row;j=ref(k,i)
            if j is None:continue
            if name in ('reconstruction_angles','reconstruction_deleted','reconstruction_intervals') and len(tail)>1:tail[1]=via_refs(tail[1])
            elif name.endswith('_cuts') and tail:tail[0]=via_refs(tail[0])
            rows.append((k,j,*tail))
        if name in cfg:cfg[name]=rows
    return p


def apply(data,project,*,reference=None):
    threshold=float(project.get('parameters',{}).get('minimum_area_um2',0.))
    edge=float(project.get('parameters',{}).get('minimum_edge_um',0.01))
    if not np.isfinite(edge) or edge<0:raise ValueError('最短边长须为有限非负数字')
    deletions=normalize_deletions(project.get('deleted_shapes',()))
    if (threshold<=0 and edge<=0 and not deletions) or getattr(data,'fabrication_filter',None)==(threshold,edge,deletions):return data,project
    from .short_edges import contract
    from .gds_shapes import shape_nodes,node_fingerprint
    base=getattr(data,'_source_snapshot',data)
    removed_refs=set()
    for layer,index,fingerprint in deletions:
        if index>=len(base.layers.get(layer,())):raise ValueError(f'已删除图形 {layer} #{index+1} 的源索引无效')
        kind,xy,_=shape_nodes(base,layer,index)
        if node_fingerprint(kind,xy)!=fingerprint:raise ValueError(f'已删除图形 {layer} #{index+1} 的源轮廓已改变')
        removed_refs.add((layer,index))
    out=copy.copy(data);out.layers={};out.shape_info={};out._area_base=data
    out.fabrication_filter=(threshold,edge,deletions);out.minimum_edge_um=edge;out.deleted_shapes=deletions
    out.area_threshold=threshold;out.area_indices={};mapping={};removed=holes=shortened=0
    invalid=[]
    previous=getattr(reference,'fabrication_filter',None)
    reuse=getattr(reference,'_area_base',None) is data and previous is not None and previous[:2]==(threshold,edge)
    from .fast_preview import polygons_from_arrays
    edge2=edge*edge
    for key,arrays in data.layers.items():
        values=[];metadata=[];indices=[];descriptions=data.shape_info.get(key,[None]*len(arrays))
        retained=dict(zip(reference.area_indices[key],zip(reference.layers[key],reference.shape_info[key]))) if reuse else {}
        pending_idx=[i for i in range(len(arrays)) if (key,i) not in removed_refs and i not in retained]
        fast_polys={}
        if pending_idx:
            # Batch-build simple polygons in C via shapely.polygons; fall back to
            # filled_polygon only for invalid/self-touching winding outlines.
            batch_arrays=[arrays[i] for i in pending_idx]
            batch_geos=polygons_from_arrays(batch_arrays)
            valid_mask=shapely.is_valid(batch_geos)
            areas=shapely.area(batch_geos)
            hole_counts=shapely.get_num_interior_rings(batch_geos)
            for pos,i in enumerate(pending_idx):
                if valid_mask[pos] and areas[pos]>0 and hole_counts[pos]==0:
                    fast_polys[i]=(batch_geos[pos],float(areas[pos]))
        for i,xy in enumerate(arrays):
            if (key,i) in removed_refs:continue
            if i in retained:
                xy,meta=retained[i]
                mapping[key,i]=len(values);indices.append(i);values.append(xy);metadata.append(meta)
                continue
            fast=fast_polys.get(i)
            if fast is not None:
                g,area=fast
                if edge>0:
                    arr=np.asarray(xy,float)
                    if len(arr)>1 and np.array_equal(arr[0],arr[-1]):arr=arr[:-1]
                    d=np.roll(arr,-1,axis=0)-arr
                    needs_contract=bool(np.any(d[:,0]*d[:,0]+d[:,1]*d[:,1]<edge2))
                else:
                    needs_contract=False
                if not needs_contract:
                    if area<threshold:removed+=1;continue
                    mapping[key,i]=len(values);indices.append(i);values.append(xy);metadata.append(descriptions[i])
                    continue
            else:
                g=filled_polygon(xy,True)
            try:clean=contract(g,edge)
            except ValueError:
                invalid.append(f'{key} #{i+1}');continue
            shortened+=int(clean is not g)
            if clean.is_empty or clean.area<threshold:removed+=1;continue
            kept=[r for r in clean.interiors if shapely.Polygon(r).area>=threshold]
            holes+=len(g.interiors)-len(kept)
            if clean is not g or len(kept)!=len(clean.interiors):
                clean=shapely.Polygon(clean.exterior,kept);xy=winding_outline(clean)
                meta=dict(kind='polygon',winding=bool(kept),nodes=xy.copy())
            else:meta=descriptions[i]
            mapping[key,i]=len(values);indices.append(i);values.append(xy);metadata.append(meta)
        out.layers[key]=values;out.shape_info[key]=metadata;out.area_indices[key]=indices
    out.node_edits=[]
    deletion_key=json.dumps(deletions,separators=(',',':')) if deletions else ''
    out.edit_digest=hashlib.sha256((data.edit_digest+'|source-filters-v52|'+threshold.hex()+'|'+edge.hex()+deletion_key).encode()).hexdigest()
    for attr in ('geometry_cache','prepared_case','prepared_auto','prepared_roles','_source_snapshot','_edit_index_maps'):
        out.__dict__.pop(attr,None)
    # Q works in the filtered index space. Give visible edits a filtered
    # baseline for restore, while the unfiltered source remains authoritative
    # for serialization and for edits hidden by the current filters.
    visible=[q for q in data.node_edits if (q['layer'],q['index']) in mapping]
    if visible:
        original=getattr(data,'_source_snapshot',data)
        baseline=copy.copy(out)
        baseline.layers=dict(out.layers);baseline.shape_info=dict(out.shape_info)
        touched={q['layer'] for q in visible}
        for key in touched:
            baseline.layers[key]=list(out.layers[key]);baseline.shape_info[key]=list(out.shape_info[key])
        for q in visible:
            key,old=q['layer'],q['index'];index=mapping[key,old]
            xy=original.layers[key][old];meta=original.shape_info.get(key,[None]*len(original.layers[key]))[old]
            g=filled_polygon(xy,True)
            try:clean=contract(g,edge)
            except ValueError:clean=g
            if not clean.is_empty:
                holes0=[r for r in clean.interiors if shapely.Polygon(r).area>=threshold]
                if clean is not g or len(holes0)!=len(clean.interiors):
                    xy=winding_outline(shapely.Polygon(clean.exterior,holes0))
                    meta=dict(kind='polygon',winding=bool(holes0),nodes=xy.copy())
            baseline.layers[key][index]=xy;baseline.shape_info[key][index]=meta
            kind,nodes,_=shape_nodes(baseline,key,index)
            _,after,_=shape_nodes(out,key,index)
            out.node_edits.append(dict(layer=key,index=index,original=node_fingerprint(kind,nodes),nodes=after.tolist()))
        baseline.node_edits=[]
        out._source_snapshot=baseline
    out._sanitized_token=tuple((k,id(v),len(v)) for k,v in out.layers.items())
    out.import_warnings=list(data.import_warnings)+[f'最小面积 {threshold:g} μm²：移除 {removed} 个图形，填去 {holes} 个小孔洞。源文件未改动。']
    out.import_warnings.append(f'最短边长 {edge:g} μm：处理 {shortened} 个图形。')
    if deletions:out.import_warnings.append(f'工程中已删除 {len(deletions)} 个源图形，源文件未改动。')
    if invalid:
        out.import_warnings.append(f'{len(invalid)} 个轮廓删除短边后无有效几何，已忽略：'+', '.join(invalid[:20]))
    p=remap_project(project,mapping);p['node_edits']=out.node_edits
    return out,p


def canonical_project(data,project):
    if not hasattr(data,'_area_base'):return copy.deepcopy(project)
    from .gds_shapes import shape_nodes,node_fingerprint
    mapping={(k,i):old for k,rows in data.area_indices.items() for i,old in enumerate(rows)}
    p=remap_project(project,mapping);base=data._area_base
    originals={(q['layer'],q['index']):dict(q) for q in base.node_edits}
    displayed={(q['layer'],q['index']):q for q in data.node_edits}
    visible={(key,old) for (key,_),old in mapping.items()}
    edits={ref:row for ref,row in originals.items() if ref not in visible}
    source=getattr(base,'_source_snapshot',base)
    for q in project.get('node_edits',()):
        key=q['layer'];ref=(key,q['index']);index=mapping[ref];rawref=(key,index)
        if ref in displayed and q==displayed[ref]:
            edits[rawref]=originals[rawref]
            continue
        kind,xy,meta=shape_nodes(source,key,index)
        _,_,shown_meta=shape_nodes(data,*ref)
        row=dict(q,index=index,original=node_fingerprint(kind,xy))
        if meta and meta.get('kind')!='polygon' and shown_meta and shown_meta.get('kind')=='polygon':
            row['outline_mode']=True
        edits[rawref]=row
    p['node_edits']=[edits[key] for key in sorted(edits)]
    return p
