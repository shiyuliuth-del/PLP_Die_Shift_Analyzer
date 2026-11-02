"""Reproduce a GDS project; print the recommendation or write numeric JSON."""
import argparse,json,sys
from pathlib import Path
from dataclasses import asdict
from .core import Parameters,read_gds,evaluate,clean_json
from .project import normalize_project
from .settings import ViewSettings
from .recognition import automatic_parameters
from .stack import stack_order,resolve_stages
parser=argparse.ArgumentParser(description='Die Shift Analyze：GDS工程评估')
parser.add_argument('--project',required=True);parser.add_argument('--json')
args=parser.parse_args();path=Path(args.project).resolve();project=normalize_project(json.loads(path.read_text(encoding='utf-8')))
source=Path(project['gds_path'])
if not source.is_absolute():source=path.parent/source
data=read_gds(source,project.get('top_cell'))
if isinstance(data,dict):parser.error('工程须选择顶层Cell')
if project.get('sha256') and data.sha256!=project['sha256']:parser.error('源GDS已改变，请重新核对工程')
from .gds_shapes import edited_snapshot
data=edited_snapshot(data,project.get('node_edits',[]))
for warning in data.import_warnings:
    print('警告 · '+warning,file=sys.stderr)
roles=project.get('roles',data.auto_roles)
order=stack_order(data,roles,project.get('layer_order'))
roles=resolve_stages(data,roles,order,project.get('stage_hints'))
params=Parameters.from_dict({**asdict(Parameters.from_dict(project['parameters'])),**automatic_parameters(data,roles)[0]})
result=evaluate(data,roles,params,layer_order=order,motions=project.get('motions'),settings=ViewSettings.from_dict(project.get('settings')),validate_extremes=True)
if args.json:Path(args.json).write_text(json.dumps(clean_json(result),ensure_ascii=False,indent=2),encoding='utf-8')
print('初筛建议：'+result['recommendation']['name'])
