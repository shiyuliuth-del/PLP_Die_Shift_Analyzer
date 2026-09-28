"""Small-shift enclosure repair and a folded trace requiring a remote cut."""
from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict
import gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

def create():
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('SMALL_SHIFT_AND_THREE_PART')
    def rect(layer,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
    rect(127,(-1400,-1500,1700,2000))
    for y,route in [(-850,[(0,-850),(1000,-850)]),(600,[(0,600),(0,950),(900,950),(900,350),(100,350),(100,600)])]:
        rect(128,(-900,y-300,-300,y+300));rect(2,(-610,y-10,-590,y+10))
        cell.add(gdstk.FlexPath([(-600,y),(0,y)],24,layer=3,simple_path=True));rect(4,(-10,y-10,10,y+10))
        wire=gdstk.FlexPath(route,40,joins='miter').to_polygons()
        pad=gdstk.rectangle((-18,y-18),(18,y+18));end=route[-1]
        shapes=wire+[pad,gdstk.rectangle((end[0]-30,end[1]-30),(end[0]+30,end[1]+30))]
        cell.add(*gdstk.boolean(shapes,[],'or',precision=.001,layer=5));rect(6,(end[0]-10,end[1]-10,end[0]+10,end[1]+10))
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    path=ROOT/'examples/示例G_小偏移与三段追溯.gds';lib.write_gds(path)
    p=Parameters(line_width_um=20,line_space_um=20,wafer_line_width_um=20,wafer_line_space_um=20,annulus_um=12,
        multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1)
    q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
        layer_order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0'],parameters=asdict(p),settings=asdict(ViewSettings()),synthetic=True,
        description='下方600×600μm芯片：DX=1,DY=1检验小偏移及源孔盘包围生长；上方芯片：DY=35，近处固定端阻止径向局部场，需沿折返铜线追溯到远端长直段；固定端不动。可按M进入右键平移/滚轮旋转。')
    path.with_suffix('.json').write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    q['gds_path']='../examples/'+path.name;(ROOT/'Jobs'/(path.stem+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    return path
if __name__=='__main__':print(create())
