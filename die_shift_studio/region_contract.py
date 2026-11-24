"""Physical outline contract for the nominal dynamic/static region DXF."""
from collections import Counter, defaultdict

import numpy as np
import shapely

from .resources import cancel_check


REFERENCE_LAYERS = {'封装边框': 'PKG_REF', '芯片边框': 'DIE_REF'}
EXPORT_ROLES = frozenset((*REFERENCE_LAYERS, '固定RDL'))
# The solver still uses A for deformation, B for fixed and C for moving copper.
PUBLIC_KIND = {'A': '_C', 'B': '_B', 'C': '_A'}


def overlapping_edges(segments):
    """Return positive-length coincidences once, including partial overlaps."""
    if not len(segments):
        return
    coords = shapely.get_coordinates(segments).reshape(-1, 2, 2)
    vecs = coords[:, 1] - coords[:, 0]
    len2 = vecs[:, 0] * vecs[:, 0] + vecs[:, 1] * vecs[:, 1]
    tree = shapely.STRtree(segments)
    for start in range(0, len(segments), 512):
        cancel_check()
        i, j = tree.query(segments[start:start+512], predicate='intersects')
        i += start
        keep = j > i
        i, j = i[keep], j[keep]
        if not len(i):
            continue
        # Only nearly collinear segment pairs can share positive-length overlap.
        cross = vecs[i, 0] * vecs[j, 1] - vecs[i, 1] * vecs[j, 0]
        collinear = (cross * cross) <= (1e-10 * len2[i] * len2[j] + 1e-24)
        i, j = i[collinear], j[collinear]
        if not len(i):
            continue
        shared = shapely.intersection(segments[i], segments[j])
        for k in np.flatnonzero(shapely.length(shared) > 0):
            yield int(i[k]), int(j[k]), shared[k]


def _records(path):
    with open(path, encoding='ascii') as stream:
        record, fields = '', []
        while line := stream.readline():
            value = stream.readline()
            if not value:
                raise ValueError('DXF记录不完整')
            code, value = int(line), value.strip()
            if code == 0:
                yield record, fields
                record, fields = value, []
            else:
                fields.append((code, value))
        yield record, fields


def verify_written_contours(path, *, expected_blocks=None, expected_references=None, shared_layers=(),
                            allow_closed_blocks=False, expected_standard=None):
    """Read actual BLOCK/INSERT coordinates and audit the expanded linework."""
    section = block = None
    contents, block_layers, inserts, references = {}, {}, {}, []
    standard, expected_standard = Counter(), expected_standard or {}
    for kind, fields in _records(path):
        attrs = dict(fields)
        if kind == 'SECTION':
            section = attrs[2]
        elif kind == 'ENDSEC':
            section = None
        elif section == 'BLOCKS':
            if kind == 'BLOCK':
                block = attrs[2]
                if block in contents or any(float(attrs.get(k, 0)) for k in (10, 20, 30)):
                    raise ValueError('动静区块名称重复或基点偏移')
                contents[block] = []
                block_layers[block] = set()
            elif kind == 'ENDBLK':
                block = None
            elif kind == 'LWPOLYLINE' and block and attrs[8].endswith(tuple(PUBLIC_KIND.values())):
                block_layers[block].add(attrs[8])
                contents[block].append(_outline(fields, attrs, None if allow_closed_blocks else False))
            else:
                raise ValueError('动静区块含不支持的实体或未赋予对应ABC图层')
        elif section == 'ENTITIES':
            if kind == 'INSERT':
                name, layer = attrs[2], attrs[8]
                if name in inserts or any(float(attrs.get(k, 0)) for k in (10, 20, 30, 50)) or any(
                        float(attrs.get(k, 1)) != 1 for k in (41, 42, 43, 70, 71)):
                    raise ValueError('动静区块重复插入或位置发生变化')
                if not layer.endswith(tuple(PUBLIC_KIND.values())):
                    raise ValueError('动静区块未插入ABC图层')
                inserts[name] = layer
            elif kind == 'LWPOLYLINE' and attrs[8] in REFERENCE_LAYERS.values():
                references.append((attrs[8], _outline(fields, attrs, True)))
            elif (attrs.get(8),kind) in expected_standard:
                standard[attrs[8],kind]+=1
                if kind=='LWPOLYLINE':_outline(fields,attrs,True)
            else:
                raise ValueError('动静区输出包含非轮廓实体')
    if standard != expected_standard:
        raise ValueError('一并导出的DXF实体回读不一致')
    if contents.pop('*Model_Space', []) or contents.pop('*Paper_Space', []):
        raise ValueError('动静区输出含未引用轮廓')
    block_layers.pop('*Model_Space', None); block_layers.pop('*Paper_Space', None)
    if set(contents) != set(inserts) or any(not paths for paths in contents.values()):
        raise ValueError('动静区块定义、插入或线段不完整')
    if any(block_layers.get(name) != {layer} for name, layer in inserts.items()):
        raise ValueError('动静区块内部实体图层与块插入图层不一致')
    if expected_blocks is not None:
        if len(expected_blocks) != len(inserts) or any(
                inserts.get(b['name']) != b['layer'] or
                len(contents[b['name']]) != len(b['outlines']) or
                any(not np.array_equal(a, q) for a, q in zip(contents[b['name']], b['outlines']))
                for b in expected_blocks):
            raise ValueError('动静区块回读轮廓与写出坐标不一致')
        if len(references) != len(expected_references) or any(
                layer != wanted or not np.array_equal(xy[:-1], points)
                for (layer, xy), (wanted, points) in zip(references, expected_references)):
            raise ValueError('边框回读轮廓与写出坐标不一致')

    layers = defaultdict(list)
    for name, layer in inserts.items():
        layers[layer[:-2]].extend(contents[name])
    for name, xy in references:
        layers[name].append(xy)
    summary, segments = {}, []
    for name, outlines in layers.items():
        cancel_check()
        degree = Counter()
        edges = np.concatenate([np.stack((xy[:-1], xy[1:]), axis=1) for xy in outlines])
        for a, b in edges:
            degree[tuple(a)] += 1
            degree[tuple(b)] += 1
        # Shared contours are emitted just once, on the first owning layer.
        # Their original rings are checked before deduplication.
        if name not in shared_layers and any(n % 2 for n in degree.values()):
            raise ValueError(f'{name} ABC交界未严格闭合，未覆盖已有文件')
        segments.append(edges)
        summary[name] = dict(segments=len(edges), nodes=len(degree), closed=True, overlap=False)
    if not summary and not standard:
        raise ValueError('未读回有效动静区轮廓')
    if segments and next(overlapping_edges(shapely.linestrings(np.concatenate(segments))), None) is not None:
        raise ValueError('动静区DXF存在完全或部分重叠的轮廓线段')
    return summary


def _outline(fields, attrs, expected_closed):
    xy = np.column_stack(([float(v) for c, v in fields if c == 10],
                          [float(v) for c, v in fields if c == 20]))
    closed=bool(int(attrs[70]) & 1)
    if ((expected_closed is not None and expected_closed != closed) or len(xy) != int(attrs[90]) or
            len(xy) < (3 if closed else 2) or not np.isfinite(xy).all() or
            np.array_equal(xy[0], xy[-1]) or np.any(np.all(xy[:-1] == xy[1:], axis=1))):
        raise ValueError('动静区输出的轮廓节点或开闭状态无效')
    return np.vstack((xy, xy[:1])) if closed else xy
