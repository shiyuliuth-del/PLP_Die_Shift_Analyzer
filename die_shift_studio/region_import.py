"""Recover editable nominal cuts from the physical A/B/C DXF outlines."""
from collections import Counter,defaultdict
from dataclasses import asdict,replace
from hashlib import sha256
import base64
import json
import math
import re
import zlib

import numpy as np
import shapely
from shapely.ops import split

from .region_contract import _records
from .resources import cancel_check


LAYER=re.compile(r'L(\d+)_D(\d+)_([ABC])',re.IGNORECASE)
UNIT_UM={0:1.,1:25400.,2:304800.,4:1000.,5:10000.,6:1e6,9:25.4,12:.001,13:1.,14:1e5}
COMMENT='DIE_SHIFT_REGION_V1 '
MAX_METADATA=8*1024**2


def outline_id(contours):
    records=[]
    for layer,kind,xy in contours:
        xy=np.array(xy,dtype='<f8',copy=True)
        xy[xy==0]=0.
        records.append((layer,kind,shapely.to_wkb(shapely.normalize(shapely.LineString(xy)),byte_order=1)))
    digest=sha256()
    for layer,kind,wkb in sorted(records):
        digest.update(f'{layer}_{kind}:'.encode());digest.update(wkb)
    return digest.hexdigest()


def edit_fields():
    from .edit_history import RECONSTRUCTION_FIELDS
    return tuple(k for k in RECONSTRUCTION_FIELDS if k!='reconstruction_fixed_via_follows')


def context_id(data,roles,order,parameters,settings):
    from .session_cache import logical_fingerprint
    empty=replace(settings,**dict.fromkeys(edit_fields(),()))
    return logical_fingerprint((data.geometry_id,data.top_name,roles,order,asdict(parameters),empty.reconstruction_key()))


def prepared_outline_id(groups):
    shapes=[]
    for group in groups:
        match=LAYER.fullmatch(group.layer)
        if match:
            shapes.extend((f'{match[1]}/{match[2]}',match[3],xy) for xy in group.paths)
    return outline_id(shapes)


def write_metadata(writer,groups,metadata):
    """DXF comments carry only native region edits; exported curves stay unchanged."""
    value=dict(metadata,outline=prepared_outline_id(groups))
    payload=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
    if len(payload)>MAX_METADATA:return
    data=base64.b64encode(zlib.compress(payload)).decode('ascii')
    for i in range(0,len(data),180):writer.add(999,COMMENT+data[i:i+180])


def metadata_for(data,roles,order,parameters,settings,layers,dxf,compensation):
    from .region_contract import REFERENCE_LAYERS
    references=tuple(sorted(k for k in layers if roles.get(k) in REFERENCE_LAYERS))
    layers=tuple(sorted(k for k in layers if roles.get(k)=='固定RDL'))
    edits={k:tuple(row for row in getattr(settings,k) if row[0] in layers) for k in edit_fields()}
    return dict(context=context_id(data,roles,order,parameters,settings),layers=layers,edits=edits,
                references=references,dxf=asdict(dxf),compensation=compensation)


def recover_metadata(record,contours,data,roles,order,parameters,settings):
    if not isinstance(record,dict) or record.get('context')!=context_id(data,roles,order,parameters,settings):return None
    layers=set(contours)
    saved_layers=record.get('layers',())
    if not isinstance(saved_layers,list) or any(not isinstance(k,str) for k in saved_layers) or set(saved_layers)!=layers:return None
    if record.get('outline')!=outline_id((layer,k,line.coords) for layer,groups in contours.items() for k,parts in groups.items() for line in parts):
        return None
    from .settings import ViewSettings
    edits=record.get('edits')
    if not isinstance(edits,dict) or set(edits)!=set(edit_fields()):return None
    if any(not isinstance(rows,list) or any(not isinstance(row,list) or not row or not isinstance(row[0],str) or row[0] not in layers for row in rows)
           for rows in edits.values()):return None
    return ViewSettings.from_dict(dict(asdict(settings),**{
        k:(*[row for row in getattr(settings,k) if row[0] not in layers],*rows) for k,rows in edits.items()}))


