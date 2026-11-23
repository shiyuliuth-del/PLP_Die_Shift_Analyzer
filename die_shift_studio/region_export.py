"""Export only physical exterior/hole contours, classified A/B/C at the seams.

Internal partition faces are never entities. Open polylines end at identical
join coordinates, and their union must equal the final physical boundary.
"""
import hashlib
import math
import os
from pathlib import Path
import tempfile
from collections import defaultdict
from typing import NamedTuple
from .resources import cancel_check,parallel_iter,resource_profile,geometry_budget
import numpy as np
import shapely
from .dxf_export import PairWriter,_handles,_verify
from . import dxf_document as doc
from .region_contract import (EXPORT_ROLES, PUBLIC_KIND, REFERENCE_LAYERS,
                              overlapping_edges, verify_written_contours)


def line_parts(g):
    # get_parts(Point) returns another Point object, not the same Python
    # instance. Identity-based recursion therefore never terminates at touches.
    pending=[g];out=[]
    while pending:
        item=pending.pop()
        if item is None or item.is_empty:continue
        if item.geom_type in ('LineString','LinearRing'):
            if item.length>0:out.append(shapely.LineString(item.coords))
        elif item.geom_type in ('MultiLineString','GeometryCollection'):
            pending.extend(item.geoms)
    return out


def joined_coordinates(lines):
    """Use one float coordinate for each shared seam node and keep paths open."""
    if not lines:return []
    largest=max(1.,max(float(np.max(abs(xy))) for _,xy in lines))
    epsilon=max(1e-10,float(np.spacing(largest))*8)
    pool={};exact={};out=[]
    def node(p):
        raw=(float(p[0]),float(p[1]));previous=exact.get(raw)
        if previous is not None:return previous
        x,y=raw;cx,cy=math.floor(x/epsilon),math.floor(y/epsilon)
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for q in pool.get((cx+dx,cy+dy),()):
                    if abs(q[0]-x)<=epsilon and abs(q[1]-y)<=epsilon:exact[raw]=q;return q
        pool.setdefault((cx,cy),[]).append(raw);exact[raw]=raw;return raw
    for name,points in lines:
        xy=np.asarray([node(p) for p in points])
        xy=xy[np.r_[True,np.any(xy[1:]!=xy[:-1],axis=1)]]
        if len(xy)<2:continue
        if np.array_equal(xy[0],xy[-1]):
            middle=(len(xy)-1)//2
            if middle>0:out.extend(((name,xy[:middle+1]),(name,xy[middle:])))
        else:out.append((name,xy))
    return out


