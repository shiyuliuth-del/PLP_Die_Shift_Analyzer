"""Two fanout nets: moving the second landing frees the initially blocked first."""
from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict
import gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

def create():
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('ITERATIVE_WINDOWS')
    def rect(layer,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=layer))
    rect(127,(-2000,-1300,2000,1300))
    for center,pad,end in [((-600,-700),(0,0),(-1200,0)),((600,700),(100,0),(1300,0))]:
        x,y=center;vx,vy=pad;ex,ey=end
        rect(128,(x-300,y-300,x+300,y+300));rect(2,(x-10,y-10,x+10,y+10))
        cell.add(gdstk.FlexPath([center,pad],16,layer=3,simple_path=True));rect(4,(vx-10,vy-10,vx+10,vy+10))
        shapes=[gdstk.rectangle((vx-30,vy-30),(vx+30,vy+30)),gdstk.rectangle((min(vx,ex),-16),(max(vx,ex),16)),gdstk.rectangle((ex-30,-30),(ex+30,30))]
        cell.add(*gdstk.boolean(shapes,[],'or',precision=.001,layer=5));rect(6,(ex-10,-10,ex+10,10))
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    path=ROOT/'examples/示例F_逐芯片幅度与迭代窗口.gds';lib.write_gds(path)
    p=Parameters(line_width_um=10,line_space_um=10,annulus_um=5,multi_bound_x_um=80,multi_bound_y_um=5,bound_angle_deg=0,
        per_die_enabled=True,die_bounds=[[80,5,0],[100,8,0]])
    q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
        layer_order=['6/0','5/0','4/0','3/0','2/0','128/0','127/0'],parameters=asdict(p),settings=asdict(ViewSettings()),synthetic=True,
        description='两个DIE的动孔通过各自运动RDL扇出到芯片外。两DIE均手动DX=80时，先适配右孔盘可释放左孔窗口，然后迭代适配左孔。逐芯片输入最大幅度不同。')
    path.with_suffix('.json').write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    q['gds_path']='../examples/'+path.name;(ROOT/'Jobs'/(path.stem+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    return path
if __name__=='__main__':print(create())
