"""Three fanout cases: adaptive route, foreign-net contact, original narrow neck."""
from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict
import gdstk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings

def create():
    lib=gdstk.Library(unit=1e-6,precision=1e-9);cell=lib.new_cell('DEFORMATION_CASES')
    def rect(l,b):cell.add(gdstk.rectangle(b[:2],b[2:],layer=l))
    # Deliberately unusual encounter order proves no numeric layer auto-sort.
    rect(127,(-1800,-1400,1800,1400))
    for y,width in [(0,32),(650,32),(-650,12)]:
        rect(128,(-1100,y-300,-500,y+300));rect(2,(-810,y-10,-790,y+10))
        cell.add(gdstk.FlexPath([(-800,y),(0,y)],20,layer=3,simple_path=True))
        rect(4,(-10,y-10,10,y+10))
        shapes=[gdstk.rectangle((-40,y-40),(40,y+40)),gdstk.rectangle((0,y-width/2),(800,y+width/2)),gdstk.rectangle((760,y-40),(840,y+40))]
        cell.add(*gdstk.boolean(shapes,[],'or',precision=.001,layer=5))
        rect(6,(790,y-10,810,y+10))
    rect(5,(-80,150,1000,180));rect(5,(-80,695,1000,725))
    roles={'127/0':'封装边框','128/0':'芯片边框','2/0':'运动过孔','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL','6/0':'固定过孔'}
    for i,(key,role) in enumerate(roles.items()):cell.add(gdstk.Label(role,(0,i*10),layer=int(key.split('/')[0])))
    name='示例E_连续型变与约束';path=ROOT/'examples'/(name+'.gds');lib.write_gds(path)
    p=Parameters(annulus_um=5,line_width_um=20,line_space_um=20,multi_bound_x_um=35,multi_bound_y_um=35,bound_angle_deg=1)
    q=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=cell.name,roles=roles,
           layer_order=['6/0','5/0','127/0','4/0','3/0','128/0','2/0'],parameters=asdict(p),settings=asdict(ViewSettings()),synthetic=True,
           description='三颗600×600μm芯片外扇出。DIE按中心排序：下方原线宽12<20；中间可平滑型变到固定连接端；上方邻网限制。固定层位于5/0；边框有意插在金属行之间验证保留用户层序。')
    (ROOT/'examples'/(name+'.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    q['gds_path']='../examples/'+path.name;(ROOT/'Jobs'/(name+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
    return path
if __name__=='__main__':print(create())
