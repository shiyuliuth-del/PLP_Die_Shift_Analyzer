"""Equipment-side straight-line fixed-RDL repairs, and DRC of the repaired layout.

A shifted die takes its moving via with it. The equipment does not re-route: it
measures the offset and extends the *fixed* RDL with a straight segment until it
reaches the via's new position. That added copper is new geometry on the fixed
RDL layer, so it can violate spacing against its same-layer neighbours, and its
width usually cannot enclose the via annulus.

This module builds that patch, hands the repaired features to the existing DRC
machinery as ordinary replacements (no DRC code is duplicated), and attributes
the resulting violations back to the patch that caused them. Geometry stays in
memory for drawing; the summary returned alongside it is JSON-safe.

Scope: the patch is a straight line of the fixed-RDL width from where the via
used to land to where it lands now. It is not a router, and a passed DRC only
means "this simplified patch shape keeps the local spacing rules", not that the
equipment's real repair is qualified.
"""
import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

SCOPE = ('按“过孔原位→偏移后位置”在固定RDL层生成直线补块，并对补块后的图形重新做DRC。'
         '直线补块是设备方简化模型，通过不代表设备实际修复合格，也不替代工厂DRC或量产签认。')


@dataclass
class Repair:
    layer: str
    index: int
    net: int
    owner: int
    via_key: str
    via_index: int
    offset_um: float
    anchor: tuple
    target: tuple
    patch: object
    geometry: object
    width_um: float = 0.


def via_width_um(polygon):
    """Narrow dimension of a via: the short side of its rotated bounding box.

    A via is not necessarily square, and in this tool it is often a bar. The
    short side is the width the equipment's repair line has to match, and it
    stays meaningful for non-rectangular landing shapes as well.
    """
    try:
        rectangle = polygon.minimum_rotated_rectangle
        xy = shapely.get_coordinates(rectangle)
    except Exception:
        xy = None
    if xy is None or len(xy) < 4:
        return float(math.sqrt(max(0., polygon.area)))
    closed = np.vstack([xy, xy[:1]])
    sides = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    sides = sides[sides > 0]
    if not len(sides):
        return float(math.sqrt(max(0., polygon.area)))
    return float(sides.min())


def _anchor_on(copper, moved):
    """Start the straight extension at the copper point nearest the new via."""
    if copper.covers(Point(moved)):
        return np.asarray(moved, float)
    return np.asarray(nearest_points(copper, Point(moved))[0].coords[0], float)


def moving_via_repairs(model, states, width_um=None, annulus_um=0., landing=False,
                       tolerance_um=0., replacements=None):
    """Build one straight-line fixed-RDL patch per interface that lost contact.

    ``model`` is a ContactModel. Only 运动过孔 features that nominally land on a
    固定RDL layer are considered, which is exactly the interface the equipment
    repairs. A patch is only needed once the moved via no longer touches the
    copper — a via that merely shifted while still landing on it needs no repair,
    and inventing one there would report spacing problems that cannot occur.
    ``width_um`` is the patch diameter; ``None`` or 0 makes every patch as wide
    as the via it reconnects, which is the equipment's own starting point.
    ``replacements`` may carry accepted deformation so the patch starts from the
    copper actually present, not from the source.
    """
    states = np.asarray(states, float)
    replaced = {}
    for key, items in (replacements or {}).items():
        for q in items:
            for i in q['indices']:
                replaced[key, i] = q['geometry']
    tolerance = max(float(tolerance_um), float(model.data.dbu_um))
    requested = None if width_um is None else float(width_um)
    if requested is not None and requested < 0:
        raise ValueError('补块直径不能为负')
    out = []
    for record in model.records:
        targets = model.connections.fixed_interfaces(record['key'], record['index'])
        if not targets:
            continue
        diameter = via_width_um(record['poly']) if requested in (None, 0.) else requested
        if diameter <= 0:
            continue
        half = float(diameter) / 2.
        moved_via = model.moved(record['poly'], record['owner'], states)
        nominal = np.asarray(record['center'], float)
        moved = np.asarray(moved_via.centroid.coords[0], float)
        offset = float(np.linalg.norm(moved - nominal))
        if offset <= tolerance:
            continue
        for key, index in targets:
            if model.roles.get(key) != '固定RDL':
                continue
            copper = replaced.get((key, index))
            if copper is None:
                copper = model.cache.layers[key]['geometry'][index]
            if copper.intersects(moved_via):
                continue
            anchor = _anchor_on(copper, moved)
            if float(np.linalg.norm(moved - anchor)) <= tolerance:
                continue
            line = LineString([anchor, moved])
            patch = line.buffer(half, quad_segs=16)
            if landing:
                patch = patch.union(Point(moved).buffer(max(float(annulus_um), half), quad_segs=32))
            if patch.is_empty:
                continue
            out.append(Repair(layer=key, index=int(index),
                              net=int(model.nodes[model.ids[key, index]]['net']),
                              owner=int(record['owner']), via_key=record['key'],
                              via_index=int(record['index']), offset_um=offset,
                              anchor=tuple(float(v) for v in anchor),
                              target=tuple(float(v) for v in moved),
                              patch=patch, geometry=copper.union(patch),
                              width_um=float(diameter)))
    return out


