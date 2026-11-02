"""Validate channel classifications and cached replays, optionally on local GD55."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

import numpy as np
import shapely
from shapely.strtree import STRtree
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters,build_case,read_gds
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.resources import resource_profile
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import ADEQUATE,NARROW,BLOCKED,WidthAnalyzer


def audit_neighbours(analysis):
    """Independent polygon-intersection oracle for all positive open rectangles."""
    checked=0
    for plane in analysis['planes']:
        rows=[r for r in analysis['rows'] if r['plane']==plane and r['gap_um']>1.e-6]
        pads=[o for o in analysis['retained'] if o['plane']==plane]
        if not rows or not pads:continue
        centers=np.array([r['point'] for r in rows]);axes=np.array([r['direction'] for r in rows])
        half=axes*(np.array([r['gap_um']/2 for r in rows])-1.e-7)[:,None]
        depth=.65*(analysis['parameters']['space_um']+analysis['parameters']['width_um'])-1.e-7
        sides=np.column_stack((-axes[:,1],axes[:,0]))*depth
        boxes=shapely.polygons(np.stack([centers-half-sides,centers+half-sides,centers+half+sides,centers-half+sides],axis=1))
        geoms=np.array([o['geometry'] for o in pads],object)
        ri,wi=STRtree(geoms).query(boxes,predicate='intersects')
        unrelated=[i for i,(r,w) in enumerate(zip(ri,wi)) if pads[w]['name'] not in rows[r]['pair']]
        if unrelated:
            r,w=ri[unrelated],wi[unrelated]
            areas=shapely.area(shapely.intersection(boxes[r],geoms[w]))
            bad=np.flatnonzero(areas>1.e-7)
            assert not len(bad),[(rows[r[i]]['pair'],pads[w[i]]['name'],areas[i]) for i in bad[:4]]
        checked+=len(rows)
    return checked


def inspect(data, roles, order, poses, sample=False,params=None):
    case=build_case(data,roles,layer_order=order)
    engine=PreviewEngine(GeometryCache(data),case,roles,{},params or Parameters(),None,case['connections'])
    engine.configure_width(True,ViewSettings())
    records=[]
    try:
        for dx,dy,angle in poses:
            states=np.tile([dx,dy,math.radians(angle)],(len(case['dies']),1))
            if sample:states[1:]=0
            start=time.perf_counter()
            f=engine.frame(states)
            cold=time.perf_counter()-start
            analysis=f['width_analysis']
            checked=audit_neighbours(analysis)
            assert all(len(r['slots_um'])<=1 and r['required']==len(r['widths_um']) for r in analysis['rows'])
            with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Completed pose recomputed')):
                start=time.perf_counter()
                cached=engine.frame(states)
                assert cached['width_analysis']['counts']==analysis['counts']
                warm=time.perf_counter()-start
            row=dict(state=[dx,dy,angle],sections=analysis['examined'],verified_open_rectangles=checked,counts=analysis['counts'],
                     retained=len(analysis['retained']),local_sections=sum(q['split'] for q in analysis['rows']),edge_violations=len(analysis.get('edge_violations',[])),first_request_s=round(cold,4),repeat_ms=round(warm*1000,3))
            if sample:
                gates=[r for r in analysis['rows'] if not r['split'] and r['required'] and r['pair']==('运动过孔孔盘 4/0 #1','运动过孔孔盘 4/0 #2')]
                expected={0:(3,ADEQUATE),3:(3,NARROW),35:(2,BLOCKED),-35:(4,ADEQUATE)}[dx]
                assert gates and all((r['original'],r['required'],r['current'],r['status'])==(3,3,*expected) for r in gates)
                row['central_gate']={k:gates[0][k] for k in ('nominal_gap_um','gap_um','original','required','current','status','widths_um','keep_um','minimum_um','narrowing_um')}
            records.append(row)
            print(row,flush=True)
    finally:
        engine.close()
    return dict(source=Path(data.path).name,sha256=data.sha256,die_count=len(case['dies']),states=records)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gds',type=Path,help='本机 GD55 图纸，保持只读')
    parser.add_argument('--out',type=Path,default=ROOT/'results/width_V24.json')
    args=parser.parse_args()
    name='示例O_运动网络三线路通道'
    q=json.loads((ROOT/'examples'/f'{name}.json').read_text(encoding='utf-8'))
    sample=inspect(read_gds(ROOT/'examples'/q['gds_path']),q['roles'],q['layer_order'],[(x,0,0) for x in (0,3,35,-35)],True)
    out=dict(version='V24',hardware=resource_profile(),sample=sample)
    source=ROOT/'examples/示例P_单点PATH与相邻孔盘.json'
    path_job=json.loads(source.read_text(encoding='utf-8'))
    drawing=read_gds(source.parent/path_job['gds_path'])
    out['path_example']=inspect(drawing,path_job['roles'],path_job['layer_order'],[(0,0,0),(3,0,0)],
                                params=Parameters.from_dict(path_job['parameters']))
    out['path_example']['import_warnings']=drawing.import_warnings
    if args.gds:
        roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
        data=read_gds(args.gds)
        roles={k:roles.get(k,'不参与') for k in data.layers}
        poses=[(0,0,0),*itertools.product((-35,35),(-35,35),(-1,1))]
        out['gd55']=inspect(data,roles,['6/0','5/0','4/0','3/0'],poses)
        assert hashlib.sha256(args.gds.read_bytes()).hexdigest()==data.sha256
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
