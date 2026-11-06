"""Export the current physical polygons verbatim; never export diagnostic artwork."""
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import tempfile
import numpy as np
import shapely
from . import dxf_document as doc


@dataclass(frozen=True)
class Snapshot:
    layers: dict
    top_name: str
    states: object
    ignored_count: int


def snapshot(data,cache,frame=None,*,deformation=False,reconstruction=False,repairs=None,progress=lambda *_:None):
    """Capture all layers, including hidden/unused ones, at one completed pose."""
    replacements={}
    if frame:
        if deformation:replacements=frame.get('deformation',{}).get('replacements',{})
        if reconstruction:
            from .fast_preview import PreviewEngine
            replacements=PreviewEngine.final_copper(replacements,frame.get('reconstruction',{}).get('replacements',{}))
    layers={};total=max(1,sum(map(len,data.layers.values())));done=0
    for key,values in data.layers.items():
        current=frame.get('layers',{}).get(key,{}).get('polygons',values) if frame else values
        items=replacements.get(key,())
        hidden={i for q in items for i in q['indices']}
        geoms=[]
        for i,xy in enumerate(current):
            if done%256==0:progress(round(100*done/total),'冻结当前几何轮廓')
            done+=1
            if i in hidden:continue
            # Use sanitized geometry including its real holes. Transform by
            # the source outline's rigid displacement without fitting curves.
            g=cache.layers[key]['geometry'][i]
            if g.is_empty:continue
            nominal=np.asarray(values[i]);actual=np.asarray(xy)
            if np.array_equal(nominal,actual):geoms.append(g)
            elif g.geom_type=='Polygon' and not g.interiors:
                geoms.append(shapely.Polygon(actual))
            else:
                # Rigid frame arrays preserve node order. Determine rotation
                # from a nonzero edge, then use the exact frame vertex anchor.
                delta=np.diff(nominal,axis=0);j=int(np.argmax(np.sum(delta*delta,axis=1)))
                a=delta[j];b=actual[j+1]-actual[j];norm=float(a@a)
                if not norm:raise ValueError(f'图层 {key} 图形 {i+1} 无有效边，不能输出')
                co=float(a@b)/norm;si=float(a[0]*b[1]-a[1]*b[0])/norm
                rotation=np.array([[co,si],[-si,co]])
                geoms.append(shapely.transform(g,lambda points:(points-nominal[j])@rotation+actual[j]))
        geoms.extend(q['geometry'] for q in items)
        layers[key]=tuple(g.wkb for g in geoms)
    # Straight patches are extra physical copper; their DRC/range marks are not.
    for repair in (repairs or {}).get('repairs',()):
        if not repair.patch.is_empty:layers[repair.layer]=(*layers.get(repair.layer,()),repair.patch.wkb)
    return Snapshot(layers,data.top_name,None if frame is None else np.array(frame['states'],copy=True),
                    len(getattr(data,'ignored_geometry',())))


class PairWriter:
    def __init__(self,stream):self.stream=stream
    def add(self,code,value):
        if isinstance(value,(float,np.floating)):
            if not math.isfinite(value):raise ValueError('DXF坐标含非有限数')
            value=format(float(value),'.17g')
        self.stream.write(f'{code}\r\n{value}\r\n')
    def extend(self,pairs):
        for code,value in pairs:self.add(code,value)


def _vertices(ring):
    xy=np.asarray(ring.coords,dtype=float)
    if len(xy)>1 and np.array_equal(xy[0],xy[-1]):xy=xy[:-1]
    if len(xy)<3 or not np.isfinite(xy).all():raise ValueError('DXF轮廓节点无效')
    return xy


def _handles(layers,count):
    alloc=doc.HandleAllocator();h={}
    for name in doc.STANDARD_TABLES:h['table_'+name]=alloc.take()
    for key in ('vport_active','ltype_byblock','ltype_bylayer','ltype_continuous','style_standard','appid_acad',
                'dimstyle_standard','block_record_model','block_record_paper','block_model','endblk_model',
                'block_paper','endblk_paper','root_dictionary','group_dictionary','plotstyle_dictionary','plot_normal'):
        h[key]=alloc.take()
    for layer in layers:layer['handle']=alloc.take()
    entities=[alloc.take() for _ in range(count)]
    h['handseed']=alloc.handseed()
    return h,entities