def validate_rebuild(model,parameters,settings,contours,dxf,compensation,progress=None,*,references=None):
    from .dxf_export import snapshot
    from .region_contract import REFERENCE_LAYERS
    from .region_export import _prepare
    from .reconstruction import Reconstructor
    from .resources import progress_slice

    r=Reconstructor(model,parameters,settings)
    try:
        frame=r.frame(np.zeros((len(model.case['dies']),3)),progress=progress_slice(progress,0,.7))
    finally:r.close()
    nominal=dict(frame,pieces=[dict(q,regions=q['nominal_regions']) for q in frame['pieces']])
    source=snapshot(model.data,model.cache,progress=progress_slice(progress,70,.1))
    reference_keys={k for k in source.layers if model.roles.get(k) in REFERENCE_LAYERS}
    if references is not None:
        if not isinstance(references,list) or any(not isinstance(k,str) or k not in reference_keys for k in references):
            raise ValueError('DXF 记录的参考层不匹配')
        reference_keys.intersection_update(references)
    source=replace(source,layers={k:v for k,v in source.layers.items() if k in contours or k in reference_keys})
    groups,_,_,_=_prepare(source,nominal,model.roles,dxf,progress_slice(progress,80,.2),compensation,model.data.dbu_um)
    return prepared_outline_id(groups)==outline_id((layer,k,line.coords) for layer,groups in contours.items() for k,parts in groups.items() for line in parts)


def read(path,*,with_metadata=False):
    """Read straight 2D entities and expand the file's referenced CAD blocks."""
    blocks={};entities=[];target=None;section='';unit=13;finished=False;encoded=[];encoded_size=0
    for kind,fields in _records(path):
        cancel_check()
        attrs=dict(fields)
        if kind=='SECTION':
            section=attrs[2]
            if section=='HEADER':
                for i,(code,value) in enumerate(fields[:-1]):
                    if code==9 and value=='$INSUNITS':unit=int(fields[i+1][1])
        elif kind=='ENDSEC':section='';target=None
        elif kind=='EOF':finished=True
        elif section=='BLOCKS':
            if kind=='BLOCK':
                name=attrs[2].upper()
                if name in blocks:raise ValueError('DXF 含重复的块名称')
                origin=(float(attrs.get(10,0.)),float(attrs.get(20,0.)))
                target=[];blocks[name]=origin,target
            elif kind=='ENDBLK':target=None
            elif target is not None:target.append((kind,fields))
        elif section=='ENTITIES':entities.append((kind,fields))
        if with_metadata:
            for code,value in fields:
                if code==999 and value.startswith(COMMENT):
                    encoded_size+=len(value)-len(COMMENT)
                    if encoded_size>MAX_METADATA*2:raise ValueError('DXF 动静区记录超过允许大小')
                    encoded.append(value[len(COMMENT):])
    if not finished or unit not in UNIT_UM:raise ValueError('DXF 不完整或图纸长度单位不受支持')
    output=defaultdict(lambda:defaultdict(list))

    def expand(rows,matrix,offset,layer='',parents=()):
        for kind,fields in rows:
            cancel_check()
            attrs=dict(fields);name=attrs.get(8,'0')
            if name=='0':name=layer
            if kind=='INSERT':
                block=attrs[2].upper()
                if block not in blocks or block in parents or len(parents)>=16:
                    raise ValueError('DXF 缺少块定义或存在递归引用')
                if any(int(attrs.get(code,1))!=1 for code in (70,71)) or any(float(attrs.get(c,d))!=d for c,d in ((210,0.),(220,0.),(230,1.))):
                    raise ValueError('动静区输入只支持平面单块引用')
                origin,parts=blocks[block]
                dx,dy=float(attrs.get(10,0.)),float(attrs.get(20,0.))
                sx,sy=float(attrs.get(41,1.)),float(attrs.get(42,1.))
                angle=math.radians(float(attrs.get(50,0.)));c,s=math.cos(angle),math.sin(angle)
                local=np.array(((c*sx,-s*sy),(s*sx,c*sy)))
                expand(parts,matrix@local,matrix@(np.array((dx,dy))-local@origin)+offset,name,(*parents,block))
                continue
            match=LAYER.fullmatch(name)
            if not match:continue
            if kind=='LINE':xy=np.array(((float(attrs[10]),float(attrs[20])),(float(attrs[11]),float(attrs[21]))))
            elif kind=='LWPOLYLINE':
                if any(code==42 and float(value) for code,value in fields):
                    raise ValueError(f'{name} 的轮廓须为直线段')
                xy=np.column_stack(([float(v) for c,v in fields if c==10],[float(v) for c,v in fields if c==20]))
                if int(attrs.get(90,len(xy)))!=len(xy):raise ValueError(f'{name} 的节点数不匹配')
                if int(attrs.get(70,0))&1:xy=np.vstack((xy,xy[:1]))
            else:raise ValueError(f'{name} 含不支持的 DXF 实体 {kind}')
            if len(xy)<2 or not np.isfinite(xy).all() or np.max(abs(xy))>1e12:
                raise ValueError(f'{name} 含无效坐标')
            line=shapely.LineString(xy@matrix.T+offset)
            if line.is_empty or not line.is_valid or not math.isfinite(line.length):raise ValueError(f'{name} 含无效轮廓')
            if line.length>0:output[f'{int(match[1])}/{int(match[2])}'][match[3].upper()].append(line)

    expand(entities,np.eye(2)*UNIT_UM[unit],np.zeros(2))
    if not output:raise ValueError('DXF 中未找到 L层号_D数据类型_A / _B / _C 图层')
    if not with_metadata:return output
    record=None
    if encoded:
        try:
            decoder=zlib.decompressobj()
            payload=decoder.decompress(base64.b64decode(''.join(encoded),validate=True),MAX_METADATA+1)
            if len(payload)<=MAX_METADATA and decoder.eof and not decoder.unused_data:record=json.loads(payload)
        except (ValueError,zlib.error):pass
    return output,record


