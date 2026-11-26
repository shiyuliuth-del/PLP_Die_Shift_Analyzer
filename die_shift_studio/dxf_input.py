"""Independent DXF import, normalised to the existing OAS geometry pipeline."""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
import re

import ezdxf
from ezdxf.path import from_hatch, make_path
import gdstk
import numpy as np
import shapely

from .region_import import LAYER, UNIT_UM, read_metadata
from .resources import cancel_check
from .layer_roles import ROLES

DBU = .001
NUMBERED = re.compile(r'(?:L)?(\d+)(?:[_/\-]D?(\d+))?',re.I)
REFERENCE = {
    'PKG_REF':('127/0','封装边框'), 'RKG_REF':('127/0','封装边框'),
    'PACKAGE':('127/0','封装边框'), 'DIE_REF':('128/0','芯片边框'), 'DIE':('128/0','芯片边框'),
    'MOVING_RDL':(None,'运动RDL'), 'FIXED_RDL':(None,'固定RDL'),
    'MOVING_VIA':(None,'运动过孔'), 'FIXED_VIA':(None,'固定过孔'), 'KEEPOUT':(None,'禁布区'),
    **{role:(None,role) for role in ROLES},
}


def _entities(items, parents=(), inherited='0'):
    for entity in items:
        cancel_check()
        layer = entity.dxf.get('layer','0')
        if layer == '0':
            layer = inherited
        if entity.dxftype() != 'INSERT':
            yield layer.upper(),entity
            continue
        name = entity.dxf.name.upper()
        if name in parents or len(parents)>=24:
            raise ValueError('DXF 包含递归或过深的块引用。')
        instances = entity.multi_insert() if entity.mcount>1 else (entity,)
        for instance in instances:
            yield from _entities(instance.virtual_entities(),(*parents,name),layer)


def _faces(lines):
    if not lines:
        return [], []
    network = shapely.union_all(lines,grid_size=DBU)
    polygons, cuts, dangles, bad = shapely.polygonize_full(shapely.get_parts(network))
    areas = list(polygons.geoms)
    if areas:
        shells = [shapely.Polygon(g.exterior) for g in areas]
        tree = shapely.STRtree(shells)
        # Every second nested shell is a hole in outline-based DXF layers.
        areas = [g for g in areas if len(tree.query(g.representative_point(),predicate='within'))%2]
    return areas, [*cuts.geoms,*dangles.geoms,*bad.geoms]


