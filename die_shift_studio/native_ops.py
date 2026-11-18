"""Pure stdlib + KLayout operations, shared by in-process and desktop backends.

This module also runs inside KLayout's embedded Python, without NumPy/Shapely.
Coordinates are integer DBU. No design file or payload is executed as code.
"""
from collections import OrderedDict
import json

_REGIONS=OrderedDict()
def region(kdb,polygons,identity=None):
    key=identity or json.dumps(polygons,separators=(',',':'))
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

def template_shapes(kdb, path):
    """Read static template geometry without interpreting design content as code."""
    layout = kdb.Layout()
    layout.read(path)
    roots = layout.top_cells()
    if not roots:
        raise ValueError('模板图纸没有顶层Cell')
    layers = []
    count = 0
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        geometry, labels = [], []
        for root in roots:
            for item in root.begin_shapes_rec(index).each():
                count += 1
                if count > 500_000:
                    raise ValueError('模板展开超过500000个图形，未截断输出')
                shape = item.shape()
                transform = item.dtrans()
                if shape.is_text():
                    label = shape.dtext.transformed(transform)
                    labels.append((label.string, label.x, label.y))
                    continue
                if not (shape.is_box() or shape.is_polygon() or shape.is_path()):
                    raise ValueError(f'模板图层 {info.layer}/{info.datatype} 含有无法保留的图形类型')
                polygon = shape.dpolygon.transformed(transform)
                hull = [(p.x, p.y) for p in polygon.each_point_hull()]
                holes = [[(p.x, p.y) for p in polygon.each_point_hole(i)] for i in range(polygon.holes())]
                geometry.append((hull, holes))
        if geometry or labels:
            layers.append((info.layer, info.datatype, info.name, geometry, labels))
    return layers

def execute(kdb,request):
    op=request['op']
    any_only=op.endswith('_any')
    if any_only:op=op[:-4]
    if op=='info':
        check=kdb.Region(kdb.Box(0,0,100,10)).width_check(20)
        if check.is_empty():raise RuntimeError('KLayout native DRC self-check failed')
        return dict(version=str(getattr(kdb,'__version__','desktop')),self_check=True)
    if op=='clear':_REGIONS.clear();return {}
    if op=='template_shapes':return template_shapes(kdb,request['path'])
    a=region(kdb,request['a'],request.get('akey'))
    if op in ('width','space','notch','separation'):
        options=dict(whole_edges=False,ignore_angle=5.+1e-8,metrics=kdb.Region.Projection,shielded=False)
        if op=='separation':pairs=a.separation_check(region(kdb,request['b'],request.get('bkey')),request['distance'],**options)
        else:
            pairs=getattr(a,op+'_check')(request['distance'],**options);kept=kdb.EdgePairs()
            for pair in pairs.each():
                x,y=pair.first,pair.second
                if x.p1==y.p1 or x.p1==y.p2 or x.p2==y.p1 or x.p2==y.p2:continue
                if any_only:return dict(polygons=[],area=1)
                kept.insert(pair)
            pairs=kept
        if any_only:return dict(polygons=[],area=0 if pairs.is_empty() else 1)
        a=pairs.polygons(1)
    elif op=='round':
        r=kdb.Region()
        for part in a.each():r.insert(part.round_corners(request['radius'],request['radius'],128))
        a=r
    elif op!='normalize':raise ValueError('Unknown native operation: '+op)
    area=float(a.area());a.merge()
    return dict(polygons=encode(a),area=area)