def repair_replacements(repairs):
    """Merge patches of one feature into the replacement shape DRC understands."""
    grouped = {}
    for q in repairs:
        slot = grouped.setdefault((q.layer, q.index), dict(net=q.net, geometry=None, patches=[]))
        slot['geometry'] = q.geometry if slot['geometry'] is None else slot['geometry'].union(q.geometry)
        slot['patches'].append(q.patch)
    out = {}
    for (layer, index), slot in grouped.items():
        out.setdefault(layer, []).append(dict(indices=(index,), geometry=slot['geometry'], net=slot['net']))
    return out


def merge_replacements(*mappings):
    """Concatenate replacement lists per layer; later mappings cannot shadow."""
    out = {}
    for mapping in mappings:
        for layer, items in (mapping or {}).items():
            out.setdefault(layer, []).extend(items)
    return out


def violation_signature(violation):
    geometry = violation.get('geometry')
    return (str(violation.get('kind')), None if geometry is None else geometry.wkb)


def new_violations(base, patched, dbu_um=0.001, relative=0.01, absolute_um2=1.0):
    """Regions a kind of violation gains once the patches are present.

    A bare overlap cannot answer "did the patch cause this": an enclosure marker
    sits in the copper-free ring *around* the patch and never touches it. Exact
    geometry equality is wrong in the other direction: adding copper reshapes an
    existing marker, and calling that "new" blames the patch for a violation it
    did not create. So compare the union of each kind with and without patches
    and keep only the area that genuinely appeared. A growth smaller than
    ``max(absolute_um2, relative * previous area)`` is treated as reshaping.
    """
    base_by_kind, patched_by_kind = {}, {}
    for violation in base:
        geometry = violation.get('geometry')
        if geometry is not None and not geometry.is_empty:
            base_by_kind.setdefault(str(violation.get('kind')), []).append(geometry)
    for violation in patched:
        geometry = violation.get('geometry')
        if geometry is not None and not geometry.is_empty:
            patched_by_kind.setdefault(str(violation.get('kind')), []).append(geometry)
    out = []
    for kind, geometries in patched_by_kind.items():
        now = shapely.union_all(geometries)
        previous = base_by_kind.get(kind)
        prior = shapely.union_all(previous) if previous else None
        extra = now if prior is None else now.difference(prior)
        if extra.is_empty:
            continue
        threshold = max(float(absolute_um2), float(relative) * (prior.area if prior is not None else 0.))
        for part in shapely.get_parts(extra):
            if part.geom_type not in ('Polygon', 'MultiPolygon') or part.area <= threshold:
                continue
            out.append(dict(kind=kind, geometry=part, area_um2=float(part.area)))
    out.sort(key=lambda q: (-q['area_um2'], q['kind']))
    return out


