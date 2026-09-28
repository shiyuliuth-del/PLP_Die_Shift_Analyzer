"""Deterministic closed-boundary checks on a rendered state, not probabilities."""
import numpy as np
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from .preview import rigid_transform
from .core import _margin


def check_state(data,case,roles,p,result,states,layers,scheme):
    failures=[];tol=max(1e-9,data.dbu_um*1e-6)
    centers=[np.array([q['cx'],q['cy']]) for q in case['infos']]
    moved=[Polygon(rigid_transform(np.asarray(d.exterior.coords),c,z)) for d,c,z in zip(case['dies'],centers,states)]
    def fail(kind,point):failures.append(dict(kind=kind,point=list(map(float,point))))
    for i,die in enumerate(moved):
        if not case['board'].covers(die) or die.distance(case['board'].boundary)<=p.min_package_clearance_um+tol:
            fail(f'DIE {i+1}触碰封装净距边界',die.centroid.coords[0])
        for j in range(i):
            if die.distance(moved[j])<=p.min_die_gap_um+tol:
                fail(f'DIE {j+1}/{i+1}触碰芯片净距边界',((die.centroid.x+moved[j].centroid.x)/2,(die.centroid.y+moved[j].centroid.y)/2))
    obstacles=[Polygon(a) for key,arrays in layers.items() if roles.get(key)=='禁布区' for a in arrays]
    if obstacles:
        tree=STRtree(obstacles)
        for key,arrays in layers.items():
            if roles.get(key) not in ('运动过孔','固定过孔','运动RDL','固定RDL'):continue
            for a in arrays:
                poly=Polygon(a)
                if any(poly.intersects(obstacles[j]) for j in tree.query(poly)):
                    fail(f'图层{key}触碰禁布区',poly.centroid.coords[0])
    from .contact import ContactModel
    from .stack import Connections
    connections=case.get('connections') or Connections(data,roles)
    for issue in ContactModel(data,case,roles,connections).current(states,p)['issues']:
        if issue['scheme']==scheme:fail(issue['kind'],issue['point'])
    return dict(problem_percent=100 if failures else 0,failures=failures,
                scope='实际动孔—固定RDL接口的其他网络接触，以及机械净距、禁布区')
