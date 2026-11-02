"""Build the V23 three-route channel from the unchanged fixed-only example N."""
import json
from pathlib import Path
import hashlib
import sys
import gdstk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.project import normalize_project


def create():
    old='示例N_三线路窄通道宽度'
    name='示例O_运动网络三线路通道'
    lib=gdstk.read_gds(ROOT/'examples'/f'{old}.gds')
    top=lib.top_level()[0]
    top.name='THREE_MOVING_NET_ROUTES'
    for poly in list(top.polygons):
        (x0,y0),(x1,y1)=poly.bounding_box()
        if poly.layer==6 or (poly.layer==5 and y0==-500 and y1==500):
            top.remove(poly)
    for x,width,y,root_x,root_y in [(-30,20,350,-850,210),(2.5,15,-350,900,-220),(32.5,15,250,780,140)]:
        top.add(gdstk.rectangle((x-width/2,-500),(x+width/2,500),layer=5))
        for ey in (-500,500):
            top.add(gdstk.rectangle((x-5,ey-5),(x+5,ey+5),layer=6))
        top.add(gdstk.rectangle((x-5,y-5),(x+5,y+5),layer=4))
        top.add(gdstk.FlexPath([(x,y),(root_x,y),(root_x,root_y)],10,layer=3,simple_path=True))
        top.add(gdstk.rectangle((root_x-8,root_y-8),(root_x+8,root_y+8),layer=2))
    path=ROOT/'examples'/f'{name}.gds'
    lib.write_gds(path)
    project=normalize_project(json.loads((ROOT/'examples'/f'{old}.json').read_text(encoding='utf-8')))
    project.update(top_cell=top.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),gds_path=path.name,
                   description='三条固定 RDL 线路宽度为 20、15、15 μm，分别接入运动过孔。15/15/15 μm 规则下，中央双孔盘之间原可通 3 根、必须通过 3 根。DIE1 DX=+3 μm 时可收窄第一条线通过。DX=+35 μm 后现可通 2 根，DX=−35 μm 时现可通 4 根。')
    project['overlays']['width']=True
    def write(path):
        path.write_text(json.dumps(project,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write(ROOT/'examples'/f'{name}.json')
    project['gds_path']='../examples/'+path.name
    write(ROOT/'Jobs'/f'{name}.dshift.json')

if __name__=='__main__':
    create()
