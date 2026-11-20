"""Pad-preserving ranking and bounded cleanup of already connected copper."""
from dataclasses import dataclass

import shapely

from .resources import cancel_check


@dataclass(frozen=True)
class Landing:
    via: object
    enclosure: object
    protected: object

    @classmethod
    def from_source(cls, via, enclosure, copper, tol):
        from .landing_geometry import embedded_pad
        pad = embedded_pad(via, copper, via, tol).intersection(copper) if copper.geom_type == 'Polygon' else via
        return cls(via, enclosure, shapely.union(pad, enclosure))


def pad_loss(copper, pads, tol):
    """Ignore area changes below the possible one-grid rounding strip."""
    return sum(max(0., pad.difference(copper).area - pad.length * tol)
               for pad in pads)


def transition_defects(geometry,zones,width_rule,min_angle,tol):
    from .drc import rule_region
    from .geometry_rules import angle_regions
    from .native_backend import any_violation
    narrow = shapely.Polygon()
    if width_rule and any_violation('width', geometry.wkb, tol, width_rule):
        narrow = rule_region(geometry, width_rule, tol).intersection(zones)
    acute = angle_regions(geometry, min_angle, tol).intersection(zones) if min_angle else shapely.Polygon()
    return narrow,acute


def refine(copper, zones, protected, free, width, width_rule, min_angle, tol, valid, defects):
    """Widen and round only near actual transition defects after connection."""
    from .reconstruction_route import parts

    def copper_union(*geometries):
        return shapely.union_all([p for g in geometries for p in parts(g)], grid_size=tol)

    narrow, acute = defects
    best = copper
    def score(a, b):
        return (not a.is_empty, a.area, not b.is_empty, b.area)

    best_score = score(narrow, acute)
    for radius in (width*.17, width*.35):
        cancel_check()
        if not narrow.is_empty:
            grown = narrow.buffer(radius, quad_segs=4).intersection(free)
            candidate = copper_union(copper, grown)
            if valid(candidate):
                candidate_score = score(*transition_defects(candidate,zones,width_rule,min_angle,tol))
                if candidate_score < best_score:
                    best, best_score = candidate, candidate_score
        if not acute.is_empty:
            window = acute.buffer(width, quad_segs=4).intersection(zones).difference(protected)
            if window.is_empty:
                continue
            # First round re-entrant corners, then trim protruding tips. The
            # active mask and acceptance check keep pad boundaries and neighbours.
            smoothed = best.buffer(radius, quad_segs=6).buffer(-radius, quad_segs=6)
            smoothed = smoothed.buffer(-radius, quad_segs=6).buffer(radius, quad_segs=6)
            candidate = copper_union(best.difference(window), smoothed.intersection(window).intersection(free))
            if valid(candidate):
                candidate_score = score(*transition_defects(candidate,zones,width_rule,min_angle,tol))
                if candidate_score < best_score:
                    best, best_score = candidate, candidate_score
    return best