def partition_ring(ring,masks):
    """Partition original edges parametrically; never subtract rounded lines.

    GEOS line difference can lose a long, almost-collinear boundary when the
    polygon overlay rounds one endpoint by a few ULPs. All pieces below reuse
    points on the very same source edge, so A/B/C joins are identical by design.
    """
    xy=np.asarray(ring.coords)
    if masks['A'].is_empty and masks['C'].is_empty:
        keep=np.r_[True,np.any(xy[1:]!=xy[:-1],axis=1)]
        pts=xy[keep]
        return [('B',pts)] if len(pts)>=2 else []
    vectors=np.diff(xy,axis=0);length2=np.sum(vectors*vectors,axis=1)
    good=length2>0;starts=xy[:-1][good];ends=xy[1:][good];vectors=vectors[good];length2=length2[good]
    segments=shapely.linestrings(np.stack([starts,ends],axis=1));breaks=[[0.,1.] for _ in segments]
    epsilon=max(1e-10,np.spacing(max(1.,float(np.max(abs(xy)))))*8)
    for kind in 'AC':
        mask=masks[kind]
        if mask.is_empty:continue
        shapely.prepare(mask)
        hit=shapely.intersects(segments,mask)
        if not np.any(hit):continue
        hit_idx=np.flatnonzero(hit)
        # Segments fully covered by mask never cross its boundary and need no split.
        partial=hit_idx[~shapely.covers(mask,segments[hit_idx])]
        if not len(partial):continue
        intersections=shapely.intersection(segments[partial],mask)
        for i,g in zip(partial,intersections):
            points=shapely.get_coordinates(g)
            values=np.clip((points-starts[i])@vectors[i]/length2[i],0,1)
            edge_epsilon=epsilon/np.sqrt(length2[i])
            values[values<edge_epsilon]=0.;values[values>1-edge_epsilon]=1.
            breaks[i].extend(values.tolist())
    records=[];centers=[]
    for p,q,v,ts in zip(starts,ends,vectors,breaks):
        ts=np.unique(ts);pts=p+ts[:,None]*v;pts[0]=p;pts[-1]=q
        for a,b in zip(pts[:-1],pts[1:]):
            if np.array_equal(a,b):continue
            records.append((a,b));centers.append((a+b)/2)
    points=shapely.points(centers);kinds=np.full(len(records),'B')
    epsilon=max(1e-10,np.spacing(max(1.,float(np.max(abs(xy)))))*8)
    for kind in 'CA':
        if not masks[kind].is_empty:
            shapely.prepare(masks[kind])
            covered=shapely.covers(masks[kind],points)
            near=~covered
            if np.any(near):covered[near]=shapely.dwithin(masks[kind],points[near],epsilon)
            kinds[covered]=kind
    groups=[]
    for kind,(a,b) in zip(kinds,records):
        if groups and groups[-1][0]==kind and np.array_equal(groups[-1][1][-1],a):groups[-1][1].append(b)
        else:groups.append((str(kind),[a,b]))
    if len(groups)>1 and groups[0][0]==groups[-1][0] and np.array_equal(groups[-1][1][-1],groups[0][1][0]):
        groups[0]=(groups[0][0],groups[-1][1]+groups[0][1][1:]);groups.pop()
    output=[(kind,np.asarray(points)) for kind,points in groups]
    # A ring has been partitioned, not offset, simplified or artificially capped.
    total=sum(float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum()) for _,points in output)
    if abs(total-float(ring.length))>max(1e-8,float(ring.length)*1e-10):
        raise ValueError('动静区轮廓长度不守恒，未写入目标文件')
    return output


class ContourGroup(NamedTuple):
    layer: str
    paths: list[np.ndarray]
    closed: bool = False


def layer_contours(task):
    key,raw,rows,role,options=task;cancel_check()
    polygons=[p for blob in raw for p in shapely.get_parts(shapely.from_wkb(blob)) if not p.is_empty]
    if any(p.geom_type!='Polygon' or not p.is_valid for p in polygons):raise ValueError(f'图层 {key} 的输出几何无效')
    inputs=len(polygons)
    if role in REFERENCE_LAYERS:
        return [ContourGroup(REFERENCE_LAYERS[role],
                             [np.asarray(ring.coords) * options.scale for ring in (p.exterior, *p.interiors)],
                             True) for p in polygons], inputs, inputs

    polygons=list(shapely.get_parts(shapely.union_all(polygons)))
    base='L'+key.replace('/','_D');groups=[]
    masks={k:shapely.union_all([q['regions'][k] for q in rows]) for k in 'AC'}
    trees={kind:shapely.STRtree(shapely.get_parts(g)) for kind,g in masks.items()}
    for polygon in polygons:
        cancel_check()
        local={kind:shapely.union_all(tree.geometries[tree.query(polygon,predicate='intersects')]) for kind,tree in trees.items()}
        assigned=[]
        for ring in (polygon.exterior,*polygon.interiors):
            assigned.extend((kind,xy * options.scale) for kind,xy in partition_ring(ring,local))
        by_layer=defaultdict(list)
        degrees=defaultdict(int)
        for kind,xy in joined_coordinates(assigned):
            by_layer[base+PUBLIC_KIND[kind]].append(xy)
            degrees[tuple(xy[0])]+=1
            degrees[tuple(xy[-1])]+=1
        if any(degree % 2 for degree in degrees.values()):
            raise ValueError(f'图层 {key} 的ABC源轮廓未闭合')
        groups.extend(ContourGroup(name,paths) for name,paths in sorted(by_layer.items()))
    return groups,inputs,len(polygons)


