"""Portable synthetic middle connection and short smooth landing island."""
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
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('MIDDLE_CONNECTION_SMOOTH_PAD')
    def rect(layer,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
    def shape(layer,g):cell.add(gdstk.Polygon(np.asarray(g.exterior.coords),layer=layer))
    rect(127,(-1200,-800,800,800));rect(128,(-1000,-500,0,500))
    rect(5,(-245,-300,-155,300))
    for y in (-240,240):shape(6,Point(-200,y).buffer(15,quad_segs=32))
    for root,via in [((-700,0),(-200,0)),((-700,350),(350,120))]:
        shape(2,Point(root).buffer(12));shape(4,Point(via).buffer(20,quad_segs=32))
        cell.add(gdstk.FlexPath([root,via],25,layer=3,simple_path=True))
    shape(5,LineString([(350,0),(350,120)]).buffer(50,quad_segs=32));shape(6,Point(350,0).buffer(20,quad_segs=32))
    path=ROOT/'examples/示例K_中段连接与平滑孔盘.gds';lib.write_gds(path)
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
        layer_order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0'],parameters=asdict(Parameters(single_bound_x_um=35,single_bound_y_um=35)),settings=asdict(ViewSettings()),synthetic=True,
        description='1000×1000 μm芯片。左侧运动孔位于固定RDL中段，双侧固定过孔都必须保持连接；右侧短孔盘在−35/−35/−1°状态需要光滑轮廓重建。原始附件几何未包含在本例中。')
    path.with_suffix('.json').write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');q['gds_path']='../examples/'+path.name
    (ROOT/'Jobs'/(path.stem+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return path
if __name__=='__main__':print(create())
