"""Between-zone rigid copper motion policies (max / min / average)."""
import math
import numpy as np
import shapely
from .preview import rigid_transform

MODES = ('max', 'min', 'average')
LABELS = {
    'max': '跟随幅度大的一侧',
    'min': '跟随幅度小的一侧',
    'average': '两侧平均',
}


def normalize_overrides(rows):
    if not isinstance(rows, (list, tuple)):
        raise ValueError('动区间随动设置必须为列表')
    result = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise ValueError('动区间随动记录须包含图层、图形编号、锚点坐标与随动模式')
        layer, index, ax, ay, mode = row
        if not isinstance(layer, str) or type(index) is not int or index < 0:
            raise ValueError('动区间随动记录的图层或图形编号无效')
        try:
            ax, ay = float(ax), float(ay)
        except (TypeError, ValueError):
            raise ValueError('动区间随动记录的锚点坐标无效')
        if not (math.isfinite(ax) and math.isfinite(ay)) or max(abs(ax), abs(ay)) > 1e12:
            raise ValueError('动区间随动记录的锚点坐标超出范围')
        if mode not in MODES:
            raise ValueError('动区间随动模式无效')
        key = (layer, index, round(ax, 4), round(ay, 4))
        result[key] = mode
    return tuple((*k, result[k]) for k in sorted(result))


def anchor_of(geometry):
    pt = geometry.representative_point()
    return (round(float(pt.x), 4), round(float(pt.y), 4))


def lookup_mode(settings, ref, geometry, tol=1e-3):
    """Resolve effective between-zone motion mode for one rigid intermediate block."""
    default = getattr(settings, 'reconstruction_between_motion', 'max')
    if default not in MODES:
        default = 'max'
    overrides = getattr(settings, 'reconstruction_between_overrides', ())
    if not overrides or geometry is None or geometry.is_empty:
        return default, False
    best = None
    margin = max(tol * 8, 1e-2)
    for layer, index, ax, ay, mode in overrides:
        if (layer, index) != ref:
            continue
        pt = shapely.Point(ax, ay)
        if geometry.covers(pt):
            return mode, True
        dist = float(geometry.distance(pt))
        if dist <= margin and (best is None or dist < best[0]):
            best = (dist, mode)
    if best is not None:
        return best[1], True
    return default, False


def solve_serial_weights(modes):
    """Solve motion weights [1.0, w_1, ..., w_{K-1}, 0.0] for K-1 intermediate blocks."""
    m = len(modes)
    if m == 0:
        return [1.0, 0.0]
    w = [1.0] + [1.0 - (i + 1) / (m + 1) for i in range(m)] + [0.0]
    for _ in range(64):
        delta = 0.0
        for i, mode in enumerate(modes, 1):
            if mode == 'max':
                nxt = max(w[i - 1], w[i + 1])
            elif mode == 'min':
                nxt = min(w[i - 1], w[i + 1])
            else:
                nxt = 0.5 * (w[i - 1] + w[i + 1])
            delta = max(delta, abs(nxt - w[i]))
            w[i] = nxt
        if delta <= 1e-12:
            break
    return w


def solve_graph_weights(physical_count, known, edges, modes):
    """Solve motion weights for floating rigid cells in branched/plane copper."""
    unknown = [i for i in range(physical_count) if i not in known]
    values = dict(known)
    if not unknown:
        return values
    # Build adjacency with weights
    adj = {i: [] for i in unknown}
    for (a, b), weight in edges.items():
        if a in adj:
            adj[a].append((b, weight))
        if b in adj:
            adj[b].append((a, weight))
    # Pure average uses exact linear least squares (identical to Laplace average).
    if all(modes.get(i, 'average') == 'average' for i in unknown):
        index = {v: k for k, v in enumerate(unknown)}
        mat = np.zeros((len(unknown), len(unknown)))
        rhs = np.zeros(len(unknown))
        for (i, j), weight in edges.items():
            for a, b in ((i, j), (j, i)):
                if a not in index:
                    continue
                mat[index[a], index[a]] += weight
                if b in index:
                    mat[index[a], index[b]] -= weight
                else:
                    rhs[index[a]] += weight * known[b]
        solved = np.linalg.lstsq(mat, rhs, rcond=None)[0]
        values.update((i, float(np.clip(v, 0.0, 1.0))) for i, v in zip(unknown, solved))
        return values
    # Initialize unknown nodes to neutral 0.5 and converge per-node policy.
    for i in unknown:
        values[i] = 0.5
    for _ in range(128):
        delta = 0.0
        for i in unknown:
            nbrs = adj.get(i)
            if not nbrs:
                nxt = 0.0
            else:
                mode = modes.get(i, 'max')
                if mode == 'max':
                    nxt = max(values[j] for j, _ in nbrs)
                elif mode == 'min':
                    nxt = min(values[j] for j, _ in nbrs)
                else:
                    total_w = sum(wt for _, wt in nbrs)
                    nxt = sum(values[j] * wt for j, wt in nbrs) / max(total_w, 1e-18)
            nxt = float(np.clip(nxt, 0.0, 1.0))
            delta = max(delta, abs(nxt - values[i]))
            values[i] = nxt
        if delta <= 1e-11:
            break
    return values


def pair_middle_pose(geometry, center_a, state_a, center_b, state_b, mode):
    """Compute rigid (center, state) for a middle core between two moving DIEs."""
    center_a = np.asarray(center_a, float)
    center_b = np.asarray(center_b, float)
    state_a = np.asarray(state_a, float)
    state_b = np.asarray(state_b, float)
    p = np.asarray(geometry.centroid.coords[0], float) if geometry is not None and not geometry.is_empty else 0.5 * (center_a + center_b)
    pa = rigid_transform(p[None, :], center_a, state_a)[0]
    pb = rigid_transform(p[None, :], center_b, state_b)[0]
    da = pa - p
    db = pb - p
    amp_a = (float(np.linalg.norm(da)), abs(float(state_a[2])))
    amp_b = (float(np.linalg.norm(db)), abs(float(state_b[2])))
    if mode == 'max':
        return (center_a, state_a) if amp_a >= amp_b else (center_b, state_b)
    if mode == 'min':
        return (center_a, state_a) if amp_a <= amp_b else (center_b, state_b)
    # Exact rigid average about middle block centroid p:
    # works identically whether both sides move or one side is stationary.
    avg_state = np.array([0.5 * (da[0] + db[0]), 0.5 * (da[1] + db[1]), 0.5 * (state_a[2] + state_b[2])], float)
    return p, avg_state
