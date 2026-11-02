"""Project compatibility; geometry always comes from the referenced GDS."""
from copy import deepcopy
from .core import Parameters

# Optional schema-5 fields. Older GDS-backed jobs use the same view defaults
# as a newly opened drawing, independent of the previously open project.
OVERLAY_DEFAULTS = dict(drc=False, probability=False, space=False, deformation=False,
                        width=False, width_sufficient=False, repair=False, reconstruction=False)


def project_overlays(project):
    options = dict(OVERLAY_DEFAULTS)
    legacy = (project.get('settings') or {}).get('reconstruction_enabled',False)
    saved = project.get('overlays') or {}
    if not isinstance(saved,dict):
        raise ValueError('工程图形选项必须为对象')
    for key in options:
        value = saved.get(key,legacy if key=='reconstruction' else options[key])
        if not isinstance(value,bool):
            raise ValueError(f'工程图形选项 {key} 必须为布尔值')
        options[key] = value
    return options


def normalize_project(project):
    p=deepcopy(project)
    if p.get('schema_version') not in (1,2,3,4,5):
        raise ValueError('不支持此工程版本')
    if p.get('geometry'):
        raise ValueError('旧工程含手动边框。本版本仅支持GDS，请在源GDS补齐边框后重新导入')
    from .layer_roles import canonical_roles
    p['roles']=canonical_roles(p.get('roles',{}),p.get('motions'))
    pars=p.get('parameters',{})
    if 'bga_assignment' in pars:
        pars['fixed_via_assignment']=pars.pop('bga_assignment')
    Parameters.from_dict(pars,require_rdl=False)
    p.setdefault('stage_hints',{k:r for k,r in p['roles'].items() if r in ('运动过孔','晶圆段输出接口')} if p.get('schema_version',1)<3 else {})
    from .settings import ViewSettings
    from dataclasses import asdict
    edits=p.setdefault('node_edits',[])
    if not isinstance(edits,list) or any(not isinstance(q,dict) or not {'layer','index','original','nodes'}<=q.keys() for q in edits):
        raise ValueError('工程节点编辑必须为含图层、图形编号、源指纹与坐标的列表')
    p['overlays']=project_overlays(p)
    p['settings']=asdict(ViewSettings.from_dict(p.get('settings')))
    p['schema_version']=5;p.pop('geometry',None)
    return p
