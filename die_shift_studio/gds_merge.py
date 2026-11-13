"""Reassemble same-layer fracture pieces, retaining legacy source identities."""
import copy
import hashlib
import json
from collections import defaultdict,Counter
import numpy as np
import shapely
from shapely.geometry.polygon import orient


def filled_polygon(points,allow_winding=False):
    xy=np.asarray(points,float);p=shapely.Polygon(xy)
    if p.is_valid and p.area>0:return p
    if not allow_winding:raise ValueError('轮廓自交、退化或没有面积')
    # Hole bridges are traversed twice in opposite directions. Remove those
    # artificial edges, then require simple, unchanged real boundary linework.
    closed=np.vstack([xy,xy[0]]) if not np.array_equal(xy[0],xy[-1]) else xy
    edges=Counter((tuple(a),tuple(b)) for a,b in zip(closed[:-1],closed[1:]) if np.any(a!=b))
    for a,b in list(edges):
        n=min(edges[a,b],edges.get((b,a),0));edges[a,b]-=n;edges[b,a]-=n
    lines=[shapely.LineString([a,b]) for (a,b),n in edges.items() for _ in range(n)]
    if not lines or not shapely.MultiLineString(lines).is_simple:raise ValueError('孔洞轮廓存在交叉边')
    repaired=p.buffer(0);boundary=shapely.union_all(lines)
    if repaired.geom_type!='Polygon' or not repaired.is_valid or repaired.area<=0 or repaired.boundary.hausdorff_distance(boundary)>1e-8:
        raise ValueError('孔洞桥接不能还原为同一有效图形')
    return repaired


def _insert(xy,point):
    xy=np.asarray(xy,float)
    near=np.linalg.norm(xy-point,axis=1);j=int(np.argmin(near))
    if near[j]<1e-10:return xy,j if j<len(xy)-1 else 0
    a=xy[:-1];v=np.diff(xy,axis=0);den=np.sum(v*v,axis=1)
    t=np.clip(np.sum((point-a)*v,axis=1)/np.maximum(den,1e-30),0,1)
    j=int(np.argmin(np.linalg.norm(a+t[:,None]*v-point,axis=1)))
    return np.insert(xy,j+1,point,axis=0),j+1


def winding_outline(poly):
    """Lossless boundary array for the existing renderer, including holes."""
    p=orient(poly,sign=1.);xy=np.asarray(p.exterior.coords)
    for hole in sorted(p.interiors,key=lambda r:r.distance(p.exterior)):
        bridge=shapely.shortest_line(hole,shapely.LineString(xy));h,q=np.asarray(bridge.coords)
        xy,j=_insert(xy,q);ring,k=_insert(np.asarray(hole.coords),h)
        ring=np.vstack([ring[k:-1],ring[:k+1]])
        xy=np.vstack([xy[:j+1],ring,xy[j:]])
    restored=filled_polygon(xy,True)
    if restored.symmetric_difference(poly).area>max(1e-8,poly.area*1e-10):raise ValueError('合并后的孔洞或轮廓不一致')
    return xy[:-1].copy()


def merge_import(data):
    if getattr(data,'geometry_indexing',None)=='merged-v39':return data
    from .fast_preview import polygons_from_arrays
    raw=copy.copy(data);raw.layers={k:list(v) for k,v in data.layers.items()};raw.shape_info={k:list(v) for k,v in data.shape_info.items()}
    out=copy.copy(data);out.layers={};out.shape_info={};out.merge_sources={};out.raw_to_merged={};out.merge_notes=[]
    for key,arrays in raw.layers.items():
        geos=polygons_from_arrays(arrays);parent=list(range(len(geos)))
        def root(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        tree=shapely.STRtree(geos)
        for i,g in enumerate(geos):
            for j in tree.query(g,predicate='intersects'):
                j=int(j)
                if j<=i:continue
                overlap=g.intersection(geos[j])
                if overlap.area>1e-14 or overlap.length>max(data.dbu_um*.01,1e-10):
                    a,b=root(i),root(j)
                    if a!=b:parent[max(a,b)]=min(a,b)
        groups=defaultdict(list)
        for i in range(len(geos)):groups[root(i)].append(i)
        result=[];metadata=[];sources=[];source=raw.shape_info.get(key,[None]*len(arrays))
        for ids in sorted(groups.values(),key=lambda v:v[0]):
            if len(ids)==1:points=arrays[ids[0]];meta=source[ids[0]] if ids[0]<len(source) else None
            else:
                g=shapely.union_all(geos[ids])
                if g.geom_type!='Polygon':raise ValueError(f'图层 {key} 的连接碎片未形成唯一面')
                points=winding_outline(g)
                meta=dict(kind='polygon',winding=True,nodes=points.copy()) if g.interiors else None
            index=len(result);result.append(points);metadata.append(meta);sources.append(tuple(ids))
            for old in ids:out.raw_to_merged[key,old]=index
        out.layers[key]=result;out.shape_info[key]=metadata;out.merge_sources[key]=sources
        if len(result)<len(arrays):out.merge_notes.append(f'图层 {key}：{len(arrays)} 个源片 Merge 为 {len(result)} 个完整图形（点接触不合并）。')
    out._legacy_snapshot=raw;out.geometry_indexing='merged-v39'
    out.legacy_node_edits=list(getattr(raw,'node_edits',[]));out.node_edits=[]
    out.edit_digest=hashlib.sha256(json.dumps([out.merge_sources,out.legacy_node_edits],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    out._sanitized_token=tuple((k,id(v),len(v)) for k,v in out.layers.items())
    for attr in ('geometry_cache','prepared_case','prepared_auto','prepared_roles','_source_snapshot'):
        out.__dict__.pop(attr,None)
    return out


def restore_project_geometry(data,project):
    """Old fragment edits run before Merge; new whole-object edits run after it."""
    from .gds_shapes import edited_snapshot
    p=copy.deepcopy(project or {})
    if not hasattr(data,'_legacy_snapshot'):
        return edited_snapshot(data,p.get('node_edits',[])),p
    modern=p.get('geometry_indexing')=='merged-v39'
    old=p.get('legacy_node_edits',[]) if modern else p.get('node_edits',[])
    if old:
        raw=edited_snapshot(data._legacy_snapshot,old)
        data=merge_import(raw)
    if not modern:
        def ref(layer,index):return data.raw_to_merged.get((str(layer),int(index)),int(index))
        def vias(rows):
            return tuple(sorted(set((str(k),ref(k,i)) for k,i in rows))) if rows and isinstance(rows[0],(list,tuple)) else rows
        cfg=p.setdefault('settings',{})
        for name in ('reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts','reconstruction_dropped_cuts'):
            cfg[name]=[(l,ref(l,i),vias(v),*tail) for l,i,v,*tail in cfg.get(name,())]
        cfg['reconstruction_intervals']=[(l,ref(l,i),owner,vias(v),identity,a,b) for l,i,owner,v,identity,a,b in cfg.get('reconstruction_intervals',())]
        for name in ('ignored_width_lines','ignored_reconstruction'):
            ignored=p.get(name)
            if ignored:ignored['refs']=sorted({(l,ref(l,i)) for l,i in ignored['refs']})
        p['legacy_node_edits']=old;p['node_edits']=[];p['geometry_indexing']='merged-v39'
        if cfg['reconstruction_intervals'] and data.merge_notes:
            data.import_warnings=list(data.import_warnings)+['工程图形已按完整轮廓合并：旧分区引用已映射；不匹配的旧路径保留记录并重新选区。']
    data=edited_snapshot(data,p.get('node_edits',[]))
    return data,p
