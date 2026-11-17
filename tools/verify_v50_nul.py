"""User-provided nul7123 local branch and handle regression; never ships its GDS."""
import sys,json,argparse,time
from pathlib import Path
from itertools import product
import numpy as np,shapely
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.project import normalize_project
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.branch_regions import editable_piece
parser=argparse.ArgumentParser();parser.add_argument('project',type=Path);args=parser.parse_args();job=normalize_project(json.loads(args.project.read_text()));path=Path(job['gds_path']);path=path if path.is_absolute() else args.project.parent/path
start=time.monotonic();d=read_gds(path,job['top_cell']);d,job=restore_project_geometry(d,job);c=build_case(d,job['roles'],layer_order=job['layer_order']);p=Parameters.from_dict(job['parameters']);cache=GeometryCache(d);e=PreviewEngine(cache,c,job['roles'],{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings.from_dict(job['settings']));r=e.reconstructor
points=[(7253,3076),(6548,3747),(8350,2553),(6661,4123),(8376,2568)];refs=set()
for point in points:
 ref=min(r.vias_by_target,key=lambda ref:r.model.nodes[r.model.ids[ref]]['poly'].distance(shapely.Point(point)));refs.add(ref)
r.vias_by_target={ref:v for ref,v in r.vias_by_target.items() if ref in refs};records=[]
try:
 for signs in product((0,-1,1),repeat=3):
  states=np.tile(np.array(signs)*[p.multi_bound_x_um,p.multi_bound_y_um,np.radians(p.bound_angle_deg)],(len(c['dies']),1));frame=r.frame(states,diagnostics=False,_raw=True)
  for q in frame['pieces']:
   copper=shapely.union_all(list(q['regions'].values()))
   for name in ('moved_cut','extra_cut'):
    cut=q.get(name)
    if cut is None or cut.is_empty or cut.geom_type in ('Point','MultiPoint'):continue
    outside=cut.difference(copper.buffer(.01)).length
    assert outside<.02,(signs,q['index'],name,outside)
   if not np.any(states):
    for kind,name in (('moved','moved_cut'),('extra','extra_cut')):
     g=q.get(name)
     if g is None or g.is_empty:continue
     xy=g.centroid.coords[0];m=editable_piece(q,xy,kind);plan=m['route_plan'];side=int(kind=='extra')
     outcomes=[plan.edit(*m['interval'],side,m['interval'][side]+step)[0] for step in (-30,30)]
     assert any(v is not None and (v['a'],v['b'])!=m['interval'] for v in outcomes),(q['index'],kind,'both directions locked')
  records.append(dict(signs=signs,pieces=len(frame['pieces']),valid_copper=all(q['geometry'].is_valid for rows in frame['replacements'].values() for q in rows)))
 assert all(q['valid_copper'] for q in records)
 print(json.dumps(dict(status='V50_NUL_LOCAL_PASS',scope='reported four source geometries, 27 owning-DIE local poses; handles and material, not whole-package DRC certification',sources=sorted(refs),seconds=time.monotonic()-start,poses=len(records)),ensure_ascii=False))
finally:e.close()
