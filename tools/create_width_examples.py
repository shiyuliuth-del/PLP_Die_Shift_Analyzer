"""Portable V21 regressions: local chip terminal stacks and a three-wire throat."""
from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict
import numpy as np,gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings


def save(lib,cell,name,roles,order,description,parameters=None):
 path=ROOT/'examples'/(name+'.gds');lib.write_gds(path)
 p=parameters or Parameters(multi_bound_x_um=35,multi_bound_y_um=35)
 doc=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,layer_order=order,
  parameters=asdict(p),settings=asdict(ViewSettings()),visibility={k:v!='不参与' for k,v in roles.items()},synthetic=True,description=description)
 (ROOT/'examples'/(name+'.json')).write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 doc['gds_path']='../examples/'+path.name
 (ROOT/'Jobs'/(name+'.dshift.json')).write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return path


def create():
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('LOCAL_DIE_TERMINAL_STACKS')
 def rect(layer,bounds):cell.add(gdstk.rectangle(bounds[:2],bounds[2:],layer=layer))
 rect(127,(-1800,-1100,1800,1100));rect(128,(-1400,-400,-600,400));rect(128,(600,-400,1400,400))
 # Only DIE 1 has a plane-2 contact. DIE 2 starts at plane 3.
 rect(2,(-1020,-20,-980,20))
 cell.add(gdstk.FlexPath([(-1000,0),(-1000,650),(0,650)],20,layer=3,simple_path=True))
 cell.add(gdstk.FlexPath([(1000,0),(1400,0),(1400,650)],20,layer=3,simple_path=True))
 for x,y in ((0,650),(1400,650)):
  rect(4,(x-15,y-15,x+15,y+15));rect(5,(x-50,y-50,x+50,y+50));rect(6,(x-10,y-10,x+10,y+10))
 roles={f'{k}/0':v for k,v in [(127,'封装边框'),(128,'芯片边框'),(2,'运动过孔'),(3,'运动RDL'),(4,'运动过孔'),(5,'固定RDL'),(6,'固定过孔')]}
 order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0']
 first=save(lib,cell,'示例M_不同芯片底层与Pad随动',roles,order,'DIE1底部为2/0，DIE2底部为3/0；两个4/0 Pad都在芯片外，须沿各自真实连接随动。')
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('THREE_WIRE_BOTTLENECK')
 rect(127,(-1200,-900,1200,900));rect(128,(-1100,-300,-500,300));rect(128,(500,-300,1100,300))
 for x in (-800,800):rect(2,(x-15,-15,x+15,15))
 for a,b in ((-800,-100),(800,100)):cell.add(gdstk.FlexPath([(a,0),(b,0)],15,layer=3,simple_path=True))
 for x in (-100,100):
  rect(4,(x-30,-100,x+30,100));rect(5,(x-45,-115,x+45,115))
 for x in (-30,0,30):
  rect(5,(x-7.5,-500,x+7.5,500))
  for y in (-500,500):rect(6,(x-5,y-5,x+5,y+5))
 # A fixed metal obstruction also creates a Pad-to-fixed-graphic channel.
 rect(5,(300,250,430,390))
 second=save(lib,cell,'示例N_三线路窄通道宽度',roles,order,'15/15 μm工艺，两个Pad包围区间距110 μm；3条线需105 μm。DIE1 DX=35时通道75 μm，仅容2条；成功预演型变后DX=-35时145 μm，可容4条；未型变的旧固定铜仍占空间。',Parameters(multi_bound_x_um=35,multi_bound_y_um=35,annulus_um=15))
 return first,second
if __name__=='__main__':
 for p in create():print(p)