def assign(lines,sources,epsilon):
    tree=shapely.STRtree(sources)
    matched=defaultdict(lambda:defaultdict(list));buffer={}
    for kind,parts in lines.items():
        for line in parts:
            cancel_check()
            candidates=[]
            for i in tree.query(line,predicate='dwithin',distance=epsilon):
                if i not in buffer:buffer[i]=sources[i].boundary.buffer(epsilon,quad_segs=2)
                if buffer[i].covers(line):candidates.append(int(i))
            if len(candidates)!=1:raise ValueError(f'_{kind} 轮廓无法唯一匹配当前名义铜图形（{line.centroid.x:.3f}, {line.centroid.y:.3f} μm）')
            matched[candidates[0]][kind].append(line)
    return matched


def extended(line,margin):
    a,b=np.asarray(line.coords)
    offset=(b-a)*margin/np.linalg.norm(b-a)
    return shapely.LineString([a-offset,b+offset])


def cuts(groups,material,epsilon):
    # Only odd-degree ends of C need a seam. Segments joined within a CAD
    # block and a closed hole in C do not contribute artificial cut points.
    count=Counter(tuple(line.coords[index]) for line in groups['C'] for index in (0,-1))
    labels={k:shapely.union_all(groups.get(k,())) for k in ('A','B')}
    ends=[]
    for xy,n in count.items():
        if not n%2:continue
        point=shapely.Point(xy)
        roles=[k for k,line in labels.items() if line.dwithin(point,epsilon)]
        if len(roles)!=1:raise ValueError('动区轮廓端点没有唯一相接的运动侧或固定侧')
        ends.append((point,roles[0]))
    if not ends or len(ends)%2:raise ValueError('缺少成对的动区分界端点')
    candidates=[]
    envelope=material.buffer(epsilon,quad_segs=2)
    for i,(a,label) in enumerate(ends):
        for j in range(i+1,len(ends)):
            b,other=ends[j]
            if other!=label:continue
            line=shapely.LineString([a,b])
            if line.length>epsilon and envelope.covers(line) and material.boundary.distance(line.interpolate(.5,normalized=True))>epsilon:
                candidates.append((line.length,i,j,line))
    used=set();seams=[]
    for _,i,j,line in sorted(candidates,key=lambda q:q[:3]):
        if i not in used and j not in used and all(not q.intersects(line) for q in seams):
            used.update((i,j));seams.append(line)
    if len(used)!=len(ends):raise ValueError('动区分界无法配对成不相交的内部直线')
    return seams


