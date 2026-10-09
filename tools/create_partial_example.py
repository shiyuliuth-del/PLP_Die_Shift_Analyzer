"""Portable shared-copper partial adaptation and inherited contact case."""
from pathlib import Path
import hashlib,json,sys
from dataclasses import asdict
import gdstk
import numpy as np
from shapely.geometry import Point,LineString
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

def create():
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('SHARED_NET_PARTIAL_ADAPTATION')
    def rect(layer,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
    def shape(layer,g):cell.add(gdstk.Polygon(np.asarray(g.exterior.coords),layer=layer))
    rect(127,(-1400,-600,900,1200));rect(128,(-1000,-400,-500,100));rect(128,(-1000,450,-500,950))
    source=LineString([(-200,0),(200,0)]).buffer(15).union(Point(-200,0).buffer(40)).union(Point(200,0).buffer(40))
    shape(5,source);shape(6,Point(0,0).buffer(12));rect(5,(150,55,260,95))
    for origin,via in [((-750,-150),(-200,0)),((-750,-50),(200,0)),((-750,700),(400,0))]:
        shape(2,Point(origin).buffer(10));shape(4,Point(via).buffer(15,quad_segs=32))
        cell.add(gdstk.FlexPath([origin,via],20,layer=3,simple_path=True))
    shape(5,LineString([(400,0),(500,0)]).buffer(30));shape(6,Point(500,0).buffer(12))
    path=ROOT/'examples/示例L_同网部分适配与既有接触.gds';lib.write_gds(path)
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
        layer_order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0'],parameters=asdict(Parameters(per_die_enabled=True,die_bounds=[[35,35,1],[200,0,0]])),settings=asdict(ViewSettings()),synthetic=True,
        description='两个500×500 μm芯片。手动设置DIE1为(0,35,0°)、DIE2为(−200,0,0°)：左侧孔不接触；同一固定铜的右孔接触上方障碍，另一网络的运动孔也在右侧形成既有接触。左孔应独立完成适配，右侧老问题仍标红，禁止新增或扩大侵入。')
    path.with_suffix('.json').write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');q['gds_path']='../examples/'+path.name
    (ROOT/'Jobs'/(path.stem+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return path
if __name__=='__main__':print(create())
