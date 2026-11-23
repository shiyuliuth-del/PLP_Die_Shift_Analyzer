"""Lightweight attraction to local source contour vertices during a cut drag."""
import numpy as np


def node_snap(plan, target, radius, strength, cache, angle=0.):
    if radius <= 0 or strength <= 0 or not 0 < target < plan.length:
        return target
    segment = int(np.searchsorted(plan.marks, target, side='right') - 1)
    key = (id(plan), segment, angle)
    if key not in cache:
        _, _, _, vertices, _ = plan._source_info()
        tangent = plan.tangent(target)
        # The cutting line can be tilted; compute the path coordinate at
        # which that line would meet each physical contour vertex.
        cosine, sine = np.cos(angle), np.sin(angle)
        normal = np.array([cosine*tangent[0]-sine*tangent[1], sine*tangent[0]+cosine*tangent[1]])
        scale = normal @ tangent
        if abs(scale) < 0.1:
            return target
        positions = plan.marks[segment] + (vertices - plan.xy[segment]) @ normal / scale
        use = (positions >= plan.marks[segment]) & (positions <= plan.marks[segment+1])
        positions, vertices = positions[use], vertices[use]
        order = np.argsort(positions)
        cache[key] = (positions[order], vertices[order])
    positions, vertices = cache[key]
    left, right = np.searchsorted(positions, [target-radius, target+radius])
    candidates = sorted(range(left, right), key=lambda index: abs(positions[index]-target))
    if not candidates:
        return target
    cut = plan.section(target, angle)
    if cut is None:
        return target
    ends = np.asarray(cut.coords)[[0,-1]]
    for index in candidates:
        if min(np.linalg.norm(ends-vertices[index], axis=1)) > radius * 2:
            continue  # same path projection, but another side branch
        distance = positions[index]-target
        return target + distance*strength*(1-abs(distance)/radius)**2
    return target
