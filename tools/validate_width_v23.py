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
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters,build_case,read_gds
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.resources import resource_profile
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import ADEQUATE,NARROW,BLOCKED,WidthAnalyzer


def inspect(data, roles, order, poses, sample=False):
    case=build_case(data,roles,layer_order=order)
    engine=PreviewEngine(GeometryCache(data),case,roles,{},Parameters(),None,case['connections'])
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
            with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Completed pose recomputed')):
                start=time.perf_counter()
                cached=engine.frame(states)
                assert cached['width_analysis']['counts']==analysis['counts']
                warm=time.perf_counter()-start
            row=dict(state=[dx,dy,angle],sections=analysis['examined'],counts=analysis['counts'],
                     retained=len(analysis['retained']),first_request_s=round(cold,4),repeat_ms=round(warm*1000,3))
            if sample:
                gates=[r for r in analysis['rows'] if r['pair']==('运动过孔孔盘 4/0 #1','运动过孔孔盘 4/0 #2')]
                expected={0:(3,ADEQUATE),3:(3,NARROW),35:(2,BLOCKED),-35:(4,ADEQUATE)}[dx]
                assert gates and all((r['original'],r['required'],r['current'],r['status'])==(3,3,*expected) for r in gates)
                row['central_gate']={k:gates[0][k] for k in ('nominal_gap_um','gap_um','original','required','current','status','widths_um','keep_um','minimum_um')}
            records.append(row)
            print(row,flush=True)
    finally:
        engine.close()
    return dict(source=Path(data.path).name,sha256=data.sha256,die_count=len(case['dies']),states=records)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gds',type=Path,help='本机 GD55 图纸，保持只读')
    parser.add_argument('--out',type=Path,default=ROOT/'results/width_V23.json')
    args=parser.parse_args()
    name='示例O_运动网络三线路通道'
    q=json.loads((ROOT/'examples'/f'{name}.json').read_text(encoding='utf-8'))
    sample=inspect(read_gds(ROOT/'examples'/q['gds_path']),q['roles'],q['layer_order'],[(x,0,0) for x in (0,3,35,-35)],True)
    out=dict(version='V23',hardware=resource_profile(),sample=sample)
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
