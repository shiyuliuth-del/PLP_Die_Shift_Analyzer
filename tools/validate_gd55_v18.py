"""Read-only full-copper, enclosure and anchor audit on requested GD55 poses."""
from pathlib import Path
import sys,math,json,time,itertools,argparse,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import shapely
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.drc import rule_region
from die_shift_studio.deformation import angle_regions
from tools.validate_gd55_v14 import ROLES,ORDER

def run():
 parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--out',type=Path,default=ROOT/'results/gd55_sweep_V18.json');parser.add_argument('--only-user-state',action='store_true');args=parser.parse_args()
 d=read_gds(args.gds);c=build_case(d,ROLES,layer_order=ORDER);p=Parameters(multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1,line_width_um=15,line_space_um=15,annulus_um=15)
 e=PreviewEngine(GeometryCache(d),c,ROLES,{},p,{},c['connections']);cfg=ViewSettings();e.configure_deformation(True,cfg);e.configure_drc(True,cfg);rows=[]
 poses=[np.tile(np.array(v)*[35,35,math.pi/180],(3,1)) for v in itertools.product((-1,1),repeat=3)]
 poses += [np.array([[-35,-35,-math.pi/180],[35,35,math.pi/180],[0,0,0]]),np.array([[0,0,0],[-35,35,-math.pi/180],[35,-35,math.pi/180]])]
 if args.only_user_state:poses=poses[:1]
 for i,z in enumerate(poses):
  start=time.perf_counter();frame=e.frame(z);seconds=time.perf_counter()-start;out=frame['deformation'];snapshot=e.deformer._snapshot(out['replacements'])
  accepted=[];modes={}
  for key,vs in out['replacements'].items():
   ids,gs,tree=snapshot[key.split('/')[0]]
   for v in vs:
    g=v['geometry'];assert g.is_valid and v['verified']
    assert rule_region(g,15,d.dbu_um).is_empty
    assert rule_region(g,15,d.dbu_um,'notch').is_empty
    assert angle_regions(g,90,d.dbu_um).is_empty
    for j in tree.query(g,predicate='dwithin',distance=15):
     if e.contact.nodes[ids[j]]['net']!=v['net']:assert g.distance(gs[j])+d.dbu_um*.01>=15
    co=next(co for co in e.deformer.components if co.key==key and co.indices==v['indices'])
    assert len(g.interiors)==len(co.poly.interiors)
    for anchor in co.anchors:assert g.covers(anchor)
    for record in co.records:
     moved=e.contact.moved(record['poly'],record['owner'],z)
     assert g.covers(moved) and g.boundary.distance(moved)+d.dbu_um*.01>=15
    accepted.append(list(v['indices']));modes[v['mode']]=modes.get(v['mode'],0)+1
  if i==0:
   required={22,104,110,112}
   assert required.issubset({v[0] for v in accepted})
   baseline_path=ROOT/'results/baseline_sweep_V18.json'
   if baseline_path.is_file():
    prior=json.loads(baseline_path.read_text(encoding='utf-8'))['rows'][0]['accepted']
    assert {tuple(v) for v in prior}.issubset({tuple(v) for v in accepted})
  rows.append(dict(states=z.tolist(),seconds=seconds,accepted=accepted,modes=modes,unadapted=out['unresolved_count'],audit='PASS'))
  args.out.write_text(json.dumps(dict(sha256=d.sha256,parameters=dict(width_um=15,space_um=15,annulus_um=15,bound_xy_um=35,bound_angle_deg=1),scope='Eight synchronous extrema plus two mixed-chip states; not all continuous or 512 cross-chip extreme combinations',rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
  print('STATE',i,'ACCEPTED',len(accepted),'SECONDS',seconds,flush=True)
  if i==0:
   Path(str(args.out)+'.geometry.json').write_text(json.dumps({str(v['indices'][0]):shapely.to_wkt(v['geometry'],rounding_precision=5) for vs in out['replacements'].values() for v in vs}),encoding='utf-8')
 assert hashlib.sha256(args.gds.read_bytes()).hexdigest()==d.sha256
 e.close()
if __name__=='__main__':run()