def _exclusive_outlines(groups):
    """Preserve the union of outlines, emitting each coincident interval once."""
    groups=sorted(groups,key=lambda q:not q.closed)
    # Deduplicate identical full reference loops without merging or splitting them.
    records=[];seen=defaultdict(list)
    for group_index,group in enumerate(groups):
        for path in group.paths:
            if group.closed:
                ring=shapely.LinearRing(path)
                key=group.layer,ring.bounds
                if any(ring.equals(previous) for previous in seen[key]):continue
                seen[key].append(ring)
            records.append((group_index,path))
    if not records:return [],set()
    sizes=np.array([len(path)-1 for _,path in records])
    offsets=np.r_[0,np.cumsum(sizes)]
    edges=shapely.linestrings(np.concatenate([np.stack((p[:-1],p[1:]),axis=1) for _,p in records]))
    owners=np.repeat(np.array([i for i,_ in records]),sizes)
    removed=defaultdict(list)
    for _,edge,overlap in overlapping_edges(edges):
        if groups[owners[edge]].closed:
            raise ValueError('参考边框之间有重叠，无法同时保持完整独立闭合和无重叠')
        removed[edge].append(overlap)
    paths=defaultdict(list);shared=set()
    for n,(owner,path) in enumerate(records):
        cut_edges=[j for j in range(offsets[n],offsets[n+1]) if j in removed]
        if not cut_edges:
            paths[owner].append(path)
            continue
        shared.add(groups[owner].layer[:-2])
        start=offsets[n]
        pieces=[]
        for j in cut_edges:
            if j>start:
                pieces.append(path[start-offsets[n]:j-offsets[n]+1])
            edge=edges[j]
            remainder=edge.difference(shapely.union_all(removed[j]))
            intervals=line_parts(remainder)
            for line in sorted(intervals,key=lambda g:min(edge.project(shapely.Point(g.coords[i])) for i in (0,-1))):
                xy=np.asarray(line.coords)
                if edge.project(shapely.Point(xy[0]))>edge.project(shapely.Point(xy[-1])):xy=xy[::-1]
                pieces.append(xy)
            start=j+1
        if start<offsets[n+1]:
            pieces.append(path[start-offsets[n]:])
        joined=[]
        for xy in pieces:
            if joined and np.array_equal(joined[-1][-1],xy[0]):
                joined[-1]=np.concatenate((joined[-1],xy[1:]))
            else:joined.append(xy)
        paths[owner].extend(joined)
    return [group._replace(paths=paths[i]) for i,group in enumerate(groups) if paths[i]],shared


def _join_block_paths(paths):
    """Join endpoint-touching polylines within one block at degree-2 nodes."""
    if len(paths)<2:return list(paths)
    cleaned=[]
    for xy in paths:
        arr=np.asarray(xy,float)
        if len(arr)>=2:cleaned.append(arr)
    if len(cleaned)<2:return cleaned
    adj=defaultdict(list)
    for idx,xy in enumerate(cleaned):
        u,v=tuple(xy[0]),tuple(xy[-1])
        adj[u].append((idx,0))
        if u!=v:adj[v].append((idx,1))
    used=[False]*len(cleaned)
    out=[]
    def walk(start_idx,start_end):
        used[start_idx]=True
        pts=cleaned[start_idx] if start_end==0 else cleaned[start_idx][::-1]
        chain=[pts]
        while True:
            tail=tuple(chain[-1][-1])
            head=tuple(chain[0][0])
            if tail==head:break
            incident=adj[tail]
            if len(incident)!=2:break
            nxt=next((item for item in incident if not used[item[0]]),None)
            if nxt is None:break
            idx,end=nxt
            used[idx]=True
            seg=cleaned[idx] if end==0 else cleaned[idx][::-1]
            chain.append(seg[1:])
        merged=np.concatenate(chain,axis=0)
        merged=merged[np.r_[True,np.any(merged[1:]!=merged[:-1],axis=1)]]
        return merged
    # First walk open chains from every non-degree-2 vertex.
    for node,incident in adj.items():
        if len(incident)==2:continue
        for idx,end in incident:
            if not used[idx]:out.append(walk(idx,end))
    # Then walk any remaining closed degree-2 cycles.
    for idx in range(len(cleaned)):
        if not used[idx]:out.append(walk(idx,0))
    return out


