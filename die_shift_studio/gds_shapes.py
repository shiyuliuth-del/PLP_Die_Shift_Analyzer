"""Import PATHs before polygonisation and edit the nominal geometry of a Job."""
import copy
import hashlib
import json
import math
from collections import Counter,defaultdict,deque
import gdstk
import numpy as np
from shapely.geometry import Polygon


def outline(nodes):
    xy=np.asarray(nodes,dtype=float)
    if len(xy)>1 and np.array_equal(xy[0],xy[-1]):
        xy=xy[:-1]
    return xy


def circle(center,width,dbu):
    # Inscribed regular polygon. Cardinal vertices give the exact diameter.
    radius=width/2
    error=min(radius*.001,max(dbu/2,1.e-9))
    angle=math.acos(max(-1.,min(1.,1-error/radius)))
    n=max(32,4*math.ceil(math.pi/max(angle,1.e-12)/4))
    if n>500_000:
        raise ValueError('单点 PATH 圆轮廓超过 500000 个节点')
    t=np.arange(n)*(2*math.pi/n)
    return np.asarray(center)+radius*np.column_stack((np.cos(t),np.sin(t)))


def import_shapes(cell,dbu):
    layers,info={},{}
    warnings=Counter();notes=[];ignored=[]
    def add(key,points,source):
        layers.setdefault(key,[]).append(np.asarray(points,float))
        info.setdefault(key,[]).append(source)
    # Keep gdstk's original mixed polygon/PATH encounter order. A private
    # in-memory tag connects each flattened outline to its native centreline.
    tag='DSA_IMPORT_PATH_ID'
    definitions=[cell,*cell.dependencies(True)]
    tracks=[];zero_counts={}
    for definition in definitions:
        local=Counter()
        for path in definition.paths:
            if not np.any(path.widths()):
                local[f'{path.layers[0]}/{path.datatypes[0]}']+=max(1,path.repetition.size)
                definition.remove(path)
            else:
                path.set_property(tag,len(tracks));tracks.append(path)
        zero_counts[definition.name]=local
    memo={}
    def expanded(definition):
        if definition.name not in memo:
            counts=zero_counts[definition.name].copy()
            for ref in definition.references:
                for k,n in expanded(ref.cell).items():counts[k]+=n*max(1,ref.repetition.size)
            memo[definition.name]=counts
        return memo[definition.name]
    warnings.update(expanded(cell))
    by_id=defaultdict(deque)
    for path in cell.get_paths(apply_repetitions=True,depth=None):
        key=f'{path.layers[0]}/{path.datatypes[0]}'
        width=abs(float(path.widths()[0,0]))
        if width==0:
            warnings[key]+=1
            continue
        xy=np.asarray(path.spine(),float)
        xy=xy[np.r_[True,np.any(np.diff(xy,axis=0)!=0,axis=1)]]
        meta=dict(nodes=xy,width=width,end=path.ends[0])
        geometry=None
        if len(xy)==1:geometry=circle(xy[0],width,dbu)
        elif not path.scale_width:
            # An absolute (negative GDS WIDTH) is independent of instance MAG.
            polygons=path.to_polygons()
            if polygons:geometry=polygons[0].points
        by_id[path.get_property(tag)[0]].append((key,meta,geometry))
    for path in tracks:
        width=abs(float(path.widths()[0,0]))
        xy=path.spine()
        if np.all(xy==xy[0]):
            # Native polygonisation of a degenerate centreline can produce
            # no area. This temporary segment is only an ordering token;
            # its polygon is always replaced by the saved transformed disk.
            path.segment(xy[0]+[max(dbu,width),0.])
    for g in cell.get_polygons(apply_repetitions=True,include_paths=True,depth=None):
        key=f'{g.layer}/{g.datatype}'
        tag_value=g.get_property(tag)
        if tag_value and by_id[tag_value[0]]:
            _,meta,corrected=by_id[tag_value[0]].popleft()
            add(key,g.points if corrected is None else corrected,meta)
        else:
            add(key,g.points,None)
    for remaining in by_id.values():
        for key,meta,corrected in remaining:
            try:
                points=corrected if corrected is not None else build_outline(meta['nodes'],meta,dbu)
                add(key,points,meta)
            except (ValueError,RuntimeError) as exc:
                xy=meta['nodes'];radius=meta['width']/2
                layers.setdefault(key,[]);info.setdefault(key,[])
                reason=f'PATH 轮廓无法恢复 · {exc}'
                ignored.append(dict(layer=key,index=len(layers[key])+1,reason=reason,points=xy,
                                    bounds=[*(xy.min(axis=0)-radius),*(xy.max(axis=0)+radius)]))
                notes.append(f'图层 {key} · {reason}')
    return layers,info,[f'图层 {key} · 已忽略 {n} 个零宽 PATH（含实例展开）。' for key,n in warnings.items()]+notes,ignored


