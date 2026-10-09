"""Per-interface audit: nominal foreign-contact status and adaptation outcome."""
from pathlib import Path
import argparse,json,sys,time,math,itertools
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from tools.validate_gd55_v14 import ROLES,ORDER

def run(path,out):
 data=read_gds(path);case=build_case(data,ROLES,layer_order=ORDER);p=Parameters(multi_bound_x_um=35,multi_bound_y_um=35,line_width_um=15,line_space_um=15,annulus_um=15,bound_angle_deg=1)
 e=PreviewEngine(GeometryCache(data),case,ROLES,{},p,{},case['connections']);cfg=ViewSettings();e.configure_deformation(True,cfg);e.configure_drc(True,cfg)
 rows=[]
 try:
  for signs in itertools.product((-1,1),repeat=3):
   z=np.tile(np.array(signs)*[35.,35.,math.pi/180],(3,1));original=e.contact.current(z,p)
   hits={(q['source_layer'],q['source_index']) for q in original['issues']};start=time.perf_counter();frame=e.frame(z);seconds=time.perf_counter()-start;d=frame['deformation'];missed=[]
   for row in d['interfaces']:
    via=(row['via_layer'],row['via_index']);record=e.contact.by_feature[via]
    if via not in hits and row['status'].startswith('未'):
     co=next(c for c in e.deformer.components if c.key==row['layer'] and c.indices==tuple(row['indices']))
     missed.append(dict(row,center=list(record['center']),moved_center=list(e.contact.moved(record['poly'],record['owner'],z).centroid.coords[0]),
         component_records=len(co.records),component_contacting_records=[r['index'] for r in co.records if (r['key'],r['index']) in hits],baseline_drc=e.deformer.component_base[co.key,co.indices]))
   item=dict(signs=signs,seconds=seconds,accepted_components=d['accepted_count'],original_contact_vias=len(hits),noncontact_missed=missed,noncontact_missed_count=len(missed),diagnostics=d['diagnostics'])
   rows.append(item);print('STATE',signs,'accepted',d['accepted_count'],'noncontact-missed',len(missed),flush=True)
   out.write_text(json.dumps(dict(source_sha256=data.sha256,rows=rows,selected_six=[v['signs'] for v in sorted(rows,key=lambda q:q['noncontact_missed_count'],reverse=True)[:6]]),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 finally:e.close()
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--gds',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);a=parser.parse_args();run(a.gds,a.out)
