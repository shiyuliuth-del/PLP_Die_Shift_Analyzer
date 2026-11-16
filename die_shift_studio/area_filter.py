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


def remap_project(project,mapping):
    p=copy.deepcopy(project);cfg=p.setdefault('settings',{})
    def ref(k,i):return mapping.get((k,int(i)))
    def via_refs(rows):
        if rows and isinstance(rows[0],(tuple,list)):
            return tuple((k,ref(k,i)) for k,i in rows if ref(k,i) is not None)
        return rows
    for name in ('ignored_width_lines','ignored_reconstruction'):
        if p.get(name):p[name]['refs']=[(k,ref(k,i)) for k,i in p[name]['refs'] if ref(k,i) is not None]
    for name in ('reconstruction_ignored','reconstruction_following','reconstruction_forced','reconstruction_added',
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


def apply(data,project):
    threshold=float(project.get('parameters',{}).get('minimum_area_um2',0.))
    if threshold<=0 or getattr(data,'area_threshold',None)==threshold:return data,project
    out=copy.copy(data);out.layers={};out.shape_info={};out._area_base=data
    out.area_threshold=threshold;out.area_indices={};mapping={};removed=holes=0
    for key,arrays in data.layers.items():
        values=[];metadata=[];indices=[]
        for i,xy in enumerate(arrays):
            g=filled_polygon(xy,True)
            if g.area<threshold:removed+=1;continue
            kept=[r for r in g.interiors if shapely.Polygon(r).area>=threshold]
            changed=len(kept)!=len(g.interiors);holes+=len(g.interiors)-len(kept)
            if changed:
                g=shapely.Polygon(g.exterior,kept);xy=winding_outline(g)
                meta=dict(kind='polygon',winding=bool(kept),nodes=xy.copy())
            else:meta=data.shape_info.get(key,[None]*len(arrays))[i]
            mapping[key,i]=len(values);indices.append(i);values.append(xy);metadata.append(meta)
        out.layers[key]=values;out.shape_info[key]=metadata;out.area_indices[key]=indices
    out.node_edits=[];out.edit_digest=hashlib.sha256((data.edit_digest+'|area-v43|'+threshold.hex()).encode()).hexdigest()
    for attr in ('geometry_cache','prepared_case','prepared_auto','prepared_roles','_source_snapshot'):
        out.__dict__.pop(attr,None)
    out._sanitized_token=tuple((k,id(v),len(v)) for k,v in out.layers.items())
    out.import_warnings=list(data.import_warnings)+[f'最小面积 {threshold:g} μm²：移除 {removed} 个图形，填去 {holes} 个小孔洞。源文件未改动。']
    p=remap_project(project,mapping);p['node_edits']=[]
    return out,p


def canonical_project(data,project):
    if not hasattr(data,'_area_base'):return copy.deepcopy(project)
    from .gds_shapes import shape_nodes,node_fingerprint
    mapping={(k,i):old for k,rows in data.area_indices.items() for i,old in enumerate(rows)}
    p=remap_project(project,mapping);base=data._area_base
    edits={(q['layer'],q['index']):dict(q) for q in base.node_edits}
    source=getattr(base,'_source_snapshot',base)
    for q in project.get('node_edits',()):
        key=q['layer'];index=mapping[key,q['index']]
        kind,xy,_=shape_nodes(source,key,index)
        edits[key,index]=dict(q,index=index,original=node_fingerprint(kind,xy))
    p['node_edits']=[edits[key] for key in sorted(edits)]
    return p
