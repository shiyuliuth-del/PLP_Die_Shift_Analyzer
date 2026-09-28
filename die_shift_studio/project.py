"""Project compatibility; geometry always comes from the referenced GDS."""
from copy import deepcopy
from .core import Parameters

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
    Parameters.from_dict(pars)
    p.setdefault('stage_hints',{k:r for k,r in p['roles'].items() if r in ('运动过孔','晶圆段输出接口')} if p.get('schema_version',1)<3 else {})
    from .settings import ViewSettings
    from dataclasses import asdict
    p['settings']=asdict(ViewSettings.from_dict(p.get('settings')))
    p['schema_version']=5;p.pop('geometry',None)
    return p