def active_domains(source,material,groups,seams,margin,epsilon):
    def cells(poly):
        result=[poly]
        for seam in seams:
            blade=extended(seam,margin)
            result=[p for cell in result for p in split(cell,blade).geoms if not p.is_empty]
        return result
    contour=shapely.union_all(groups['C'])
    marked=shapely.union_all([cell for cell in cells(material)
                              if cell.boundary.buffer(epsilon,quad_segs=2).intersection(contour).length>epsilon*8])
    if marked.is_empty or marked.boundary.buffer(epsilon,quad_segs=2).intersection(contour).length<contour.length-epsilon*8:
        raise ValueError('动区分界与 _C 轮廓不一致')
    nominal=cells(source)
    domains=[p for p in nominal if marked.covers(p.representative_point())]
    if not domains or len(domains)==len(nominal):raise ValueError('无法将 DXF 动区映射回名义铜')
    return domains


def read_domains(source,cache,roles,compensation,scale,dbu):
    from .output_compensation import sized
    from .short_edges import contract
    if not isinstance(source,dict):source=read(source)
    result={}
    epsilon=max(dbu*.1,1e-7)
    for layer,raw in source.items():
        if roles.get(layer)!='固定RDL':
            raise ValueError(f'DXF 图层 {layer} 须在当前工程设置为固定RDL')
        nominal=cache.layers[layer]['geometry']
        amount=float(compensation.get(layer,0.))
        scaled_maps=[raw]
        if scale!=1:
            scaled_maps.append({k:[shapely.transform(line,lambda xy:xy/scale) for line in values] for k,values in raw.items()})
        matched=None
        for lines in scaled_maps:
            try:matched=assign(lines,nominal,epsilon)
            except ValueError as exc:failure=exc
            else:material=nominal;break
        if matched is None and round(amount/dbu):
            edge=getattr(cache.data,'minimum_edge_um',0.)
            material=[contract(g,edge) for g in sized(((g,) for g in nominal),amount,dbu)]
            for lines in scaled_maps:
                try:matched=assign(lines,material,epsilon)
                except ValueError as exc:failure=exc
                else:break
        if matched is None:raise ValueError(f'{layer}：{failure}') from failure
        for i,groups in matched.items():
            if 'C' not in groups:
                if len(groups)==1:result[(layer,i)]=(next(iter(groups)),(),())
                else:raise ValueError(f'{layer} #{i+1} 缺少运动侧与固定侧之间的 _C 动区')
                continue
            try:
                seams=cuts(groups,material[i],epsilon)
                margin=abs(amount)*4+dbu*16
                domains=active_domains(nominal[i],material[i],groups,seams,margin,epsilon)
            except (ValueError,shapely.errors.GEOSException) as exc:raise ValueError(f'{layer} #{i+1}：{exc}') from exc
            result[(layer,i)]=('C',domains,[extended(seam,margin) for seam in seams])
    return result


def cut_interval(plan,domain,blades):
    from .reconstruction_edits import key
    portion=plan.route.intersection(domain)
    if portion.length<=plan.tolerance*4:return None
    marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(portion)))
    a,b=float(marks.min()),float(marks.max())
    absolute=[];relative=[]
    for side,pos in enumerate((a,b)):
        probe=plan.route.interpolate(pos)
        axis=min(blades,key=lambda line:line.distance(probe))
        if axis.distance(probe)>plan.tolerance*4:
            if side or a>plan.tolerance or not plan.allows_pad_point:return None
            a=0.;absolute.append(None);relative.append(0.);continue
        u,v=np.asarray(axis.coords);direction=v-u
        angle=math.atan2(direction[1],direction[0])
        tangent=plan.tangent(pos);perp=math.atan2(tangent[1],tangent[0])+math.pi/2
        absolute.append(angle);relative.append((angle-perp+math.pi/2)%math.pi-math.pi/2)
    part=plan.partition(a,b,relative)
    if part is None or abs(part['a']-a)+abs(part['b']-b)>plan.tolerance*4:return None
    if part['A'].intersection(domain).area<=plan.tolerance**2:return None
    return key(plan),a,b,absolute


