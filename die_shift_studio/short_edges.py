"""Contract a short edge by removing one endpoint and joining its neighbours."""
import heapq
import numpy as np
import shapely


def _ring(coords, threshold, reverse=False):
    xy = np.asarray(coords, dtype=float)[:-1]
    if reverse:
        xy = xy[::-1]
    count = len(xy)
    limit = threshold * threshold
    delta = np.roll(xy, -1, axis=0) - xy
    lengths = np.einsum('ij,ij->i', delta, delta)
    if not np.any(lengths < limit):
        return xy
    nxt = list(range(1, count+1))
    nxt[-1] = 0
    alive = [True]*count
    heap = [(d, i, nxt[i]) for i, d in enumerate(lengths) if d < limit]
    heapq.heapify(heap)
    while heap and count >= 3:
        _, a, b = heapq.heappop(heap)
        if not alive[a] or not alive[b] or nxt[a] != b:
            continue
        c = nxt[b]
        alive[b] = False
        count -= 1
        nxt[a] = c
        delta = xy[c] - xy[a]
        d = float(delta @ delta)
        if d < limit:
            heapq.heappush(heap, (d, a, c))
    return xy[alive]


def contract(geometry, threshold):
    if threshold <= 0 or geometry.is_empty:
        return geometry
    if geometry.geom_type == 'MultiPolygon':
        parts = [contract(g, threshold) for g in geometry.geoms]
        result = shapely.MultiPolygon([g for g in parts if not g.is_empty])
        if not result.is_valid:
            raise ValueError('删除短边会使多个铜区接触或交叠')
        return result
    if geometry.geom_type != 'Polygon':
        raise ValueError('最短边长过滤需要有效的多边形')
    rings = (geometry.exterior, *geometry.interiors)
    if not has_short(geometry, threshold):
        return geometry
    for reverse in (False, True):
        shell, *holes = [_ring(r.coords, threshold, reverse) for r in rings]
        if len(shell) < 3:
            return shapely.Polygon()
        result = shapely.Polygon(shell, [r for r in holes if len(r) >= 3])
        if result.is_valid and not result.is_empty and result.area > 0:
            return result
    raise ValueError('删除短边无法形成有效轮廓')


def has_short(geometry, threshold):
    if threshold <= 0 or geometry.is_empty:
        return False
    for g in shapely.get_parts(geometry):
        for ring in (g.exterior, *g.interiors):
            d = np.diff(np.asarray(ring.coords), axis=0)
            if np.any(np.einsum('ij,ij->i', d, d) < threshold**2):
                return True
    return False
