"""Export only physical exterior/hole contours, classified A/B/C at the seams.

Internal partition faces are never entities. Open polylines end at identical
join coordinates, and their union must equal the final physical boundary.
"""
import hashlib
import math
import os
from pathlib import Path
import tempfile
import numpy as np
import shapely
from .dxf_export import PairWriter,_handles,_verify
from . import dxf_document as doc


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
    """One float coordinate for every shared seam node; no closing DXF entity."""
    if not lines:return []
    largest=max(1.,max(float(np.max(abs(xy))) for _,xy in lines))
    epsilon=max(1e-10,float(np.spacing(largest))*8)
    pool={};out=[]
    def node(p):
        cell=tuple(np.floor(p/epsilon).astype(np.int64))
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for q in pool.get((cell[0]+dx,cell[1]+dy),()):
                    if np.max(abs(q-p))<=epsilon:return q
        q=p.copy();pool.setdefault(cell,[]).append(q);return q
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
    xy=np.asarray(ring.coords);vectors=np.diff(xy,axis=0);length2=np.sum(vectors*vectors,axis=1)
    good=length2>0;starts=xy[:-1][good];ends=xy[1:][good];vectors=vectors[good];length2=length2[good]
    segments=shapely.linestrings(np.stack([starts,ends],axis=1));breaks=[[0.,1.] for _ in segments]
    epsilon=max(1e-10,np.spacing(max(1.,float(np.max(abs(xy)))))*8)
    for kind in 'AC':
        mask=masks[kind]
        if mask.is_empty:continue
        indices=np.flatnonzero(shapely.intersects(segments,mask))
        intersections=shapely.intersection(segments[indices],mask)
        for i,g in zip(indices,intersections):
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


def contours(snapshot,reconstruction,roles,options,progress=lambda *_:None):
    shapes=[];total=max(1,len(snapshot.layers));inputs=merged=0
    for n,(key,raw) in enumerate(sorted(snapshot.layers.items())):
        polygons=[p for blob in raw for p in shapely.get_parts(shapely.from_wkb(blob)) if not p.is_empty]
        if any(p.geom_type!='Polygon' or not p.is_valid for p in polygons):raise ValueError(f'图层 {key} 的输出几何无效')
        inputs+=len(polygons)
        if options.merge_layers:polygons=list(shapely.get_parts(shapely.union_all(polygons)))
        merged+=len(polygons);base='L'+key.replace('/','_D')
        rows=[p for p in reconstruction.get('pieces',()) if p['layer']==key]
        masks={k:shapely.union_all([q['regions'][k] for q in rows]) for k in 'AC'}
        mask_trees={kind:shapely.STRtree(shapely.get_parts(g)) for kind,g in masks.items()} if roles.get(key)=='固定RDL' else {}
        for polygon in polygons:
            local_masks={kind:shapely.union_all(tree.geometries[tree.query(polygon,predicate='intersects')]) for kind,tree in mask_trees.items()}
            for hole,ring in enumerate([polygon.exterior,*polygon.interiors]):
                if roles.get(key)=='固定RDL':assigned=partition_ring(ring,local_masks)
                else:assigned=[('',np.asarray(ring.coords))]
                for kind,xy in assigned:
                    name=base+kind+('_holes' if hole and options.separate_holes else '')
                    xy=xy*options.scale
                    if len(xy)>=2 and np.isfinite(xy).all():shapes.append((name,xy))
        progress(round(65*(n+1)/total),f'拆分真实轮廓：{key}')
    shapes=joined_coordinates(shapes)
    return shapes,inputs,merged


def write_regions(snapshot,reconstruction,roles,path,progress=lambda *_:None,*,settings=None):
    from .dxf_settings import DxfSettings
    options=settings or DxfSettings();path=Path(path)
    shapes,inputs,merged=contours(snapshot,reconstruction,roles,options,progress)
    if not shapes:raise ValueError('当前没有可导出的有效轮廓')
    xy=np.concatenate([a for _,a in shapes]);extent=np.r_[xy.min(axis=0),xy.max(axis=0)]
    names={name for name,_ in shapes};layers=[dict(encoded_name=n,color=7) for n in sorted(names|{'0'})]
    handles,entities=_handles(layers,len(shapes))
    fd,temp=tempfile.mkstemp(prefix='.die-shift-regions-',suffix='.dxf',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='ascii',newline='') as stream:
            w=PairWriter(stream);doc._write_header(w,handles,extent,0);doc._write_classes(w)
            doc._write_tables(w,handles,layers,extent);doc._write_blocks(w,handles)
            w.extend(((0,'SECTION'),(2,'ENTITIES')))
            for i,((name,xy),handle) in enumerate(zip(shapes,entities)):
                doc._write_outline(w,dict(vertices=xy,closed=False),name,handle,handles['block_record_model'])
                if i%128==0:progress(65+round(25*i/len(shapes)),'写出开放的 A/B/C 轮廓')
            w.add(0,'ENDSEC');doc._write_objects(w,handles);stream.flush();os.fsync(stream.fileno())
        _verify(temp,len(shapes),extent);digest=hashlib.sha256(Path(temp).read_bytes()).hexdigest()
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    progress(100,'动静区DXF输出完成')
    return dict(path=str(path),polygons=len(shapes),layers=len(names),sha256=digest,ignored=snapshot.ignored_count,
                input_polygons=inputs,merged_polygons=merged,flattened=True,merge_layers=options.merge_layers,scale=options.scale,regions=True,
                unresolved_regions=sum(not q.get('valid',True) for q in reconstruction.get('pieces',())))