def violation_owners(repairs, violations, window_um=0.):
    """Index of the patch each violation belongs to, or None when it belongs to none.

    One rule serves both the counts and the click-through navigation, so the
    table can never disagree with the numbers it shows.
    """
    owners = []
    for violation in violations:
        geometry = violation.get('geometry')
        best = None
        if geometry is not None and not geometry.is_empty:
            for i, q in enumerate(repairs):
                distance = q.patch.distance(geometry)
                if best is None or distance < best[1]:
                    best = (i, distance)
        owners.append(best[0] if best is not None and best[1] <= window_um else None)
    return owners


def attribute_violations(repairs, violations, window_um=0.):
    """Assign each new marker to its nearest patch inside the rule window."""
    hits = [0] * len(repairs)
    elsewhere = []
    for owner, violation in zip(violation_owners(repairs, violations, window_um), violations):
        if owner is None:
            elsewhere.append(violation)
        else:
            hits[owner] += 1
    return hits, elsewhere


def repair_summary(repairs, violations, additions=None, hits=None, elsewhere=None, window_um=0.):
    """JSON-safe view of the patches and of the post-patch DRC result."""
    violations = list(violations)
    additions = list(violations) if additions is None else list(additions)
    if hits is None or elsewhere is None:
        hits, elsewhere = attribute_violations(repairs, additions, window_um)
    owners = violation_owners(repairs, additions, window_um)
    def kinds(rows):
        out = {}
        for violation in rows:
            name = str(violation.get('kind', '未分类'))
            out[name] = out.get(name, 0) + 1
        return out
    rows = []
    for i, q in enumerate(repairs):
        rows.append(dict(layer=q.layer, feature=int(q.index) + 1, via=q.via_key,
                         via_index=int(q.via_index) + 1, owner=int(q.owner) + 1,
                         net=int(q.net), offset_um=float(q.offset_um),
                         width_um=float(q.width_um), patch_area_um2=float(q.patch.area),
                         new_violations=int(hits[i])))
    return dict(count=len(repairs), violations=len(violations),
                added=len(additions), on_patch=sum(hits), elsewhere=len(elsewhere),
                preexisting=len(violations) - len(additions),
                kinds=kinds(violations), added_kinds=kinds(additions),
                widths_um=sorted({round(float(q.width_um), 6) for q in repairs}),
                window_um=float(window_um), owners=[None if o is None else int(o) for o in owners],
                max_offset_um=max((q.offset_um for q in repairs), default=0.),
                layers=sorted({q.layer for q in repairs}), rows=rows, scope=SCOPE)


def repair_frame(model, states, settings, parameters, width_um=None, landing=None,
                 replacements=None, progress=lambda *_:None):
    """Repairs plus the DRC of the repaired layout, compared with the unpatched one.

    Returns every post-patch marker for display, the subset the patches newly
    introduced, and a JSON-safe summary. The DRC instance is local so the
    comparison cannot pollute an interactive engine's cache.
    """
    from .drc import GlobalDRC
    from .drc_rules import effective_rules
    width, space = effective_rules(parameters, settings)
    if width_um is None:
        width_um = getattr(settings, 'repair_patch_width_um', 0.)
    if landing is None:
        landing = bool(getattr(settings, 'repair_landing_pad', False))
    repairs = moving_via_repairs(model, states, width_um, annulus_um=parameters.annulus_um,
                                 landing=landing, replacements=replacements)
    merged = merge_replacements(replacements, repair_replacements(repairs))
    drc = GlobalDRC(model, parameters)
    try:
        patched = drc.frame(states, merged, settings, progress)
        # Without this comparison every pre-existing marker would look "new".
        additions = new_violations(drc.frame(states, replacements, settings, progress), patched) if repairs else []
    finally:
        drc.cache.clear()
    window = width + space
    hits, elsewhere = attribute_violations(repairs, additions, window_um=window)
    return dict(repairs=repairs, violations=patched, new_violations=additions,
                summary=repair_summary(repairs, patched, additions, hits, elsewhere, window_um=window))
