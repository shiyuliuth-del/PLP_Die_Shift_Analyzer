"""Portable engineering example: linked vias outside every chip boundary."""
import sys,json,hashlib
from pathlib import Path
import gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dataclasses import asdict
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings


def create():
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('FANOUT_OUTSIDE_DIE')
    def rect(layer,x0,y0,x1,y1):cell.add(gdstk.rectangle((x0,y0),(x1,y1),layer=layer))
    rect(127,-2100,-1700,2500,1700)
    rect(128,-1600,-600,-400,600);rect(128,600,-500,1600,500)
    # Lowest metal contact roots inside each DIE. No nominal via overlaps the foreign net.
    for x,y in [(-1000,0),(1100,0)]:rect(2,x-18,y-18,x+18,y+18)
    for points in [[(-1000,0),(-1000,1000),(0,1000)],[(1100,0),(2100,0),(2100,-900)]]:
        cell.add(gdstk.FlexPath(points,18,layer=3,simple_path=True))
    # DIE 1 has a rectangular fanout via; DIE 2 has a wide polygonal via.
    rect(4,-20,988,20,1012);cell.add(gdstk.ellipse((2100,-900),radius=(24,15),layer=4,tolerance=.1))
    rect(5,-60,958,60,1042);rect(5,88,958,130,1042)
    rect(5,2040,-945,2160,-855);rect(5,2192,-945,2235,-855)
    rect(6,-12,994,12,1006);rect(6,2090,-910,2110,-890)
    rect(122,-2050,-1650,2450,1650)
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔','122/0':'非金属图形'}
    for i,(key,role) in enumerate(roles.items()):cell.add(gdstk.Label(role,(0,i*10),layer=int(key.split('/')[0])))
    name='示例D_芯片外扇出';path=ROOT/'examples'/(name+'.gds');lib.write_gds(path)
    pars=Parameters(multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1,annulus_um=8,line_space_um=8,interdie='none',transition_length_um=400)
    project=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,
        roles=roles,layer_order=['6/0','5/0','4/0','3/0','2/0','127/0','128/0','122/0'],parameters=asdict(pars),settings=asdict(ViewSettings()),
        visibility={k:r!='非金属图形' for k,r in roles.items()},synthetic=True,
        description='两颗DIE的运动过孔均通过下层RDL引到芯片外；4/0的归属按连接为DIE1、DIE2。手动给DIE1设DX=65、DY=0、O=0，方案3可见控制区侵入5/0其他网络。')
    text=lambda q:json.dumps(q,ensure_ascii=False,indent=2)+'\n'
    (ROOT/'examples'/(name+'.json')).write_text(text(project),encoding='utf-8')
    project['gds_path']='../examples/'+path.name
    (ROOT/'Jobs'/(name+'.dshift.json')).write_text(text(project),encoding='utf-8')
    return path
if __name__=='__main__':print(create())