def _prepare(snapshot,reconstruction,roles,options,progress):
    tasks=[]
    for key,raw in sorted(snapshot.layers.items()):
        if roles.get(key) in EXPORT_ROLES:
            tasks.append((key,raw,[p for p in reconstruction.get('pieces',()) if p['layer']==key],roles[key],options))
    profile=resource_profile()
    largest=max((sum(map(len,raw)) for _,raw,*_ in tasks),default=1)
    profile=dict(profile,workers=min(len(tasks) or 1,geometry_budget(profile,largest)))
    groups=[];inputs=merged=0
    for n,(rows,i,m) in enumerate(parallel_iter(layer_contours,tasks,profile)):
        groups.extend(rows);inputs+=i;merged+=m
        if progress:progress(round(55*(n+1)/max(1,len(tasks))),f'拆分真实轮廓：{tasks[n][0]}')
    if progress:progress(56,'去除完全及局部重叠的轮廓线段')
    groups,shared=_exclusive_outlines(groups)
    if getattr(options,'region_auto_join',False):
        groups=[group if group.closed else group._replace(paths=_join_block_paths(group.paths)) for group in groups]
    return groups,shared,inputs,merged


def contours(snapshot,reconstruction,roles,options,progress=lambda *_:None):
    groups,_,inputs,merged=_prepare(snapshot,reconstruction,roles,options,progress)
    return [(q.layer,xy) for q in groups for xy in q.paths],inputs,merged


def write_regions(snapshot,reconstruction,roles,path,progress=lambda *_:None,*,settings=None):
    from .dxf_settings import DxfSettings
    options=settings or DxfSettings();path=Path(path)
    groups,shared,inputs,merged=_prepare(snapshot,reconstruction,roles,options,progress)
    if not groups:raise ValueError('当前没有可导出的有效轮廓')
    blocks=[];references=[];names=set();lo=np.array([np.inf,np.inf]);hi=-lo
    count=0
    for i,(layer,paths,closed) in enumerate(groups):
        names.add(layer)
        for xy in paths:
            lo=np.minimum(lo,xy.min(axis=0));hi=np.maximum(hi,xy.max(axis=0))
            count+=1
        if closed:references.extend((layer,xy[:-1]) for xy in paths)
        else:blocks.append(dict(name=f'RDL_{i+1}',layer=layer,outlines=paths))
    extent=np.r_[lo,hi];layers=[dict(encoded_name=n,color=7) for n in sorted(names|{'0'})]
    handles,entities=_handles(layers,len(references),blocks)
    fd,temp=tempfile.mkstemp(prefix='.die-shift-regions-',suffix='.dxf',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='ascii',newline='') as stream:
            w=PairWriter(stream);doc._write_header(w,handles,extent,0);doc._write_classes(w)
            doc._write_tables(w,handles,layers,extent,blocks);doc._write_blocks(w,handles,blocks)
            w.extend(((0,'SECTION'),(2,'ENTITIES')))
            for (layer,xy),handle in zip(references,entities):
                cancel_check()
                doc._write_outline(w,dict(vertices=xy),layer,handle,handles['block_record_model'])
            for i,block in enumerate(blocks):
                cancel_check()
                doc._write_insert(w,block,handles['block_record_model'])
                if progress and i%128==0:progress(65+round(25*i/len(blocks)),'写出按源图形和图层分组的CAD块')
            w.add(0,'ENDSEC');doc._write_objects(w,handles);stream.flush();os.fsync(stream.fileno())
        _verify(temp,count,extent)
        if progress:progress(92,'回读核对CAD块、闭合与重复轮廓')
        topology=verify_written_contours(temp,expected_blocks=blocks,expected_references=references,shared_layers=shared,allow_closed_blocks=bool(getattr(options,'region_auto_join',False)))
        digest=hashlib.sha256()
        with open(temp,'rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        cancel_check()
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    if progress:progress(100,'动静区DXF输出完成')
    return dict(path=str(path),polygons=count,layers=len(names),sha256=digest.hexdigest(),ignored=snapshot.ignored_count,
                input_polygons=inputs,merged_polygons=merged,flattened=True,merge_layers=True,scale=options.scale,regions=True,topology=topology,
                unresolved_regions=sum(not q.get('valid',True) for q in reconstruction.get('pieces',())))
