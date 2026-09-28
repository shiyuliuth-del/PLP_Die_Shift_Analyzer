"""Pure stdlib + KLayout operations, shared by in-process and desktop backends.

This module also runs inside KLayout's embedded Python, without NumPy/Shapely.
Coordinates are integer DBU. No design file or payload is executed as code.
"""
from collections import OrderedDict
import json

_REGIONS=OrderedDict()
def region(kdb,polygons):
    key=json.dumps(polygons,separators=(',',':'))
    if key in _REGIONS:
        _REGIONS.move_to_end(key);return _REGIONS[key].dup()
    r=kdb.Region()
    for rings in polygons:
        q=kdb.Polygon([kdb.Point(*point) for point in rings[0]])
        for ring in rings[1:]:q.insert_hole([kdb.Point(*point) for point in ring])
        r.insert(q)
    r.merge();_REGIONS[key]=r
    while len(_REGIONS)>192:_REGIONS.popitem(last=False)
    return r.dup()

def encode(r):
    return [[[ [v.x,v.y] for v in p.each_point_hull()]]+
            [[[v.x,v.y] for v in p.each_point_hole(i)] for i in range(p.holes())]
            for p in r.each()]

def execute(kdb,request):
    op=request['op']
    if op=='info':
        check=kdb.Region(kdb.Box(0,0,100,10)).width_check(20)
        if check.is_empty():raise RuntimeError('KLayout native DRC self-check failed')
        return dict(version=str(getattr(kdb,'__version__','desktop')),self_check=True)
    if op=='clear':_REGIONS.clear();return {}
    a=region(kdb,request['a'])
    if op in ('width','space','notch','separation'):
        options=dict(whole_edges=False,ignore_angle=5.+1e-8,metrics=kdb.Region.Projection,shielded=False)
        if op=='separation':pairs=a.separation_check(region(kdb,request['b']),request['distance'],**options)
        else:
            pairs=getattr(a,op+'_check')(request['distance'],**options);kept=kdb.EdgePairs()
            for pair in pairs.each():
                x,y=pair.first,pair.second
                if x.p1==y.p1 or x.p1==y.p2 or x.p2==y.p1 or x.p2==y.p2:continue
                kept.insert(pair)
            pairs=kept
        a=pairs.polygons(1)
    elif op=='round':
        r=kdb.Region()
        for part in a.each():r.insert(part.round_corners(request['radius'],request['radius'],128))
        a=r
    elif op!='normalize':raise ValueError('Unknown native operation: '+op)
    area=float(a.area());a.merge()
    return dict(polygons=encode(a),area=area)