def load(path, folder, progress=None):
    path = Path(path).resolve()
    try:
        doc = ezdxf.readfile(path)
    except (ezdxf.DXFError, UnicodeError) as exc:
        raise ValueError(f'无法读取 DXF：{exc}') from exc
    unit = int(doc.header.get('$INSUNITS',0))
    if unit not in UNIT_UM:
        raise ValueError(f'DXF 长度单位 {unit} 暂不支持。')
    scale = UNIT_UM[unit]
    if progress:
        progress(10,'展开 DXF 图形与块引用')
    lines = defaultdict(list)
    solids = defaultdict(list)
    warnings = Counter()
    supported = {'LINE','LWPOLYLINE','POLYLINE','ARC','CIRCLE','ELLIPSE','SPLINE','SOLID','TRACE','3DFACE'}
    for index,(layer,entity) in enumerate(_entities(doc.modelspace())):
        if index>=500000:
            raise ValueError('DXF 展开超过 500000 个实体，未截断图纸。')
        kind = entity.dxftype()
        if kind not in supported and kind not in ('HATCH','MPOLYGON'):
            warnings[kind]+=1
            continue
        try:
            paths = from_hatch(entity) if kind in ('HATCH','MPOLYGON') else (make_path(entity),)
            contours=[]
            for curve in paths:
                vertices=np.asarray([tuple(v) for v in curve.flattening(DBU/scale)],float)
                if len(vertices)<2:
                    continue
                if not np.isfinite(vertices).all() or np.ptp(vertices[:,2])*scale>DBU:
                    raise ValueError('非平面或非有限坐标')
                xy = vertices[:,:2]*scale
                if curve.is_closed and not np.array_equal(xy[0],xy[-1]):
                    xy=np.vstack((xy,xy[0]))
                line=shapely.LineString(xy)
                if line.length>DBU:
                    contours.append(line)
            if kind in ('HATCH','MPOLYGON'):
                solid, unfitted = _faces(contours)
                solids[layer].extend(solid)
                warnings['断开的填充轮廓']+=len(unfitted)
            else:
                lines[layer].extend(contours)
        except (ValueError, TypeError, shapely.errors.GEOSException) as exc:
            raise ValueError(f'DXF 图层 {layer} 的 {kind} 无法读取：{exc}') from exc

    names=sorted(set(lines)|set(solids))
    stems=Counter(name[:-2] for name in names if re.search(r'_[ABC]$',name))
    buckets=defaultdict(list);abc_names={}
    for name in names:
        match=LAYER.fullmatch(name)
        abc = name[-2:-1]=='_' and name[-1:] in 'ABC' and (match or stems[name[:-2]]>=2)
        if abc:
            base=name[:-2]
            abc_names[name]=(base,name[-1])
            buckets[base].append(name)
        else:
            buckets[name].append(name)

    keys={};roles={};contours={};geometry={};display={}
    def fixed_key(name):
        match=NUMBERED.fullmatch(name)
        if match:
            return f'{int(match[1])}/{int(match[2] or 0)}'
        return REFERENCE.get(name,(None,''))[0]
    for base in sorted(buckets,key=lambda item:(not bool(NUMBERED.fullmatch(item)),item)):
        key=fixed_key(base)
        if key and key not in keys.values() and all(0<=int(x)<=65535 for x in key.split('/')):
            keys[base]=key
    occupied=set(keys.values())
    number=iter(f'{i}/0' for i in range(1,65536))
    record=read_metadata(path)
    recorded=(record or {}).get('roles',{})
    if not isinstance(recorded,dict):
        recorded={}
    if progress:
        progress(40,'归并 DXF 轮廓并分配图层')
    for base,names in sorted(buckets.items()):
        cancel_check()
        key=keys.get(base)
        if key is None:
            key=next(k for k in number if k not in occupied)
            occupied.add(key)
        abc=any(name in abc_names for name in names)
        role='固定RDL' if abc else REFERENCE.get(base,(None,recorded.get(key,'不参与')))[1]
        if role not in ROLES:
            role='不参与'
        faces, open_lines=_faces([line for name in names for line in lines[name]])
        if open_lines and role=='不参与':
            # Open construction strokes remain visible without becoming copper.
            faces.extend(g.buffer(DBU*5,cap_style='flat') for g in open_lines)
        elif open_lines:
            warnings[f'{base} 未闭合轮廓']+=len(open_lines)
        material=shapely.union_all([*faces,*(g for name in names for g in solids[name])])
        if material.is_empty:
            continue
        geometry[key]=[p for p in shapely.get_parts(material) if p.geom_type=='Polygon']
        roles[key]=role;display[key]=base
        if abc:
            parts=defaultdict(list)
            for name in names:
                if name in abc_names:
                    parts[abc_names[name][1]].extend(lines[name])
            contours[key]=parts
    if not geometry:
        raise ValueError('DXF 未找到可导入的二维轮廓。')

    # Convert once to a native, editable source. The original input remains read-only.
    from .gds_merge import winding_outline
    lib=gdstk.Library(unit=1e-6,precision=DBU*1e-6)
    top=lib.new_cell('DXF')
    for key,polygons in geometry.items():
        layer,datatype=map(int,key.split('/'))
        top.add(*(gdstk.Polygon(winding_outline(p),layer=layer,datatype=datatype) for p in polygons))
    folder.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(dir=folder,suffix='.oas',delete=False) as stream:
        temp=Path(stream.name)
    try:
        lib.write_oas(temp)
        with temp.open('rb') as stream:
            digest=sha256()
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        target=folder/(digest.hexdigest()+'.oas')
        cancel_check()
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    from .core import read_gds, Parameters, build_case
    from .gds_merge import restore_project_geometry
    from .settings import ViewSettings
    from dataclasses import asdict
    stored_order=(record or {}).get('layer_order',[])
    order=[k for k in stored_order if isinstance(k,str) and k in roles] if isinstance(stored_order,list) else []
    order=list(dict.fromkeys([*order,*sorted(roles,key=lambda k:tuple(map(int,k.split('/'))),reverse=True)]))
    project=dict(schema_version=5,roles=roles,layer_order=order,gds_path=str(target),
                 dxf_source=str(path),dxf_layer_names=display,
                 dxf_outlines=[[key,kind,list(map(list,line.coords))] for key,groups in contours.items()
                               for kind,paths in groups.items() for line in paths],
                 visibility=dict.fromkeys(roles,True),layer_compensation=dict.fromkeys(roles,0.),
                 settings={'reconstruction_auto':False,'evaluation_scope':'risk'},parameters={'minimum_area_um2':0.})
    data,project=restore_project_geometry(read_gds(target),project)
    # If terminal layers are present, recover editable transitions from the ABC
    # outline. An ABC-only file still opens as a complete nominal drawing.
    if contours and '运动过孔' in roles.values():
        from .contact import ContactModel
        from .fast_preview import GeometryCache
        from .region_import import configure, read_domains
        try:
            cache=GeometryCache(data)
            case=build_case(data,roles,geometry=cache.geometries,layer_order=order)
            p=Parameters.from_dict(project['parameters'],require_rdl=False)
            model=ContactModel(data,case,roles,case['connections'],cache,parameters=p)
            domains=read_domains(contours,cache,roles,{},1.,data.dbu_um)
            project['settings']=asdict(configure(model,p,ViewSettings.from_dict(project['settings']),domains))
            project['overlays']={'reconstruction':True}
        except ValueError as exc:
            data.import_warnings.append(f'DXF 已导入原始轮廓。分区暂未绑定到可用端孔：{exc}')
    elif contours:
        data.import_warnings.append('DXF 已导入名义铜。缺少运动过孔时，补齐端孔图层用途后才能应用重构预演。')
    data.import_warnings.extend(f'DXF 已跳过 {count} 个 {kind} 实体或未闭合轮廓。'
                                for kind,count in warnings.items() if count)
    return data,project
