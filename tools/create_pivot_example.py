"""Portable off-DIE fanout with independent pivots and full-support contours."""
import json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dataclasses import asdict
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings
name='示例I_旋转中心与全范围概率'
from die_shift_studio.project import normalize_project
q=normalize_project(json.loads((ROOT/'examples/示例D_芯片外扇出.json').read_text(encoding='utf-8')))
shutil.copyfile(ROOT/'examples'/q['gds_path'],ROOT/'examples'/(name+'.gds'))
p=Parameters.from_dict(q['parameters']);p=__import__('dataclasses').replace(p,per_die_enabled=True,die_bounds=[[35,25,1],[20,30,2]],die_pivots=[['top_left',0,0],['custom',150,-100]])
q.update(parameters=asdict(p),gds_path=name+'.gds',settings=asdict(ViewSettings(probability_show_ranges=True,probability_ranges=((1,'#386cb0'),(3,'#299985')))))
q['notes']='DIE1左上角旋转；DIE2自定义中心(+150,-100) μm。芯片外扇出按连通归属随动。当前6σ与1/3σ包络共用同一截断分布；切换0级保留6σ尺度。'
(ROOT/'examples'/(name+'.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
q['gds_path']='../examples/'+name+'.gds';(ROOT/'Jobs'/(name+'.dshift.json')).write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
