"""A portable single-XY PATH, zero-width warnings and an adjacent-pad coupon."""
from pathlib import Path
from dataclasses import asdict
import hashlib
import json
import struct
import sys
import gdstk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from die_shift_studio.core import Parameters


def patch_paths(path,single,zero,absolute=()):
    """Write real GDS test records that geometry authoring libraries normalise."""
    data=path.read_bytes();result=bytearray();layer=None;is_path=False
    offset=0
    while offset<len(data):
        size,rec,dtype=struct.unpack_from('>HBB',data,offset)
        body=data[offset+4:offset+size];offset+=size
        if rec==9:is_path=True;layer=None
        if rec==13 and is_path:layer=struct.unpack('>h',body)[0]
        if is_path and rec==15:
            if layer in zero:body=b'\0'*4
            elif layer in absolute:body=struct.pack('>i',-abs(struct.unpack('>i',body)[0]))
        if is_path and rec==16 and layer in single:body=body[:8]
        result.extend(struct.pack('>HBB',len(body)+4,rec,dtype)+body)
        if rec==17:is_path=False
    path.write_bytes(result)


def create(folder=ROOT/'examples', jobs=ROOT/'Jobs'):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    name='示例P_单点PATH与相邻孔盘'
    lib=gdstk.Library(unit=1e-6,precision=1e-9);top=lib.new_cell('PAD_NEIGHBOUR_TEST')
    top.add(gdstk.rectangle((-200,-180),(200,180),layer=127),gdstk.rectangle((-185,-155),(185,155),layer=128))
    # The primary horizontal nominal section is at y=0. The middle pad reaches
    # the assessment rectangle, while its circle can miss the actual section.
    for x,y in [(-85,0),(0,25),(85,0),(-44,100),(44,105)]:
        top.add(gdstk.FlexPath([(x,y),(x+1,y)],16,ends='round',simple_path=True,layer=4))
        top.add(gdstk.ellipse((x,y),18,layer=5))
        top.add(gdstk.rectangle((x-10,y-10),(x+10,y+10),layer=3))
    for x,y in [(-44,100),(44,105)]:
        top.add(gdstk.rectangle((x-8,-104),(x+8,y),layer=5))
    top.add(gdstk.rectangle((-6,-103),(6,25),layer=5))
    top.add(gdstk.rectangle((-195,-90),(-185,-55),layer=5))
    top.add(gdstk.FlexPath([(5,83),(52,83)],2,simple_path=True,layer=77))
    path=folder/(name+'.gds');lib.write_gds(path);patch_paths(path,{4},{77})
    roles={'127/0':'封装边框','128/0':'芯片边框','3/0':'运动RDL','4/0':'运动过孔','5/0':'固定RDL'}
    project=dict(schema_version=5,gds_path=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),top_cell=top.name,
                 roles=roles,layer_order=['5/0','4/0','3/0','128/0','127/0'],overlays=dict(width=True),
                 parameters=asdict(Parameters(annulus_um=10)),description='4/0 来自单个 XY、宽 16 μm 的圆孔。77/0 的零宽 PATH 被忽略。中孔部分进入评估矩形，必须保留完整孔盘。左边界实测 5 μm，小于 15 μm 线距。',synthetic=True)
    (folder/(name+'.json')).write_text(json.dumps(project,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if jobs is not None:
        project['gds_path']='../examples/'+path.name
        (Path(jobs)/(name+'.dshift.json')).write_text(json.dumps(project,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return path


if __name__=='__main__':create()