def _verify(path,expected,extents):
    """Streaming record/node audit before replacing an existing user file."""
    count=0;nodes=0;first=[];last=None
    with open(path,'rb') as f:
        while True:
            a=f.readline()
            if not a:break
            b=f.readline()
            if not b or not a.endswith(b'\r\n') or not b.endswith(b'\r\n'):raise ValueError('DXF记录或换行不完整')
            code=int(a);value=b[:-2].decode('ascii');last=(code,value)
            if code==0 and value in ('LWPOLYLINE','HATCH'):count+=1
            if code==10:nodes+=1
            if len(first)<8:first.append((code,value))
    if last!=(0,'EOF') or count!=expected or not nodes or (9,'$ACADVER') not in first:raise ValueError('DXF输出回读校验失败')
    return count


def write(snapshot,path,progress=lambda *_:None):
    path=Path(path)
    if not path.parent.is_dir():raise ValueError('输出目录不存在')
    progress(0,'准备当前几何DXF')
    polygons=[];names=set();extent=np.array([math.inf,math.inf,-math.inf,-math.inf])
    keys=sorted(snapshot.layers,key=lambda key:tuple(map(int,key.split('/'))))
    for j,key in enumerate(keys):
        layer,datatype=map(int,key.split('/'));name=f'L{layer}_D{datatype}';names.add(name)
        for wkb in snapshot.layers[key]:
            g=shapely.from_wkb(wkb)
            if not g.is_valid:raise ValueError(f'图层 {key} 含无效输出几何')
            for p in shapely.get_parts(g):
                if p.is_empty:continue
                if p.geom_type!='Polygon':raise ValueError(f'图层 {key} 含非面输出几何')
                rings=[_vertices(p.exterior),*[_vertices(r) for r in p.interiors]]
                bounds=np.asarray(p.bounds);extent[:2]=np.minimum(extent[:2],bounds[:2]);extent[2:]=np.maximum(extent[2:],bounds[2:])
                polygons.append((name,p,rings))
        progress(round(15*(j+1)/max(1,len(keys))),'展开输出轮廓')
    if not polygons:raise ValueError('当前没有可导出的有效图形')
    names.add('0');layers=[dict(encoded_name=n,color=7) for n in sorted(names)]
    handles,entity_handles=_handles(layers,len(polygons))
    fd,temp=tempfile.mkstemp(prefix='.die-shift-',suffix='.dxf',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='ascii',newline='') as f:
            writer=PairWriter(f)
            doc._write_header(writer,handles,extent,0)
            doc._write_classes(writer);doc._write_tables(writer,handles,layers,extent);doc._write_blocks(writer,handles)
            writer.extend(((0,'SECTION'),(2,'ENTITIES')))
            for i,((name,p,rings),handle) in enumerate(zip(polygons,entity_handles)):
                if len(rings)==1:doc._write_outline(writer,dict(vertices=rings[0]),name,handle,handles['block_record_model'])
                else:
                    # Straight edge loops retain holes without bridges or fitting.
                    hatch=dict(handle=handle,encoded_layer=name,seed=p.representative_point().coords[0],
                               loops=[dict(vertices=xy,flags=1 if j==0 else 0) for j,xy in enumerate(rings)])
                    doc._write_hatch(writer,hatch,handles['block_record_model'])
                if i%64==0:progress(15+round(70*i/len(polygons)),'写出DXF直线轮廓')
            writer.add(0,'ENDSEC');doc._write_objects(writer,handles);f.flush();os.fsync(f.fileno())
        progress(90,'核对DXF实体与完整性')
        _verify(temp,len(polygons),extent)
        digest=hashlib.sha256(Path(temp).read_bytes()).hexdigest()
        progress(99,'保存已校验的DXF')
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    progress(100,'DXF输出完成')
    return dict(path=str(path),polygons=len(polygons),layers=len(keys),sha256=digest,ignored=snapshot.ignored_count)