def shape_nodes(data,key,index):
    meta=data.shape_info.get(key,[])
    source=meta[index] if index<len(meta) else None
    if source is not None and source.get('kind')=='polygon':return '多边形',outline(data.layers[key][index]),source
    if source is not None:
        return ('单点 PATH 圆' if len(source['nodes'])==1 else 'PATH'),np.asarray(source['nodes']),source
    xy=outline(data.layers[key][index])
    is_box=len(xy)==4 and Polygon(xy).equals(Polygon(xy).minimum_rotated_rectangle)
    return ('矩形' if is_box else '多边形'),xy,None


def node_fingerprint(kind,xy):
    digest=hashlib.sha256(kind.encode())
    digest.update(np.ascontiguousarray(xy,dtype='<f8'))
    return digest.hexdigest()


def build_outline(points,source,dbu):
    xy=np.asarray(points,float)
    if xy.ndim!=2 or xy.shape[1]!=2 or not 1<=len(xy)<=500_000 or not np.isfinite(xy).all() or np.max(np.abs(xy))>1.e9:
        raise ValueError('节点坐标必须是有限的 X、Y（μm），范围 ±1e9')
    if np.any(np.all(np.diff(xy,axis=0)==0,axis=1)):
        raise ValueError('相邻节点不能重合')
    if source and source.get('kind')!='polygon':
        if len(xy)==1:
            boundary=circle(xy[0],source['width'],dbu)
        else:
            path=gdstk.FlexPath(xy,source['width'],ends=source['end'],tolerance=max(dbu/2,1.e-9))
            polygons=path.to_polygons()
            if len(polygons)!=1:
                raise ValueError('PATH 未生成唯一有效的轮廓')
            boundary=polygons[0].points
    else:
        boundary=outline(xy)
        if len(boundary)<3:
            raise ValueError('多边形至少需要三个不同的节点')
    from .gds_merge import filled_polygon
    filled_polygon(boundary,bool(source and source.get('winding')))
    return np.asarray(boundary,float)


def edited_snapshot(data,edits):
    """Stage every edit against original flattened indices and publish atomically."""
    base=getattr(data,'_source_snapshot',data)
    if not edits:
        return base
    updated=copy.copy(base)
    updated._source_snapshot=base
    updated.layers=dict(base.layers)
    updated.shape_info=dict(base.shape_info)
    updated.node_edits=[]
    changed=set()
    for entry in edits:
        key,index=entry['layer'],entry['index']
        if type(index) is not int or index<0 or key not in base.layers or index>=len(base.layers[key]) or (key,index) in changed:
            raise ValueError(f'节点编辑引用无效或重复的图形 {key} #{index}')
        kind,original,source=shape_nodes(base,key,index)
        if node_fingerprint(kind,original)!=entry['original']:
            raise ValueError(f'{key} #{index+1} 的源轮廓已改变，无法应用工程节点')
        xy=np.asarray(entry['nodes'],float)
        try:
            polygon=build_outline(xy,source,base.dbu_um)
        except ValueError as exc:
            raise ValueError(f'{key} #{index+1} · {exc}') from exc
        if updated.layers[key] is base.layers[key]:
            updated.layers[key]=list(base.layers[key])
            updated.shape_info[key]=list(base.shape_info.get(key,[None]*len(base.layers[key])))
        updated.layers[key][index]=polygon
        if source:
            updated.shape_info[key][index]=dict(source,nodes=xy.copy())
        changed.add((key,index))
        updated.node_edits.append(dict(layer=key,index=index,original=entry['original'],nodes=xy.tolist()))
    updated.node_edits.sort(key=lambda e:(e['layer'],e['index']))
    updated.edit_digest=hashlib.sha256(json.dumps([base.edit_digest,updated.node_edits],separators=(',',':'),allow_nan=False).encode()).hexdigest()
    for attr in ('geometry_cache','prepared_case','prepared_auto','prepared_roles'):
        updated.__dict__.pop(attr,None)
    updated._sanitized_token=tuple((k,id(v),len(v)) for k,v in updated.layers.items())
    return updated
