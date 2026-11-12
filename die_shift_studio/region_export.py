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
    if g.is_empty:return []
    if g.geom_type in ('LineString','LinearRing'):return [shapely.LineString(g.coords)]
    return [p for q in shapely.get_parts(g) if q is not g for p in line_parts(q)]


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
        for polygon in polygons:
            for hole,ring in enumerate([polygon.exterior,*polygon.interiors]):
                boundary=shapely.LineString(ring.coords);remaining=boundary;assigned=[]
                if roles.get(key)=='固定RDL':
                    # Exact line overlay keeps all source/intersection nodes. The
                    # residual is B, including independent fixed networks.
                    for kind in 'AC':
                        piece=remaining.intersection(masks[kind])
                        if not piece.is_empty:assigned.append((kind,piece));remaining=remaining.difference(piece)
                    if not remaining.is_empty:assigned.append(('B',remaining))
                else:assigned=[('',boundary)]
                rebuilt=shapely.union_all([g for _,g in assigned])
                if rebuilt.symmetric_difference(boundary).length>max(1e-8,boundary.length*1e-9):
                    raise ValueError(f'图层 {key} 动静区轮廓无法闭合，未写入目标文件')
                for kind,g in assigned:
                    name=base+kind+('_holes' if hole and options.separate_holes else '')
                    lines=line_parts(shapely.line_merge(g) if g.geom_type=='MultiLineString' else g)
                    for line in lines:
                        xy=np.asarray(line.coords)*options.scale
                        if len(xy)>=2 and np.isfinite(xy).all():shapes.append((name,xy))
        progress(round(65*(n+1)/total),f'拆分真实轮廓：{key}')
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
                input_polygons=inputs,merged_polygons=merged,flattened=True,merge_layers=options.merge_layers,scale=options.scale,regions=True)
