"""Project compatibility; geometry always comes from the referenced GDS."""
from copy import deepcopy
from .core import Parameters

# Optional schema-5 fields. Older GDS-backed jobs use the same view defaults
# as a newly opened drawing, independent of the previously open project.
EXTREME_PAGE_SIZE=256

OVERLAY_DEFAULTS = dict(drc=False, space=False,
                        width=False, width_sufficient=False, repair=False, reconstruction=False, footprint=False)


def restored_width_ignores(project,data,name='ignored_width_lines'):
    saved=project.get(name) or {}
    if (saved.get('sha256'),saved.get('top_cell'))!=(data.sha256,data.top_name):
        return frozenset()
    return frozenset((key,i) for key,i in saved['refs'] if key in data.layers and 0<=i<len(data.layers[key]))


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
    options['footprint']=bool(saved.get('footprint',False) or saved.get('probability',False))
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
    from dataclasses import asdict
    p['parameters']=asdict(Parameters.from_dict(pars,require_rdl=False))
    p.setdefault('stage_hints',{k:r for k,r in p['roles'].items() if r in ('运动过孔','晶圆段输出接口')} if p.get('schema_version',1)<3 else {})
    if p.get('geometry_indexing','legacy') not in ('legacy','merged-v39'):raise ValueError('工程图形索引模式无效')
    legacy_edits=p.get('legacy_node_edits',[])
    if not isinstance(legacy_edits,list) or any(not isinstance(q,dict) or not {'layer','index','original','nodes'}<=q.keys() for q in legacy_edits):raise ValueError('旧源片节点编辑格式无效')
    from .settings import ViewSettings
    from dataclasses import asdict
    edits=p.setdefault('node_edits',[])
    if not isinstance(edits,list) or any(not isinstance(q,dict) or not {'layer','index','original','nodes'}<=q.keys() for q in edits):
        raise ValueError('工程节点编辑必须为含图层、图形编号、源指纹与坐标的列表')
    for ignore_name in ('ignored_width_lines','ignored_reconstruction'):
        saved=p.get(ignore_name)
        if saved is not None:
            if (not isinstance(saved,dict) or not all(isinstance(saved.get(k),str) for k in ('sha256','top_cell'))
                or not isinstance(saved.get('refs'),list) or any(not isinstance(r,(list,tuple)) or len(r)!=2
                or not isinstance(r[0],str) or type(r[1]) is not int or r[1]<0 for r in saved['refs'])):
                raise ValueError('忽略线路须绑定源 GDS、顶层 Cell 及有效的图层和图形编号')
    sections=p.setdefault('expanded_settings',[])
    if not isinstance(sections,list) or any(not isinstance(title,str) for title in sections):
        raise ValueError('工程设置栏目列表必须由标题字符串组成')
    titles={'全输入无接触模型良率':'无接触良率','全输入良率计算':'无接触良率','无接触良率计算':'无接触良率',
            '宽度分析 · 良率计算':'宽度分析','宽度分析 · 元素显示':'宽度分析','DXF输出参数':'DXF',
            '缓存管理':'硬件与计算资源','过孔实体覆盖概率':'极端足迹','视图与预演运动':'动画',
            '连续无异常接触范围':'安全范围','固定RDL重构预演':'重构预演','DRC检查':'DRC'}
    p['expanded_settings']=list(dict.fromkeys(titles.get(title,title) for title in sections if not title.startswith('预演分析')))
    from .dxf_settings import DxfSettings
    p['dxf_options']=asdict(DxfSettings.from_dict(p.get('dxf_options')))
    if not isinstance(p.setdefault('dxf_directory',''),str):raise ValueError('DXF输出目录必须为文本')
    view=p.get('extreme_view',{})
    if not isinstance(view,dict):raise ValueError('工程极端情况视图必须是对象')
    view={'mode':'focus','page':1,'page_size':64,**view}
    if view['mode'] not in ('all','focus') or any(type(view[k]) is not int or view[k]<1 for k in ('page','page_size')):
        raise ValueError('工程极端情况视图须包含有效目录类型、正整数页码及页大小')
    # Keep the old first pose on screen when the larger pages absorb a V26 page.
    filtered=view.get('filter_problems',True)
    if type(filtered) is not bool:raise ValueError('过滤无问题情况必须为布尔值')
    p['extreme_view']=dict(filter_problems=filtered,mode=view['mode'],page=1+(view['page']-1)*view['page_size']//EXTREME_PAGE_SIZE,page_size=EXTREME_PAGE_SIZE)
    p['overlays']=project_overlays(p)
    p['settings']=asdict(ViewSettings.from_dict(p.get('settings')))
    p['schema_version']=5;p.pop('geometry',None)
    return p
