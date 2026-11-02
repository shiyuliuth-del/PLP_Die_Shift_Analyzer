"""Reproduce recorded 1102 boundary findings; --strict fails while unresolved."""
import sys,json,numpy as np,shapely
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.test_reconstruction import example,shared,BRIDGE,row_for
from die_shift_studio.settings import ViewSettings
from die_shift_studio.core import Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.repair_patch import repair_summary,new_violations
out={}
e,d,c=example(BRIDGE,settings=ViewSettings(width_search_radius_um=10.))
try:
 z=np.array([[35.,0,0],[-35.,0,0]]);f=e.frame(z);mapping={(k,i):q['geometry'] for k,vals in f['reconstruction']['replacements'].items() for q in vals for i in q['indices']};checks=[]
 for v in e.width_analyzer.vias:
  if v['key']!='4/0':continue
  _,land=e.width_analyzer._land(v,z);dist=[]
  for layer in v['targets']:
   pool=e.width_analyzer.pools[layer]
   for i,g in enumerate(pool['geoms']):
    if pool['nets'][i]!=v['net']:dist.append(float(land.distance(mapping.get((layer,i),g))))
  actual=min(dist) if dist else None;row=row_for(f,v['key'],v['index']+1)
  checks.append(dict(via=v['index']+1,reported_gap_um=row['gap_um'],reported_status=row['status'],independent_current_geometry_gap_um=actual,radius_um=10))
 out['airgap_current_candidate_index']=checks
finally:e.close()
e,d=shared();c=e.bound.case;roles=e.roles;p=replace(e.p,rotation_center='custom',rotation_x_um=3000.);e.close()
e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections']);e.configure_reconstruction(True,ViewSettings())
try:
 selected=e.reconstructor._own_frame(0);f=e.frame(np.array([[35.,0,0],[-35.,0,0]]));areas=[]
 for q in f['reconstruction']['pieces']:
  landings=[v['land'] for v in e.reconstructor.vias if v['owner']==q['die'] and v['index'] in q['vias']]
  allowed=shapely.union_all([c['dies'][q['die']],*landings]);areas.append(dict(die=q['die']+1,outside_own_die_and_landing_um2=float(q['movable'].difference(allowed).area)))
 out['custom_pivot_frame_selection']=dict(rotation_x_um=3000.,owner0_frame_found=selected is not None,areas=areas)
finally:e.close()
g=shapely.union_all([shapely.box(0,0,10,10),shapely.box(20,0,30,10)]);markers=[dict(kind='线距',geometry=g)];added=new_violations([],markers);summary=repair_summary([],markers,additions=added)
out['repair_preexisting_count']=dict(post_marker_count=len(markers),added_region_count=len(added),reported_preexisting=summary['preexisting'])
out['status']='KNOWN_FAILURES' if (any(q['reported_gap_um']!=q['independent_current_geometry_gap_um'] for q in out['airgap_current_candidate_index']) or not out['custom_pivot_frame_selection']['owner0_frame_found'] or out['repair_preexisting_count']['reported_preexisting']<0) else 'PASS'
path=ROOT/'results/1102_edge_audit.json';path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))

if "--strict" in sys.argv and out["status"]!="PASS":raise SystemExit(1)
