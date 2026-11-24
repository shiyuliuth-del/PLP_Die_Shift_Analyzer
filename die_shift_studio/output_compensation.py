"""Output-only, signed KLayout contour sizing in micrometres."""
from dataclasses import replace
import math
import re

import numpy as np
import shapely

from .resources import cancel_check


def default_amount(role):
    return 1.5 if role in ('运动RDL','固定RDL') else 0.


def normalize(values=None):
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError('图层补偿必须按 GDS 层号填写')
    result = {}
    for layer, amount in values.items():
        if (not isinstance(layer, str) or not re.fullmatch(r'\d+/\d+', layer)
                or type(amount) not in (int, float) or not math.isfinite(amount) or not -1000 <= amount <= 1000):
            raise ValueError('图层补偿须为 −1000 至 +1000 μm')
        rounded = round(float(amount), 3)
        if rounded:
            result[layer] = rounded
    return result


def sized(groups, amount, dbu):
    """Size each group as one merged region. Bound each offline IPC message."""
    from .native_backend import _encode, backend
    distance = int(round(amount / dbu))
    if not distance:
        return [shapely.union_all(group) for group in groups]

    def run(batch):
        cancel_check()
        regions = backend().execute(dict(op='size_groups', groups=batch, distance=distance))
        return [shapely.union_all([
            shapely.Polygon(np.asarray(rings[0]) * dbu, [np.asarray(hole) * dbu for hole in rings[1:]])
            for rings in region
        ]) for region in regions]

    output = []
    batch = []
    points = 0
    for group in groups:
        polygons = [geom for geom in group if not geom.is_empty]
        maximum = max((max(map(abs, geom.bounds)) for geom in polygons), default=0.)
        if maximum/dbu+4*abs(distance)>2**31-1:
            raise ValueError('轮廓补偿超出 KLayout 的 32 位格点坐标范围')
        encoded = [poly for geom in polygons for poly in _encode(geom, dbu) if len(poly[0]) >= 3]
        count = sum(len(ring) for poly in encoded for ring in poly)
        if batch and (points + count > 50000 or len(batch) >= 512):
            output.extend(run(batch))
            points = 0
            batch = []
        batch.append(encoded)
        points += count
    if batch:
        output.extend(run(batch))
    return output


def prepared_sources(cache, values, dbu):
    """Freeze nominal compensated shapes before producing any measured poses."""
    return {
        layer: tuple(sized(((g,) for g in cache.layers[layer]['geometry']), amount, dbu))
        for layer, amount in values.items() if layer in cache.layers and round(amount/dbu)
    }


def compensate_snapshot(snapshot, values, dbu):
    layers = dict(snapshot.layers)
    from .short_edges import contract
    for layer, amount in values.items():
        if layer not in layers or not layers[layer] or not round(amount / dbu):
            continue
        adjusted, = sized([shapely.from_wkb(layers[layer])], amount, dbu)
        clean = contract(adjusted, snapshot.minimum_edge_um)
        layers[layer] = tuple(poly.wkb for poly in shapely.get_parts(clean) if not poly.is_empty)
    return replace(snapshot, layers=layers)


def extended_masks(original, adjusted, masks, amount, dbu):
    """Extend the material-side seams to the newly expanded physical outline."""
    from .region_export import line_parts
    from shapely.ops import polygonize

    if amount <= 0 or original.is_empty or adjusted.is_empty or all(g.is_empty for g in masks.values()):
        return masks
    epsilon = max(dbu * 0.01, 1e-8)
    boundary = original.boundary
    collar = boundary.buffer(epsilon)
    seams = shapely.union_all([g.boundary for g in masks.values() if not g.is_empty]).difference(collar)
    lines = [adjusted.boundary]
    # Polygonize one arrangement for all three labels, so every new junction
    # is shared. An original split face continues along its terminal tangent.
    extension = max(4 * amount, np.hypot(*(np.subtract(adjusted.bounds[2:], adjusted.bounds[:2]))))
    for line in line_parts(seams):
        xy = np.array(line.coords)
        for end, neighbor in ((0, 1), (-1, -2)):
            if boundary.distance(shapely.Point(xy[end])) <= epsilon * 4:
                direction = xy[end] - xy[neighbor]
                direction /= np.linalg.norm(direction)
                start = xy[end].copy()
                ray = shapely.LineString([start,start+direction*extension])
                near = next((g for g in line_parts(ray.intersection(adjusted))
                             if g.distance(shapely.Point(start)) <= epsilon*4),None)
                if near is not None:
                    distance = max((np.asarray(near.coords)-start)@direction)
                    xy[end] = start+direction*(distance+epsilon*8)
        lines.append(shapely.LineString(xy))
    # Very narrow features can disappear/merge on the output grid. Classify
    # each resulting face by its surviving overlap with nominal material.
    labels = {**masks, 'B': original.difference(shapely.union_all(list(masks.values())))}
    trees = {key: shapely.STRtree(shapely.get_parts(g)) for key,g in labels.items() if not g.is_empty}
    classified = {key: [] for key in masks}
    for face in polygonize(shapely.union_all(lines)):
        if not adjusted.covers(face.representative_point()):
            continue
        overlap={}
        for key,tree in trees.items():
            near=tree.query(face,predicate='intersects')
            if len(near):overlap[key]=float(shapely.area(shapely.intersection(tree.geometries[near],face)).sum())
        if overlap and max(overlap.values())>0:
            label=max(overlap,key=overlap.get)
        else:
            label=min(trees,key=lambda key:face.distance(trees[key].geometries[trees[key].nearest(face)]))
        if label in classified:
            classified[label].append(face)
    return {key: shapely.union_all(faces) for key, faces in classified.items()}
