"""GD55 joint-grid timing and full-frame event parity, using an external input only."""
import argparse,json,sys,time,platform,pickle,math
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('gds',type=Path);parser.add_argument('--implementation-root',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--output',type=Path,required=True);parser.add_argument('--bank',type=Path)
args=parser.parse_args();sys.path.insert(0,str(args.implementation_root))
from dataclasses import replace
import numpy as np
from die_shift_studio import __version__
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.stack import Connections
from die_shift_studio.contact import ContactModel
from die_shift_studio.settings import ViewSettings
from die_shift_studio.width_analysis import WidthAnalyzer,ADEQUATE
from die_shift_studio.width_keepout import routing_violations
from die_shift_studio.width_yield import measure_width_yield,policy_yields
from die_shift_studio.width_grid import weighted_pair
from die_shift_studio.extreme_filter import ProblemIndex
d=read_gds(args.gds)
used=dict(zip(('127/0','128/0','3/0','4/0','5/0','6/0'),('封装边框','芯片边框','运动RDL','运动过孔','固定RDL','固定过孔')))
roles={k:used.get(k,'不参与') for k in d.layers}
cache=GeometryCache(d);con=Connections(d,roles,['6/0','5/0','4/0','3/0'],cache.geometries);case=build_case(d,roles,connections=con)
p=Parameters();m=ContactModel(d,case,roles,con,cache,parameters=p)
cfg=replace(ViewSettings(),width_yield_method='grid',width_grid_nodes=5)
t=time.perf_counter();bank=measure_width_yield(m,p,cfg,None,lambda *_:None);elapsed=time.perf_counter()-t
q=policy_yields(bank,[('5/0',25)])
line_values={str(ref):weighted_pair(ProblemIndex(bank['grid'],clauses),bank['grid'].weights)[1] for ref,clauses in bank['by_line'].items()}
checks=0
if 'by_site' in bank:
    from die_shift_studio.width_event_ids import edge_keys
    from itertools import product
    a=WidthAnalyzer(m,p,cfg);a.probability=None
    try:
        grid=bank['grid'];poses=[np.zeros((grid.count,3))]
        corners=[np.array([x*35,y*35,o*math.pi/180]) for x,y,o in product((-1,1),repeat=3)]
        poses.extend(np.tile(z,(grid.count,1)) for z in corners)
        for owner in range(grid.count):
            for corner in corners[:4]:
                z=np.zeros((grid.count,3));z[owner]=corner;poses.append(z)
        for z in poses:
            digits=[next(i for i,v in enumerate(vs) if np.allclose(v,s,atol=1e-12,rtol=0)) for vs,s in zip(grid.locals,z)]
            lines=set();sites=set()
            for row in a.frame({'states':z})['rows']:
                if row['status']!=ADEQUATE:lines.update(row['refs']);sites.add(row['probability_key'])
            for row in routing_violations(m,z,p,cfg):lines.update((row['layer'],i) for i in row['indices']);sites.update(edge_keys(row))
            def predicted(mapping):
                return {key for key,clauses in mapping.items() if any(all(digits[owner] in allowed for owner,allowed in c.allowed) for c in clauses)}
            assert lines==predicted(bank['by_line']),(digits,'lines',lines^predicted(bank['by_line']))
            assert sites==predicted(bank['by_site']),(digits,'sites',sites^predicted(bank['by_site']))
            checks+=1
    finally:a.close()
record=dict(version=__version__,python=platform.python_version(),platform=platform.platform(),
 gds_sha256=d.sha256,parameters=dict(bounds_um=[35,35],angle_deg=1,width_space_enclosure_um=[15,15,15],sigma=6,nodes=5),
 grid_seconds=elapsed,grid_combinations=bank['total'],local_checks=bank['local_checks'],
 full_frame_checks=checks,site_count=len(bank.get('by_site',{})),line_probabilities=line_values,
 ignored_source_26_values=q['values'].tolist())
args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
if args.bank:
    with args.bank.open('wb') as f:pickle.dump(bank,f)
print(json.dumps(record,ensure_ascii=False))
