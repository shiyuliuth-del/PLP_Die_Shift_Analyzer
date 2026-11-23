"""Export APD 23.1 named spacing rules and their editable layout regions."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET
import json
import os

import numpy as np
import shapely

from .resources import cancel_check


CFT='http://www.cadence.com/xsd/wholeFile'
HEADER='http://www.cadence.com/xsd/header'
OBJECTS='http://www.cadence.com/xsd/objects'
ROOT=f'{{{CFT}}}root-Cadence-Allegro-Constraint-File'
TO_UM={'um':Decimal(1),'mm':Decimal(1000),'mil':Decimal('25.4'),'mils':Decimal('25.4'),
       'inch':Decimal(25400),'inches':Decimal(25400)}


def template(path):
    with open(path,'rb') as stream:raw=stream.read(32*1024**2+1)
    if len(raw)>32*1024**2 or b'<!DOCTYPE' in raw.upper():
        raise ValueError('APD 模板超过容量限制或包含文档类型定义')
    root=ET.fromstring(raw)
    if (root.tag!=ROOT or root.findtext(f'{{{CFT}}}txt-FormatVersion','').strip()!='23.100'
            or root.findtext(f'{{{CFT}}}txt-FileType','').strip()!='Design'):
        raise ValueError('请选择 APD 23.1 导出的 .dcfx 模板')
    header=root.find(f'{{{CFT}}}xml-Header/{{{HEADER}}}root')
    precision=header.find(f'{{{HEADER}}}precision') if header is not None else None
    if precision is None or precision.get('units','').lower() not in TO_UM:
        raise ValueError('APD 模板缺少受支持的图纸单位')
    decimals=int(precision.get('numberOfDecimalPlaces','3'))
    if not 0<=decimals<=9:raise ValueError('APD 模板精度无效')
    objects=root.find(f'{{{CFT}}}dir-objects')
    subclass=next((q for q in objects if q.get('Name')=='SubClass'),None) if objects is not None else None
    names=[q.get('Name','') for q in subclass.iter(f'{{{OBJECTS}}}object')] if subclass is not None else []
    layers=tuple(n.split('/',1)[1] for n in names if n.startswith('Etch/') and 'ConsRegion/'+n.split('/',1)[1] in names)
    if not layers:raise ValueError('APD 模板中没有可绑定约束区的导体层')
    return root,header,TO_UM[precision.get('units').lower()],decimals,layers


def rule_regions(field,board,centers,states,interval,scope):
    """Round the full-cell demand upward and resolve overlap by maximum demand."""
    from .reconstruction import transform
    if not np.isfinite(interval) or interval<=0:raise ValueError('避让空间输出间隔须大于零')
    base=float(field['base_spacing_um'])
    per_layer=defaultdict(lambda:defaultdict(list))
    for q in field['maps']:
        cancel_check()
        spacing=np.asarray(q['spacing'],float)+q['cell_allowance']
        ratio=np.maximum(0.,(spacing-base)/interval)
        if not np.isfinite(ratio).all() or np.max(ratio)>1e9:raise ValueError('目标良率在当前范围内无法转成有限的线距规则')
        bands=np.ceil(np.nextafter(ratio,-np.inf)).astype(np.int64)
        for band in np.unique(bands):
            cancel_check()
            iy,ix=np.nonzero(bands==band)
            x,y=q['x_edges'],q['y_edges']
            cells=shapely.box(x[ix],y[iy],x[ix+1],y[iy+1])
            area=shapely.coverage_union_all(cells).intersection(q['domain'])
            if states is not None:
                owner=q['owner'];area=transform(area,centers[owner],states[owner])
            if scope=='package':area=area.intersection(board)
            if not area.is_empty and area.area>0:
                for layer in q['fixed_layers']:per_layer[layer][int(band)].append(area)
    result={}
    for layer,groups in sorted(per_layer.items()):
        remaining=board if scope=='package' else shapely.union_all([g for values in groups.values() for g in values])
        rules=[]
        for band,areas in sorted(groups.items(),reverse=True):
            cancel_check()
            region=shapely.union_all(areas).intersection(remaining)
            if region.is_empty or region.area<=0:continue
            rules.append((base+band*interval,region))
            remaining=remaining.difference(region)
        if scope=='package' and remaining.area>0:rules.append((base,remaining))
        if rules:result[layer]=rules
    if not result:raise ValueError('当前图纸没有可输出的固定 RDL 避让空间')
    return result


def without_holes(area):
    """Cadence shape creation accepts a single exterior path."""
    for poly in shapely.get_parts(area):
        if poly.geom_type!='Polygon' or poly.is_empty:continue
        if not poly.interiors:
            yield poly
            continue
        triangulate=getattr(shapely,'constrained_delaunay_triangles',None)
        if triangulate is None:
            from shapely.ops import triangulate
            pieces=[p for tri in triangulate(poly) for p in shapely.get_parts(tri.intersection(poly))
                    if p.geom_type=='Polygon' and not p.is_empty]
        else:
            pieces=list(shapely.get_parts(triangulate(poly)))
        if (not pieces or any(g.interiors for g in pieces)
                or shapely.union_all(pieces).symmetric_difference(poly).area>max(1e-9,poly.area*1e-10)):
            raise ValueError('APD 约束区孔洞不能无损拆分')
        yield from pieces


def named_regions(regions,layers):
    for source,rules in regions.items():
        layer=layers[source]
        for spacing,area in rules:
            key=sha256(f'{source}|{layer}|{spacing:.12g}'.encode()+area.normalize().wkb).hexdigest()[:16]
            yield f'DSA_{key.upper()}',layer,spacing,area


def dcfx_bytes(entries,source):
    """Use the Region override form emitted by Constraint Manager 23.100."""
    original,header,unit,decimals,_=source
    ET.register_namespace('cft',CFT)
    root=ET.Element(ROOT)
    for tag in ('txt-FileType','txt-FormatVersion'):
        root.append(deepcopy(original.find(f'{{{CFT}}}{tag}')))
    h=deepcopy(header)
    contents=h.find(f'{{{HEADER}}}Contents')
    if contents is not None:
        contents.clear();ET.SubElement(contents,f'{{{HEADER}}}v').text='Spacing'
    ET.SubElement(root,f'{{{CFT}}}xml-Header',Name='Header').append(h)
    objects=ET.SubElement(root,f'{{{CFT}}}dir-objects',Name='objects')
    section=ET.SubElement(ET.SubElement(objects,f'{{{CFT}}}xml-objects',Name='Region'),'root',xmlns=OBJECTS)
    for name,_,spacing,_ in entries:
        value=(Decimal(str(spacing))/unit).quantize(Decimal(1).scaleb(-decimals),rounding=ROUND_CEILING)
        obj=ET.SubElement(section,'object',Name=name)
        attr=ET.SubElement(obj,'attribute',Name='LINE_TO_LINE_SPACING')
        ET.SubElement(attr,'value',Value=format(value,'f'))
        ET.SubElement(ET.SubElement(attr,'origin'),'v').text='BE'
    ET.indent(root,space='  ')
    payload=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    return payload.replace(b'\n',b'\r\n')+b'\r\n'


def skill_bytes(entries):
    rows=[]
    for name,layer,_,area in entries:
        for polygon in without_holes(area):
            points=[f'({float(x):.9f} {float(y):.9f})' for x,y in polygon.exterior.coords]
            lines='\n      '.join(' '.join(points[i:i+8]) for i in range(0,len(points),8))
            rows.append(f'    ("{name}" {json.dumps("CONSTRAINT REGION/"+layer,ensure_ascii=False)} (\n      {lines}))')
    head='''; Cadence Allegro X APD 23.1
; Load this file, run dsa_apd_regions(), then merge the accompanying .dcfx.
procedure(dsa_apd_regions()
  let((tx ok groups region name layer points shape rows)
    rows = '(
'''
    tail='''
    )
    unless(axlDBGetDesign() error("Open the target APD design first."))
    groups = makeTable('dsaRegions nil)
    foreach(row rows
      unless(axlLayerGet(cadr(row)) error("Missing constraint layer: %s" cadr(row)))
      when(axlSelectByName("REGION" car(row)) error("Region already exists: %s" car(row)))
    )
    tx = axlDBTransactionStart()
    unless(tx error("Could not start APD transaction."))
    unwindProtect(
      progn(
        foreach(row rows
          name = car(row)
          layer = cadr(row)
          region = groups[name]
          unless(region
            region = axlRegionCreate(name)
            unless(region error("Could not create region: %s" name))
            groups[name] = region
          )
          points = foreach(mapcar p caddr(row)
            list(axlMKSConvert(car(p)*1e-6 "METERS" "design")
                 axlMKSConvert(cadr(p)*1e-6 "METERS" "design"))
          )
          shape = car(axlDBCreateShape(axlPathStart(points) nil layer))
          unless(shape && axlRegionAdd(region shape) error("Could not add shape: %s" name))
        )
        ok = axlDBTransactionCommit(tx)
      )
      unless(ok axlDBTransactionRollback(tx))
    )
    when(ok printf("DSA regions created. Import the paired .dcfx using Constraints > Import > Merge.\\n"))
    ok
  )
)
'''
    return (head+'\n'.join(rows)+tail).encode('utf-8')


def write(path,regions,layer_map,source):
    path=Path(path).with_suffix('.dcfx');script=path.with_suffix('.il')
    if len(set(layer_map.values()))!=len(layer_map) or not set(layer_map.values())<=set(source[4]):
        raise ValueError('每个固定 GDS 图层须对应不同的 APD 导体层')
    entries=list(named_regions(regions,layer_map))
    payloads=(dcfx_bytes(entries,source),skill_bytes(entries))
    previous=script.read_bytes() if script.exists() else None
    staged=[]
    try:
        for data in payloads:
            with NamedTemporaryFile(dir=path.parent,prefix='.dsa-apd-',delete=False) as out:
                staged.append(Path(out.name));out.write(data);out.flush();os.fsync(out.fileno())
        cancel_check()
        os.replace(staged[1],script)
        try:os.replace(staged[0],path)
        except OSError:
            if previous is None:script.unlink()
            else:script.write_bytes(previous)
            raise
    finally:
        for temp in staged:temp.unlink(missing_ok=True)
    return path,script,len(entries)
