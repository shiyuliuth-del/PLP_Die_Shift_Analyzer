"""Read-only GD55 throat, route and duplicate audit; publish only aggregate evidence."""
import argparse
import itertools
import json
import math
import sys
import time
from pathlib import Path
from unittest.mock import patch
import numpy as np
import shapely
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.validate_width_v24 import audit_neighbours
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import WidthAnalyzer,ADEQUATE
from die_shift_studio.resources import resource_profile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'results/width_V25.json')
    args=parser.parse_args()
    data=read_gds(args.gds)
    known={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    roles={k:known.get(k,'不参与') for k in data.layers}
    c=build_case(data,roles,layer_order=['6/0','5/0','4/0','3/0'])
    engine=PreviewEngine(GeometryCache(data),c,roles,{},Parameters(),None,c['connections'])
    engine.configure_width(True,ViewSettings())
    focus=np.array([-1346.5,-464.5]);states=[]
    try:
        for dx,dy,angle in [(0,0,0),*itertools.product((-35,35),(-35,35),(-1,1))]:
            z=np.tile([dx,dy,math.radians(angle)],(len(c['dies']),1));start=time.perf_counter()
            a=engine.frame(z)['width_analysis'];cold=time.perf_counter()-start
            checked=audit_neighbours(a)
            max_transverse=0.;max_end_error=0.;target=[]
            for plane in a['planes']:
                walls=engine.width_analyzer.planes[plane]['walls']
                copper={w.name:(w.geometry if w.owner<0 else engine.contact.moved(w.geometry,w.owner,z)) for w in walls}
                rows=[r for r in a['rows'] if r['plane']==plane]
                sites=np.array([r['witness_um'] for r in rows]);nominals=np.array([r['nominal_witness_um'] for r in rows])
                axes=np.array([r['direction'] for r in rows]);vec=sites[:,1]-sites[:,0]
                np.testing.assert_allclose(sites.mean(axis=1),[r['point'] for r in rows],atol=1e-8)
                max_transverse=max(max_transverse,float(np.max(abs(vec[:,0]*axes[:,1]-vec[:,1]*axes[:,0]))))
                left,right=([copper[r['pair'][i]] for r in rows] for i in (0,1))
                max_end_error=max(max_end_error,float(np.max(shapely.distance(left,shapely.points(sites[:,0])))),
                                  float(np.max(shapely.distance(right,shapely.points(sites[:,1])))))
                assert max_end_error<=1e-6 and max_transverse<=1e-6
                assert all(r['required']>0 and r['refs'] for r in rows)
                # All named source conductors must really meet their *nominal*
                # transverse witness. The target pose cannot create demand.
                idx=np.repeat(np.arange(len(rows)),[len(r['refs']) for r in rows])
                sources=[engine.contact.cache.layers[k]['geometry'][i] for r in rows for k,i in r['refs']]
                cuts=shapely.linestrings(nominals[idx])
                assert np.all(shapely.length(shapely.intersection(sources,cuts))>1e-8)
                for r in rows:
                    if np.linalg.norm(np.asarray(r['point'])-focus)<65 and r['status']!=ADEQUATE:
                        target.append({k:r[k] for k in ('pair','status','refs','required','point','witness_um','gap_um')})
            if (dx,dy,angle)==(-35,-35,-1):
                assert len(target)==1 and target[0]['required']==2 and {s.split('#')[-1] for s in target[0]['pair']}=={'67','102'},target
                q=target[0]
                np.testing.assert_allclose(q['gap_um'],copper[q['pair'][0]].distance(copper[q['pair'][1]]),atol=1e-7)
            with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('cached case recomputed')):
                start=time.perf_counter();assert engine.frame(z)['width_analysis']['counts']==a['counts'];warm=time.perf_counter()-start
            states.append(dict(state=[dx,dy,angle],counts=a['counts'],sections=len(a['rows']),verified_open_rectangles=checked,
                               endpoint_error_um=max_end_error,axis_cross_error_um=max_transverse,focus_risks=target,
                               first_request_s=round(cold,3),repeat_ms=round(1000*warm,3)))
            print((dx,dy,angle),a['counts'],len(target),flush=True)
    finally:engine.close()
    out=dict(version='V25',source_sha256=data.sha256,die_count=len(c['dies']),hardware=resource_profile(),
             focus_um=focus.tolist(),focus_radius_um=65,process_um=dict(width=15,space=15,enclosure=15),
             v24_focus_risks_minus_minus_minus=4,states=states)
    args.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