def configure(model,parameters,settings,domains,progress=None):
    """Translate selected DXF blocks to native editable intervals atomically."""
    from .reconstruction import Reconstructor
    from .reconstruction_edits import key
    from .edit_history import RECONSTRUCTION_FIELDS
    from .resources import progress_slice

    affected=set(domains);imported={ref for ref,(kind,_,_) in domains.items() if kind=='C'}
    fields={name:tuple(row for row in getattr(settings,name) if row[:2] not in affected)
            for name in RECONSTRUCTION_FIELDS if name not in ('reconstruction_regions','reconstruction_boxes','reconstruction_angle_boxes','reconstruction_fixed_via_follows')}
    fields['reconstruction_regions']=tuple(row for row in settings.reconstruction_regions if row[0] not in {k for k,_ in affected})
    fields['reconstruction_box_excluded']=tuple(sorted(set(settings.reconstruction_box_excluded)|affected))
    fields['reconstruction_forced']=tuple(sorted(set(fields['reconstruction_forced'])|imported))
    fields['reconstruction_ignored']=tuple(sorted(set(fields['reconstruction_ignored'])|(affected-imported)))
    fields['reconstruction_following']=tuple(sorted(set(fields['reconstruction_following'])|{ref for ref,(kind,_,_) in domains.items() if kind=='A'}))
    cfg=replace(settings,**fields)
    intervals=list(cfg.reconstruction_intervals);angles=list(cfg.reconstruction_angles)
    added=list(cfg.reconstruction_added);deleted=list(cfg.reconstruction_deleted);used=set()
    r=Reconstructor(model,parameters,cfg)
    try:
        plans=defaultdict(list)
        for entry in r._plans_for_all_poses(progress_slice(progress,0,.6)):
            ref=(entry['layer'],entry['index'])
            if ref in imported and entry.get('plan') is not None:plans[ref].append(entry['plan'])
        for ref in sorted(imported):
            cancel_check()
            _,regions,blades=domains[ref];found=set()
            for plan in plans[ref]:
                picks=[(j,p) for j,g in enumerate(regions) if (p:=cut_interval(plan,g,blades)) is not None]
                picks.sort(key=lambda item:item[1][1])
                if not picks:
                    deleted.append(key(plan));continue
                for count,(j,(identity,a,b,directions)) in enumerate(picks):
                    found.add(j)
                    if count:
                        center=plan.route.interpolate((a+b)/2)
                        suffix=sha256(repr((identity,round(a,8),round(b,8))).encode()).hexdigest()[:12]
                        added.append((*ref,center.x,center.y,suffix,plan.identity,picks[0][1][1],picks[-1][1][2]))
                        identity=(*plan.key,plan.identity.split('~')[0]+'~'+suffix)
                    intervals.append((*identity,a,b))
                    angles.extend((*identity,side,angle) for side,angle in enumerate(directions) if angle is not None)
            if len(found)!=len(regions):raise ValueError(f'{ref[0]} #{ref[1]+1} 的 DXF 动区无法沿真实端孔生成有效切口')
            used.add(ref)
        updated=replace(cfg,reconstruction_intervals=tuple(intervals),reconstruction_added=tuple(added),
                        reconstruction_angles=tuple(angles),reconstruction_deleted=tuple(deleted))
        nominal=np.zeros((len(model.case['dies']),3))
        verifier=Reconstructor(model,parameters,updated,plan_pool=r.plan_pool,reference=r)
        try:
            frame=verifier.frame(nominal,targets=used,diagnostics=False,_raw=True,progress=progress_slice(progress,60,.4))
        finally:verifier.close()
        actual=defaultdict(list)
        for q in frame['pieces']:actual[(q['layer'],q['index'])].append(q['nominal_regions']['A'])
        for ref in used:
            expected=shapely.union_all(domains[ref][1]);observed=shapely.union_all(actual[ref])
            if expected.symmetric_difference(observed).area>model.data.dbu_um*4*expected.length:
                raise ValueError(f'{ref[0]} #{ref[1]+1} 的实际动区与 DXF 分界不符，未应用输入')
        return updated
    finally:r.close()
