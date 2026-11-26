"""Choose a small, reproducible set of stress poses before reconstruction."""
from itertools import combinations
import numpy as np
import shapely

from .preview import rigid_transform
from .resources import cancel_check


def indices_for(model, p, grid):
    centers = model.bound.centers
    signature=(centers.tobytes(),p.line_space_um,tuple(z.tobytes() for z in grid.locals))
    cached=getattr(model,'_risk_catalogue',None)
    if cached is not None and cached[0]==signature:
        return cached[1]
    active = [i for i, radix in enumerate(grid.radices) if radix > 1]
    if not active:
        return ()
    gaps = [np.full(radix, np.inf) for radix in grid.radices]
    hits = [np.zeros(radix, int) for radix in grid.radices]
    travel = [np.linalg.norm(poses[:, :2], axis=1) for poses in grid.locals]
    for record in model.records:
        owner = record['owner']
        if owner not in active:
            continue
        cancel_check()
        nominal = shapely.get_coordinates(record['poly'])
        posed = np.array([shapely.transform(record['poly'], lambda xy: rigid_transform(xy, centers[owner], pose))
                          for pose in grid.locals[owner]], dtype=object)
        center = np.asarray(record['center'])
        distances = np.array([np.linalg.norm(rigid_transform(center, centers[owner], pose)-center)
                              for pose in grid.locals[owner]])
        travel[owner] = np.maximum(travel[owner], distances)
        # Narrow residual spacing and high violation counts give different
        # useful witnesses. Compare only foreign copper on a receiving plane.
        halo = float(distances.max()+p.line_space_um+np.ptp(nominal, axis=0).max())
        near = set()
        for plane in {k.split('/')[0] for k in record['target_layers']}:
            fixed, tree = model.fixed_trees[plane]
            near.update(fixed[int(i)] for i in tree.query(record['poly'], predicate='dwithin', distance=halo))
        candidates = sorted(near.intersection(record['foreign']))
        if candidates:
            slack = shapely.distance(posed[:, None], model.node_geometry[candidates][None, :])-p.line_space_um
            gaps[owner] = np.minimum(gaps[owner], slack.min(axis=1))
            hits[owner] += (slack <= 0).sum(axis=1)
    poses = [shapely.polygons([rigid_transform(np.asarray(die.exterior.coords), centers[i], z)
                              for z in grid.locals[i]]) for i, die in enumerate(model.case['dies'])]
    strongest = {}
    out = {}
    def add(values):
        digits = [0]*grid.count
        for i, d in values:
            digits[i] = int(d)
        index = grid.index(digits)
        if index >= 0:
            out[index] = None

    for i in active:
        if not np.isfinite(gaps[i]).any():
            radius = float(np.linalg.norm(shapely.get_coordinates(model.case['dies'][i])-centers[i], axis=1).max())
            travel[i] += 2*radius*np.abs(np.sin(grid.locals[i][:, 2]/2))
        ranked = sorted(range(1, grid.radices[i]), key=lambda d: (-hits[i][d], gaps[i][d], -travel[i][d], d))
        strongest[i] = ranked[0]
        # With only one active DIE, expose all 2^k extreme corner combinations
        # across its active axes (8 corners when X, Y, and angle are all non-zero).
        if len(active) == 1:
            poses_i = grid.locals[i]
            active_axes = [ax for ax in range(3) if np.ptp(poses_i[:, ax]) > 0]
            witnesses = [d for d in range(1, grid.radices[i]) if all(poses_i[d, ax] != 0 for ax in active_axes)]
            if not witnesses:
                witnesses = ranked
        else:
            # With only one active axis there are at most two stress directions.
            # Keep both, including ties which can stress different neighbours.
            witnesses = ranked if grid.radices[i] <= 3 else {
                ranked[0], min(ranked, key=lambda d: (gaps[i][d], -travel[i][d], d)),
                max(ranked, key=lambda d: travel[i][d]),
            }
        for d in witnesses:
            add(((i, d),))
    for i, j in combinations(active, 2):
        cancel_check()
        # Real polygon distance selects approaching and separating poses.
        # Exclude zero local states here: single-DIE witnesses are above.
        distance = shapely.distance(poses[i][1:, None], poses[j][None, 1:])
        for idx in (distance.argmin(), distance.argmax()):
            a, b = np.unravel_index(idx, distance.shape)
            add(((i, a+1), (j, b+1)))
    if len(active) > 1:
        add(strongest.items())
    # With only a handful of feasible states, useful witnesses can coincide
    # with the full focus set (for example a single active translation axis).
    result=tuple(sorted(out))
    model._risk_catalogue=(signature,result)
    return result
