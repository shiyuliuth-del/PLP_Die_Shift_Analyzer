import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,shapely
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.statistics import ExtremeCatalog
parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);jobfile=parser.parse_args().project;job=normalize_project(json.loads(jobfile.read_text()));path=Path(job['gds_path']);path=path if path.is_absolute() else jobfile.parent/path
d=read_gds(path,job['top_cell']);d,job=restore_project_geometry(d,job);c=build_case(d,job['roles'],layer_order=job['layer_order']);cache=GeometryCache(d);p=Parameters.from_dict(job['parameters']);cfg=ViewSettings.from_dict(job['settings']);e=PreviewEngine(cache,c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,cfg);r=e.reconstructor
point=shapely.Point(8633,2284);refs={ref for ref in r.vias_by_target if r.model.nodes[r.model.ids[ref]]['poly'].distance(point)<60};assert refs,'no source at the specified case location';checks=[];r.vias_by_target={k:v for k,v in r.vias_by_target.items() if k in refs};catalog=ExtremeCatalog(c,p)
try:
 for n in range(len(catalog.locals[0])):
  states=np.asarray([v[n] for v in catalog.locals]);frame=r.frame(states,diagnostics=False,_raw=True)
  for q in frame['pieces']:
   copper=shapely.union_all(list(q['regions'].values()))
   for name in ('moved_cut','extra_cut'):
    cut=q.get(name)
    if cut is None or cut.is_empty or cut.distance(point)>200:continue
    outside=cut.difference(copper.buffer(.01)).length
    assert outside<.01,(n,q['layer'],q['index'],name,outside)
    assert cut.length<200,(n,name,cut.length)
    checks.append(dict(pose=n,source=[q['layer'],q['index']],side=name,length_um=cut.length,outside_um=outside))
 assert len(checks)>=27,len(checks)
 print(json.dumps(dict(status='PASS',scope='local source controls across 27 owner states; not whole-package DRC',checks=checks),ensure_ascii=False))
finally:e.close()
