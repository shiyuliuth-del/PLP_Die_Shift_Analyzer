"""Read-only GD55 export audit; external GDS and DXF are never release assets."""
import argparse,hashlib,json,math,sys,tempfile
from pathlib import Path
import numpy as np
import shapely
import ezdxf
import klayout.db as k
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.stack import Connections
from die_shift_studio.dxf_export import snapshot,write


def verify(source):
    data=read_gds(source)
    used=dict(zip(('127/0','128/0','3/0','4/0','5/0','6/0'),
                  ('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
    roles={key:used.get(key,'不参与') for key in data.layers}
    cache=GeometryCache(data);connections=Connections(data,roles,['6/0','5/0','4/0','3/0'],cache.geometries)
    case=build_case(data,roles,connections=connections)
    engine=PreviewEngine(cache,case,roles,{},Parameters(),None,connections)
    try:
        pose=np.tile([-35.,-35.,math.radians(-1)],(len(case['dies']),1))
        frozen=snapshot(data,cache,engine.frame(pose))
    finally:engine.close()
    with tempfile.TemporaryDirectory() as folder:
        target=Path(folder)/'current.dxf';info=write(frozen,target)
        doc=ezdxf.readfile(target);assert not doc.audit().errors
        assert doc.units==13 and not list(doc.modelspace().query('ARC CIRCLE'))
        entities=list(doc.modelspace());n=0
        for key in sorted(frozen.layers,key=lambda v:tuple(map(int,v.split('/')))):
            for encoded in frozen.layers[key]:
                for polygon in shapely.get_parts(shapely.from_wkb(encoded)):
                    entity=entities[n];n+=1
                    assert entity.dxf.layer=='L'+key.replace('/','_D')
                    rings=[polygon.exterior,*polygon.interiors]
                    if len(rings)==1:
                        assert entity.dxftype()=='LWPOLYLINE' and entity.closed
                        np.testing.assert_array_equal(entity.get_points('xy'),np.array(rings[0].coords)[:-1])
                    else:
                        assert entity.dxftype()=='HATCH' and len(entity.paths)==len(rings)
                        for ring,loop in zip(rings,entity.paths):
                            np.testing.assert_array_equal([q.start for q in loop.edges],np.array(ring.coords)[:-1])
                            np.testing.assert_array_equal([q.end for q in loop.edges],np.array(ring.coords)[1:])
        assert n==len(entities)
        options=k.LoadLayoutOptions();options.dxf_polyline_mode=2;options.dxf_dbu=data.dbu_um
        layout=k.Layout();layout.read(str(target),options);rows=[]
        for key,items in frozen.layers.items():
            layer,datatype=map(int,key.split('/'))
            index=next(i for i in layout.layer_indices() if layout.get_info(i).layer==layer and layout.get_info(i).datatype==datatype)
            expected=k.Region()
            for encoded in items:
                for polygon in shapely.get_parts(shapely.from_wkb(encoded)):
                    shape=k.DPolygon([k.DPoint(*xy) for xy in polygon.exterior.coords[:-1]])
                    for ring in polygon.interiors:shape.insert_hole([k.DPoint(*xy) for xy in ring.coords[:-1]])
                    expected.insert(shape.to_itype(layout.dbu))
            actual=k.Region(layout.top_cell().begin_shapes_rec(index));difference=actual^expected
            # DXF's decimal reader and binary DPolygon rounding can disagree
            # at half-DBU ties. Keep the raw double node equality above strict;
            # report, rather than hide, any integer reader residual below.
            assert (actual-expected.sized(1)).is_empty() and (expected-actual.sized(1)).is_empty(),key
            rows.append(dict(layer=key,xor_dbu2=difference.area(),xor_um2=difference.area()*layout.dbu**2))
        assert hashlib.sha256(source.read_bytes()).hexdigest()==data.sha256
        return dict(status='PASS',scope='All exported double nodes equal; integer-reader differences bounded and reported',
                    gds_sha256=data.sha256,pose=[-35,-35,-1],layer_count=len(rows),output_polygons=info['polygons'],
                    integer_readback=rows,units=13,arc_entities=0,source_unchanged=True,exact_double_nodes=True,
                    all_readback_within_dbu=layout.dbu,klayout_version=k.__version__,ezdxf_version=ezdxf.__version__)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('gds',type=Path)
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    report=verify(args.gds);text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text,encoding='utf8')
    print(text)
