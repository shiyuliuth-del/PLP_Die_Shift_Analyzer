"""Reference frames for conductors terminated by two physical moving vias."""
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from .preview import rigid_transform


def rotate(vector, angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s], [s, c]]) @ vector


@dataclass(frozen=True)
class PairFrame:
    center: np.ndarray
    base: np.ndarray
    relative: np.ndarray

    @classmethod
    def from_endpoints(cls, center, state, other_center, other_state):
        # Express both transforms about the same origin, then evaluate the
        # moving terminal in the coordinate system rigidly attached to the other.
        delta = rigid_transform(np.asarray([center]), other_center, other_state)[0] - center
        base = np.r_[delta, other_state[2]]
        xy = rotate(state[:2] - delta, -base[2])
        return cls(center, base, np.r_[xy, state[2] - base[2]])

    def boundary_state(self, fraction):
        return self.world_pose(self.relative*fraction)

    def world_pose(self,state):
        return np.r_[self.base[:2]+rotate(state[:2],self.base[2]),self.base[2]+state[2]]

    def local_pose(self,state):
        return np.r_[rotate(state[:2]-self.base[:2],-self.base[2]),state[2]-self.base[2]]


@dataclass(frozen=True)
class MultiPairFrame:
    center: np.ndarray
    base: np.ndarray
    per_owner: dict
    mode: str
    custom: bool
    nominal_core: object


def frames(entries, centers, states, *, settings=None, interval_fn=None):
    grouped = defaultdict(list)
    for entry in entries:
        if entry.get('paired_vias') and entry['plan'] is not None:
            grouped[entry['layer'], entry['index']].append(entry)
    result = {}
    for ref, rows in grouped.items():
        endpoints = rows[0]['paired_vias']
        owners = {owner for _, owner in endpoints}
        active = {via for entry in rows for via in entry['refs']}
        if len(owners) > 1 and all(via in active for via, _ in endpoints):
            # Two distinct editable zones share a middle rigid core. Move the
            # core according to the between-zone motion logic (max / min / average).
            import shapely
            from .between_motion import lookup_mode, pair_middle_pose
            source = rows[0]['source']
            occupied = []
            if interval_fn is not None:
                for entry in rows:
                    part, _ = interval_fn(entry)
                    if part is not None:
                        occupied.extend((part['A'], part['C']))
            core = source.difference(shapely.union_all(occupied)) if occupied else source
            if not core.is_empty and core.geom_type!='Polygon':
                from .reconstruction_route import parts
                kept_parts=[g for g in parts(core) if g.area>1e-6]
                core=kept_parts[0] if len(kept_parts)==1 else shapely.MultiPolygon(kept_parts) if kept_parts else shapely.Polygon()
            mode, custom = lookup_mode(settings, ref, core) if settings is not None else ('max', False)
            owner_a, owner_b = sorted(owners)[:2]
            center_shape=source if core.is_empty else core
            mid_center, mid_state = pair_middle_pose(center_shape, centers[owner_a], states[owner_a], centers[owner_b], states[owner_b], mode, centers=centers, states=states)
            per_owner = {
                owner: PairFrame.from_endpoints(centers[owner], states[owner], mid_center, mid_state)
                for owner in owners
            }
            result[ref] = MultiPairFrame(mid_center, mid_state, per_owner, mode, custom, core)
            continue
        owner = rows[0]['owner']
        other = next((o for via, o in endpoints if via not in active), owner)
        result[ref] = (PairFrame(centers[owner], states[owner], np.zeros(3)) if other == owner else
                       PairFrame.from_endpoints(centers[owner], states[owner], centers[other], states[other]))
    return result


def boundary_pose(piece, frame, side):
    center = piece.get('boundary_center', frame['rotation_centers'][piece['die']])
    if 'boundary_states' in piece:
        return center, piece['boundary_states'][side]
    return center, frame['states'][piece['die']] * piece.get('endpoint_weights', (1., 0.))[side]


def relative_pose(piece, frame):
    owner = piece['die']
    return frame['rotation_centers'][owner], piece.get('relative_pose', frame['states'][owner])


def _move_branch_evidence(piece,center,state):
    from .reconstruction import transform
    if piece.get('branch_rigid'):
        piece['branch_rigid']=tuple((a,transform(b,center,state)) for a,b in piece['branch_rigid'])
    if piece.get('branch_conflict') is not None:
        piece['branch_conflict']=transform(piece['branch_conflict'],center,state)


def _record_boundary_states(piece,owner_frame):
    local=piece.get('local_boundary_states')
    piece['boundary_states']=(tuple(owner_frame.world_pose(state) for state in local) if local is not None
                              else tuple(owner_frame.boundary_state(weight) for weight in piece.get('endpoint_weights',(1.,0.))))
    piece['relative_pose']=owner_frame.relative
    piece['base_pose']=owner_frame.base


def repose(pieces, geometry, pose):
    """Map the assembled local copper and visible before/after witnesses to world XY."""
    from .reconstruction import transform
    if isinstance(pose, MultiPairFrame):
        if np.any(pose.base):
            geometry = transform(geometry, pose.center, pose.base)
        for q in pieces:
            owner_frame = pose.per_owner.get(q['die'])
            if owner_frame is not None:
                _record_boundary_states(q,owner_frame)
            if not np.any(pose.base):
                continue
            _move_branch_evidence(q,pose.center,pose.base)
            q['regions'] = {key: transform(g, pose.center, pose.base) for key, g in q['regions'].items()}
            q['bridge'], q['fixed'], q['moved'] = (q['regions'][key] for key in 'ABC')
            q['immovable'] = q['fixed']
            for name in ('moved_cut', 'extra_cut', 'missing_region', 'moving_handle_endpoint'):
                if q.get(name) is not None:
                    q[name] = transform(q[name], pose.center, pose.base)
            if q.get('seam') and q['moved_cut'] is not None:
                q['seam'] = [q['seam'][0], list(q['moved_cut'].centroid.coords[0])]
            if q.get('fallback_regions'):
                q['fallback_regions'] = {key: transform(g, pose.center, pose.base)
                                         for key, g in q['fallback_regions'].items()}
        return geometry
    if np.any(pose.base):
        geometry = transform(geometry, pose.center, pose.base)
    for q in pieces:
        _record_boundary_states(q,pose)
        if not np.any(pose.base):
            continue
        _move_branch_evidence(q,pose.center,pose.base)
        q['regions'] = {key: transform(g, pose.center, pose.base) for key, g in q['regions'].items()}
        q['bridge'], q['fixed'], q['moved'] = (q['regions'][key] for key in 'ABC')
        q['immovable'] = q['fixed']
        for name in ('moved_cut', 'extra_cut', 'missing_region', 'moving_handle_endpoint'):
            if q.get(name) is not None:
                q[name] = transform(q[name], pose.center, pose.base)
        if q.get('seam') and q['moved_cut'] is not None:
            q['seam'] = [q['seam'][0], list(q['moved_cut'].centroid.coords[0])]
        if q.get('fallback_regions'):
            q['fallback_regions'] = {key: transform(g, pose.center, pose.base)
                                     for key, g in q['fallback_regions'].items()}
    return geometry
