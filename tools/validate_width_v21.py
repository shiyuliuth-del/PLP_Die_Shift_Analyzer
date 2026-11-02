"""Reproducible V21 landing air-gap checks; optional supplied GDS stays local."""
import argparse,sys,json,time,math,hashlib,itertools,platform
from pathlib import Path
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import read_gds,build_case,Parameters
from die_shift_studio.fast_preview import GeometryCache,PreviewEngine
from die_shift_studio.settings import ViewSettings
from die_shift_studio.resources import resource_profile
from die_shift_studio.preview import rigid_transform
from die_shift_studio.width_analysis import WidthAnalyzer

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--gds');ap.add_argument('--out',default='results/width_V21.json')
 ap.add_argument('--overlap',type=float,default=0.,help='落盘外扩 / μm（0=只用过孔本身）')
 ap.add_argument('--radius',type=float,default=100.,help='air-gap 局部查找半径 R / μm')
 ap.add_argument('--threshold',type=float,default=0.,help='air-gap 标注阈值 / μm（0=有效线距）')
 args=ap.parse_args();settings=ViewSettings(width_overlap_um=args.overlap,width_search_radius_um=args.radius,width_gap_threshold_um=args.threshold)
 records=[]
 for name in ['示例M_不同芯片底层与Pad随动','示例N_三线路窄通道宽度']:
  q=json.loads((ROOT/'examples'/(name+'.json')).read_text(encoding='utf-8'));d=read_gds(ROOT/'examples'/q['gds_path']);c=build_case(d,q['roles'],layer_order=q['layer_order']);p=Parameters.from_dict(q['parameters']);e=PreviewEngine(GeometryCache(d),c,q['roles'],{},p,{},c['connections']);e.configure_width(True,settings)
  output=dict(case=name,sha256=d.sha256,synthetic=True,owners={k:v.tolist() for k,v in c['ownership'].items()},root_planes=c['connections'].summary()['chip_terminal_planes_by_die'],states=[])
  for x,deform in [(0,False),(35,False),(-35,True)]:
   e.configure_deformation(deform,settings);z=np.zeros((len(c['dies']),3));z[0,0]=x;t=time.perf_counter();f=e.frame(z);secs=time.perf_counter()-t
   gates=[{k:v[k] for k in ('status','gap_um','land_radius_um','target')} for v in f['width_analysis']['rows'] if v['key']=='4/0' and v['index']==0]
   output['states'].append(dict(dx=x,deformation=deform,seconds=secs,counts=f['width_analysis']['counts'],accepted=f.get('deformation',{}).get('accepted_count'),example_landing=gates))
  e.close();records.append(output)
 if args.gds:
  d=read_gds(args.gds);roles={k:'不参与' for k in d.layers};roles.update({'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'});c=build_case(d,roles,layer_order=['6/0','5/0','4/0','3/0']);p=Parameters();e=PreviewEngine(GeometryCache(d),c,roles,{},p,None,c['connections'])
  output=dict(case=Path(args.gds).name,sha256=d.sha256,synthetic=False,die_count=len(c['dies']),root_planes=c['connections'].summary()['chip_terminal_planes_by_die'],motion_issues=c['connections'].ownership_notes,rigid_pose_count=8,max_vertex_error_um=0.,states=[])
  for signs in itertools.product((-1,1),repeat=3):
   z=np.tile(np.array(signs)*[35,35,math.pi/180],(len(c['dies']),1));f=e.frame(z)
   for k in ('3/0','4/0'):
    for i,owner in enumerate(c['ownership'][k]):
     assert owner>=0,(k,i,'unresolved owner')
     expected=rigid_transform(d.layers[k][i],e.bound.centers[owner],z[owner]);error=float(np.max(np.abs(f['layers'][k]['polygons'][i]-expected)))
     output['max_vertex_error_um']=max(output['max_vertex_error_um'],error);assert error<1e-8
  e.configure_width(True,settings);poses=[np.zeros((len(c['dies']),3)),np.tile([-35.,-35.,-math.pi/180],(len(c['dies']),1)),np.tile([35.,35.,math.pi/180],(len(c['dies']),1))]
  for z in poses:
   t=time.perf_counter();f=e.frame(z);elapsed=time.perf_counter()-t;analysis=f['width_analysis']
   t=time.perf_counter()
   with patch.object(WidthAnalyzer,'_measure',side_effect=AssertionError('Warm cache re-measured landings')):assert e.frame(z) is f
   repeat=time.perf_counter()-t
   output['states'].append(dict(states=z.tolist(),cold_seconds=elapsed,warm_seconds=repeat,counts=analysis['counts'],examined=analysis['examined'],total_vias=analysis['total_vias'],risky_landings=len(analysis['rows']),dimensioned=sum(r['draw'] for r in analysis['rows']),beyond_radius=analysis['beyond_radius']))
   print('GD55 air-gap',output['states'][-1],flush=True)
  t=time.perf_counter();quick=e.frame(poses[-1]*.5,quick=True);output['tracked_intermediate_seconds']=time.perf_counter()-t;output['tracked_intermediate_landings']=len(quick['width_analysis']['rows']);assert not quick['width_analysis']['complete']
  e.close();records.append(output)
 result=dict(version='V21',status='PASS',platform=platform.platform(),hardware=resource_profile(),settings=dict(overlap_um=settings.width_overlap_um,radius_um=settings.width_search_radius_um,threshold_um=settings.width_gap_threshold_um),records=records,scope='Local landing air-gap within the stated radius R, not global routing or complete DRC proof; Linux only; actual deformed geometry included for synthetic N.')
 path=ROOT/args.out;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(path)
if __name__=='__main__':main()
