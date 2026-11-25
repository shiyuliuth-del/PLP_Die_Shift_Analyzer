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
    return component_modes(settings, ref, {0: geometry}, tol)[0]


def component_modes(settings, ref, components, tol=1e-3):
    """Assign each saved nominal anchor to at most one physical copper block."""
    default = getattr(settings, 'reconstruction_between_motion', 'max')
    if default not in MODES:
        default = 'max'
    result = dict.fromkeys(components, (default, False))
    overrides = [q for q in getattr(settings, 'reconstruction_between_overrides', ()) if q[:2] == ref]
    if not overrides:
        return result
    candidates = [(i, g, g.representative_point()) for i, g in components.items()
                  if g is not None and not g.is_empty]
    margin = max(tol * 8, 1e-2)
    chosen = {}
    for layer, index, ax, ay, mode in overrides:
        pt = shapely.Point(ax, ay)
        matches = [(not g.contains(pt), g.distance(pt), anchor.distance(pt), i)
                   for i, g, anchor in candidates if g.dwithin(pt, margin)]
        if matches:
            *rank, i = min(matches)
            if i not in chosen or rank < chosen[i]:
                chosen[i] = rank
                result[i] = (mode, True)
    return result


def solve_serial_weights(modes, *, moving_is_larger=True):
    """Share the end-to-end change over the cuts left free by local policies."""
    # The cumulative fraction decreases from the local terminal (1) to the
    # reference terminal (0). A max/min choice makes exactly one neighbouring
    # cut rigid. Average ties the magnitudes of its two neighbouring cuts.
    locked=set()
    group_ends=[]
    for i,mode in enumerate(modes,1):
        if mode=='average':continue
        follows_left=(mode=='max')==moving_is_larger
        locked.add(i-1 if follows_left else i)
        group_ends.append(i)
    groups=[];start=0
    for end in (*group_ends,len(modes)+1):
        groups.append((end-start,not any(i in locked for i in range(start,end))))
        start=end
    active=sum(count for count,free in groups if free)
    if not active:raise ValueError('串联随动约束锁住了全部过渡区')
    weights=[1.];steps=0
    for count,free in groups:
        for _ in range(count):
            steps+=int(free)
            weights.append((active-steps)/active)
    return weights


def motion_order(point, center, pose, reference):
    """Compare each terminal's actual displacement at the common copper."""
    placed=rigid_transform(np.asarray([point]),center,pose)[0]
    base=rigid_transform(np.asarray([point]),reference.center,reference.base)[0]
    return (float(np.linalg.norm(placed-point)),abs(float(pose[2]))) >= (
        float(np.linalg.norm(base-point)),abs(float(reference.base[2])))


def _anchored_chains(known, edges, modes):
    """Use the same equal split on unbranched material inside the graph."""
    neighbours={}
    for (a,b),weight in edges.items():
        neighbours.setdefault(a,{})[b]=weight
        neighbours.setdefault(b,{})[a]=weight
    solved={}
    for start,start_weight in known.items():
        for first,edge_weight in neighbours.get(start,{}).items():
            prior,current=start,first
            path=[]
            while current not in known:
                adjacent=neighbours[current]
                if len(adjacent)!=2 or any(weight!=edge_weight for weight in adjacent.values()):break
                path.append(current)
                prior,current=current,next(node for node in adjacent if node!=prior)
            if path and current in known and current>start:
                end_weight=known[current]
                shares=solve_serial_weights([modes.get(i,'max') for i in path],
                                            moving_is_larger=start_weight>=end_weight)
                solved.update((i,end_weight+(start_weight-end_weight)*share) for i,share in zip(path,shares[1:]))
    return solved


def solve_graph_weights(physical_count, known, edges, modes):
    """Solve motion weights for floating rigid cells in branched/plane copper."""
    known={**known,**_anchored_chains(known,edges,modes)}
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
