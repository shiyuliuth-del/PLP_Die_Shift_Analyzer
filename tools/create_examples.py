"""Create explicitly synthetic GDS design candidates and projects (um)."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from dataclasses import asdict
import gdstk
from die_shift_studio.core import Parameters,read_gds,evaluate,clean_json

ROOT=Path(__file__).resolve().parents[1]
ROLES={'1/0':'封装边框','2/0':'芯片边框','10/0':'运动过孔','20/0':'固定过孔','30/0':'晶圆段输出接口','31/0':'晶圆段RDL','40/0':'板段RDL','41/0':'板段RDL'}

def create(name,width,height,centers,pad_diameter,interdie,transition=200,board=(14000,10000),outputs=False):
    lib=gdstk.Library(unit=1e-6,precision=1e-9);top=lib.new_cell('PACKAGE');die=lib.new_cell('DIE')
    top.add(gdstk.rectangle((-board[0]/2,-board[1]/2),(board[0]/2,board[1]/2),layer=1))
    die.add(gdstk.rectangle((-width/2,-height/2),(width/2,height/2),layer=2))
    # 32 raw pads; 4 x 8 regular fixed via terminals per die.
    for j in range(16):
        x=-width*.42+j*width*.84/15
        for sign in (-1,1):die.add(gdstk.rectangle((x-12,sign*height*.42-12),(x+12,sign*height*.42+12),layer=10))
    if outputs:
        # Explicit physical output-pad geometry; no hidden synthetic pin map.
        pitch=min(width/9,height/5)
        for j in range(8):
            for k in range(4):
                x=(j-3.5)*pitch;y=(k-1.5)*pitch
                die.add(gdstk.ellipse((x,y),min(pitch*.40,160),tolerance=.05,layer=30))
    # Continuous illustrative connections across every adjacent physical layer.
    # They demonstrate landing overlap, not netlist-complete DRC-clean routing.
    raw=[]
    for j in range(16):
        for sign in (-1,1):raw.append((-width*.42+j*width*.84/15,sign*height*.42))
    fixed=[((j-3.5)*min(width/7,700),(k-1.5)*min(height/3,700)) for j in range(8) for k in range(4)]
    out=[((j-3.5)*min(width/9,height/5),(k-1.5)*min(width/9,height/5)) for j in range(8) for k in range(4)] if outputs else raw
    for source,interface,target in zip(raw,out,fixed):
        if outputs:die.add(gdstk.FlexPath([source,interface],3,layer=31))
        die.add(gdstk.FlexPath([interface,target],5,layer=40))
        die.add(gdstk.FlexPath([interface,target],5,layer=41))
    for cx,cy in centers:
        top.add(gdstk.Reference(die,origin=(cx,cy)))
        for j in range(8):
            for k in range(4):
                x=cx+(j-3.5)*min(width/7,700);y=cy+(k-1.5)*min(height/3,700)
                top.add(gdstk.ellipse((x,y),pad_diameter/2,tolerance=.05,layer=20))
    from die_shift_studio.recognition import TAGS
    for key,role in ROLES.items():
        if key=='30/0' and not outputs:continue
        if key=='31/0' and not outputs:continue
        top.add(gdstk.Label(role,(0,0),layer=int(key.split('/')[0])))
    top.add(gdstk.rectangle((-board[0]/2+100,-board[1]/2+100),(-board[0]/2+180,-board[1]/2+160),layer=90))
    path=ROOT/'examples'/f'{name}.gds';lib.write_gds(path)
    data=read_gds(path);p=Parameters(interdie=interdie,fixed_via_assignment='all',transition_length_um=transition)
    if not outputs:p.wafer_layers=0
    roles={k:v for k,v in ROLES.items() if k in data.layers}
    order=[k for k in ['20/0','41/0','40/0','30/0','31/0','10/0','1/0','2/0','90/0'] if k in data.layers]
    project=dict(schema_version=5,layer_order=order,gds_path=path.name,sha256=data.sha256,top_cell='PACKAGE',roles=roles,parameters=asdict(p),synthetic=True)
    path.with_suffix('.json').write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
    job=dict(project,gds_path='../examples/'+path.name);(ROOT/'Jobs').mkdir(exist_ok=True)
    (ROOT/'Jobs'/(name+'.dshift.json')).write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
    from die_shift_studio.recognition import automatic_parameters
    p=Parameters.from_dict({**asdict(p),**automatic_parameters(data,roles)[0]})
    from die_shift_studio import __version__
    result=evaluate(data,roles,p,layer_order=order);(ROOT/'results'/f'{name}_v{__version__.replace(".","")}.json').write_text(json.dumps(clean_json(result),ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,result['recommendation']['scheme'],[(s['scheme'],s['status'],round(s['gamma'],3)) for s in result['schemes']])

if __name__=='__main__':
    create('示例A_小芯片大孔盘',2000,2000,[(0,0)],180,'none',board=(5000,5000))
    create('示例B_大芯片局部重构',8000,4000,[(0,0)],100,'none',transition=400)
    create('示例C_多芯片晶圆接口',4000,2500,[(-3000,0),(3000,0)],100,'present',transition=25,outputs=True)
