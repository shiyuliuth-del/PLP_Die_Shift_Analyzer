"""Frozen V18 worst-six interface audit + independent final-geometry checks."""
from pathlib import Path
import sys,json,math,time,hashlib,argparse,pickle
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import shapely
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.session_cache import ProjectCacheRegistry
from die_shift_studio.drc import rule_region
from die_shift_studio.deformation import angle_regions,snapped
from tools.validate_gd55_v14 import ROLES,ORDER

def verify(e,z,out,initial):
 dp=e.deformer;dbu=e.contact.data.dbu_um;p=e.p;snapshot=dp._snapshot(out['replacements']);count=0;repairs=[]
 final=e.contact.current(z,p,out['replacements']);hits={(r['source_layer'],r['source_index']) for r in final['issues']}
 assert not hits-initial,('new contact interfaces',hits-initial)
 for layer,values in out['replacements'].items():
  ids,gs,tree=snapshot[layer.split('/')[0]]
  for v in values:
   co=next(c for c in dp.components if c.key==layer and c.indices==tuple(v['indices']));g=v['geometry'];count+=1
   assert g.is_valid and g.geom_type=='Polygon'
   assert rule_region(g,15,dbu).is_empty,('width',co.indices)
   assert rule_region(g,15,dbu,'notch').is_empty,('internal spacing',co.indices)
   assert angle_regions(g,90,dbu).is_empty,('angle',co.indices)
   for j in tree.query(g,predicate='dwithin',distance=15):
    if e.contact.nodes[ids[j]]['net']!=v['net']:assert g.distance(gs[j])+dbu*.01>=15,('foreign spacing',co.indices,ids[j])
   meaningful=[];tiny=[]
   for ring in co.poly.interiors:
    h=shapely.Polygon(ring);x0,y0,x1,y1=h.bounds
    (tiny if max(x1-x0,y1-y0)<=.05 else meaningful).append(h)
   assert len(g.interiors)>=len(meaningful),('manufacturing-scale holes lost',co.indices)
   if len(g.interiors)<len(co.poly.interiors):repairs.append(dict(indices=co.indices,holes=[dict(area_um2=h.area,bounds=h.bounds) for h in tiny]))
   chosen=set(map(tuple,v['adapted_vias']))
   for a in co.anchors:assert g.covers(a),('fixed anchor',co.indices)
   for r in co.records:
    if (r['key'],r['index']) in chosen:
     via=e.contact.moved(r['poly'],r['owner'],z)
     assert g.covers(via) and g.boundary.distance(via)+dbu*.01>=15,('enclosure',r['index'])
    else:assert g.covers(r['poly']),('held nominal terminal',r['index'])
   reference=snapped(co.poly,dbu)[0]
   for r in e.contact.records:
    if r['net']==co.net or layer not in r['target_layers']:continue
    control=e.contact.moved(r['poly'],r['owner'],z).buffer(30,quad_segs=32)
    inherited=reference.intersection(control)
    assert g.intersection(control).difference(inherited.buffer(dbu)).is_empty,('added control intrusion',co.indices,r['index'])
 return dict(verified_components=count,new_contact_interfaces=0,source_microhole_repairs=repairs,contact_count_after=len(hits))

def run(args):
 baseline=json.loads((ROOT/'results/baseline_interfaces_V19.json').read_text());selected=baseline['selected_six']
 d=read_gds(args.gds);c=build_case(d,ROLES,layer_order=ORDER);p=Parameters(multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1,line_width_um=15,line_space_um=15,annulus_um=15)
 registry=ProjectCacheRegistry();group=registry.get(d.sha256);e=PreviewEngine(GeometryCache(d),c,ROLES,{},p,{},c['connections'],cache_group=group)
 cfg=ViewSettings();e.configure_deformation(True,cfg);e.configure_drc(True,cfg);rows=[];poses=[]
 try:
  for signs in selected:
   z=np.tile(np.array(signs)*[35.,35.,math.pi/180],(3,1));poses.append(z)
   start=time.perf_counter();frame=e.frame(z);seconds=time.perf_counter()-start;out=frame['deformation']
   original=e.contact.current(z,p);hits={(r['source_layer'],r['source_index']) for r in original['issues']}
   outcome=[dict(r,initial_contact=(r['via_layer'],r['via_index']) in hits) for r in out['interfaces']]
   assert {(r['via_layer'],r['via_index']) for r in outcome}=={(r['key'],r['index']) for r in e.contact.records},'Missing interface outcomes'
   missing=[r for r in outcome if not r['initial_contact'] and r['status']!='已适配']
   if args.dump:
    args.dump.mkdir(parents=True,exist_ok=True);pickle.dump(frame,open(args.dump/(''.join('p' if v>0 else 'm' for v in signs)+'.pkl'),'wb'))
   row=dict(signs=signs,seconds=seconds,noncontact_missed=missing,noncontact_missed_count=len(missing),noncontact_count=sum(not r['initial_contact'] for r in outcome),accepted_components=out['accepted_count'],adapted_interfaces=out['adapted_interface_count'],partial_components=out['partial_component_count'],interfaces=outcome,diagnostics=out['diagnostics'])
   try:row['geometry_audit']=verify(e,z,out,hits)
   except AssertionError as exc:row['audit_error']=str(exc)
   rows.append(row);args.out.write_text(json.dumps(dict(source_sha256=d.sha256,parameters=dict(bound_x_um=35,bound_y_um=35,bound_angle_deg=1,width_um=15,space_um=15,annulus_um=15,drc_tolerance_um=0,minimum_angle_deg=90,curve_tolerance_um=.05),selected_six=selected,scope='Frozen six synchronous sign extrema; all nominal moving-via/fixed-RDL interfaces. Not a continuous or all cross-chip-combination proof.',rows=rows),ensure_ascii=False,indent=2)+'\n')
   print('STATE',signs,'seconds',round(seconds,3),'missed',len(missing),'AUDIT',row.get('audit_error','PASS'),flush=True)
  timings=[]
  with patch.object(e.deformer,'search',side_effect=AssertionError('Cache recomputed')):
   for _ in range(3):
    for z in poses:
     start=time.perf_counter();e.frame(z);timings.append((time.perf_counter()-start)*1000)
  summary=json.loads(args.out.read_text());summary['cache_revisits']=dict(count=len(timings),median_ms=float(np.median(timings)),maximum_ms=max(timings),file_bytes=group.bytes)
  summary['original_gds_unchanged']=hashlib.sha256(args.gds.read_bytes()).hexdigest()==d.sha256
  summary['all_six_noncontact_interfaces_adapted']=all(r['noncontact_missed_count']==0 and 'audit_error' not in r for r in rows)
  args.out.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
  assert summary['all_six_noncontact_interfaces_adapted'],'Unadapted no-contact interfaces or final geometry audit failure'
 finally:e.close();registry.close()
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--gds',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--dump',type=Path);run(a.parse_args())
