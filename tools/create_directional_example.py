"""Synthetic short wide pad plus an isolated 14.8 um DRC tolerance coupon."""
from pathlib import Path
import json,sys,hashlib
from dataclasses import asdict
import gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

def create():
 lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('DIRECTIONAL_PAD_AND_TOLERANCE')
 def rect(layer,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
 rect(127,(-1000,-700,600,700));rect(128,(-700,-300,-200,300));rect(2,(-460,-10,-440,10))
 cell.add(gdstk.FlexPath([(-450,0),(0,0)],30,layer=3,simple_path=True));rect(4,(-10,-10,10,10))
 rect(5,(-45,-45,45,125));rect(6,(-10,70,10,90));rect(5,(250,-100,264.8,100))
 roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
 path=ROOT/'examples/示例J_短宽孔盘与DRC容差.gds';lib.write_gds(path)
 q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
  layer_order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0'],parameters=asdict(Parameters()),settings=asdict(ViewSettings()),synthetic=True,
  description='500×600μm单DIE，短宽孔盘及近端固定孔。预览DX=-25,DY=-25,O=0可见方向性孔盘过渡，固定端不动；右侧孤立14.8μm测试铜条在默认15μm规则下报线宽不足，容差+0.3时消失、-0.3时收紧。该测试铜条不接运动孔。')
 path.with_suffix('.json').write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n');q['gds_path']='../examples/'+path.name
 (ROOT/'Jobs'/(path.stem+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n');return path
if __name__=='__main__':print(create())
