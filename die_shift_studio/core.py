"""Read-only GDS geometry and conservative reconstruction-scheme screening.

Length unit: um. Recommendations are engineering screening, not routed/DRC
signoff. Geometric networks follow same-plane and adjacent-plane connections.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields, field, replace
from pathlib import Path
from typing import Callable

import gdstk
import numpy as np
from scipy.stats import chi2, norm
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from . import __version__
from .resources import parallel_map,resource_profile
from .statistics import statistical_state

from .layer_roles import ROLES,canonical_roles

SCHEME_NAMES = {1: "动静区局部重构", 2: "整体随动＋固定RDL捕获", 3: "运动RDL预制＋接口适配"}
PARAMETER_DESCRIPTIONS = {
    'panel_layers':'固定RDL可用层数 / 层', 'wafer_layers':'运动RDL可用层数 / 层',
    'line_width_um':'固定线宽 / μm', 'line_space_um':'固定线距 / μm',
    'wafer_line_width_um':'运动线宽 / μm', 'wafer_line_space_um':'运动线距 / μm',
    'via_diameter_um':'连接孔直径 / μm', 'annulus_um':'最小包围量 / μm',
    'residual_budget_um':'套刻和形变确定性预留 / μm', 'transition_length_um':'动态过渡长度 / μm',
    'max_route_angle_deg':'通道允许斜角 / °', 'routing_edge_keepout_um':'边端禁布长度 / μm',
    'min_die_gap_um':'要求芯片间净距 / μm', 'min_package_clearance_um':'要求芯片到封装边净距 / μm',
    'single_bound_x_um':'单芯片X单边幅度 / μm', 'single_bound_y_um':'单芯片Y单边幅度 / μm',
    'multi_bound_x_um':'多芯片每颗X单边幅度 / μm', 'multi_bound_y_um':'多芯片每颗Y单边幅度 / μm',
    'bound_angle_deg':'角度单边幅度 / °', 'input_nsigma':'输入幅度对应的σ倍率 / 无量纲',
    'reference_nsigma':'最近有限非零尺度级别；0级沿用此值 / 无量纲',
    'rotation_center':'旋转中心选项', 'rotation_x_um':'自定义中心相对芯片中心X / μm', 'rotation_y_um':'自定义中心相对芯片中心Y / μm',
    'target_nsigma':'目标0～12；0为全部有界概率，∞为二值模式 / 无量纲', 'mean_x_um':'平均X偏移 / μm',
    'mean_y_um':'平均Y偏移 / μm', 'mean_angle_deg':'平均转角 / °', 'rho_xy':'X/Y相关系数 / 无量纲',
    'interdie':'跨芯片连接：unknown待确认、none无、present有',
    'fixed_via_assignment':'固定过孔归属：all所有点保守包络、nearest最近芯片假设',
}


@dataclass
class Parameters:
    panel_layers: int = 2
    wafer_layers: int = 1
    line_width_um: float = 5.0
    line_space_um: float = 5.0
    wafer_line_width_um: float = 3.0
    wafer_line_space_um: float = 3.0
    via_diameter_um: float = 20.0
    annulus_um: float = 10.0
    residual_budget_um: float = 5.0
    transition_length_um: float = 200.0
    max_route_angle_deg: float = 30.0
    routing_edge_keepout_um: float = 40.0
    min_die_gap_um: float = 20.0
    min_package_clearance_um: float = 20.0
    single_bound_x_um: float = 25.0
    single_bound_y_um: float = 25.0
    multi_bound_x_um: float = 35.0
    multi_bound_y_um: float = 35.0
    bound_angle_deg: float = 1.0
    per_die_enabled: bool = False
    die_bounds: list = field(default_factory=list)  # rows: [Bx μm, By μm, Bθ degrees], DIE order
    rotation_center: str = "center"
    rotation_x_um: float = 0.0
    rotation_y_um: float = 0.0
    die_pivots: list = field(default_factory=list)  # [mode, relative X, relative Y]
    input_nsigma: float = 6.0
    target_nsigma: float = 6.0
    reference_nsigma: float = 6.0  # last finite nonzero level, persists for all-support mode
    mean_x_um: float = 0.0
    mean_y_um: float = 0.0
    mean_angle_deg: float = 0.0
    rho_xy: float = 0.0
    interdie: str = "unknown"  # unknown, none, present
    fixed_via_assignment: str = "all"  # all: conservative envelope; nearest: explicit user assumption

    def __post_init__(self):
        # Old projects may contain an obsolete independent input multiplier.
        from copy import deepcopy
        self.die_bounds=deepcopy(self.die_bounds);self.die_pivots=deepcopy(self.die_pivots)
        self.input_nsigma=self.target_nsigma
        if 0<self.target_nsigma<math.inf:self.reference_nsigma=self.target_nsigma

    @classmethod
    def from_dict(cls, data, require_rdl=True):
        data=dict(data)
        for key in ('input_nsigma','target_nsigma'):
            if data.get(key) in ('Infinity','∞','无穷大'):data[key]=math.inf
        data={k:v for k,v in data.items() if k not in {f'{prefix}_s{i}' for prefix in ('cost','yield') for i in (1,2,3)}}
        unknown = set(data)-{f.name for f in fields(cls)}
        if unknown:
            raise ValueError("未知参数："+", ".join(sorted(unknown)))
        value = cls(**data)
        value.validate(require_rdl=require_rdl)
        return value

    def for_die(self,owner):
        """Resolve one immutable per-DIE snapshot; all model consumers use this."""
        if not self.per_die_enabled:return self
        if not 0 <= owner < len(self.die_bounds):raise ValueError(f"DIE {owner+1} 缺少单独幅度参数，请检查芯片识别与幅度表")
        x,y,a=self.die_bounds[owner]
        mode,px,py=self.die_pivots[owner] if owner<len(self.die_pivots) else (self.rotation_center,self.rotation_x_um,self.rotation_y_um)
        return replace(self,per_die_enabled=False,single_bound_x_um=x,single_bound_y_um=y,
                       multi_bound_x_um=x,multi_bound_y_um=y,bound_angle_deg=a,rotation_center=mode,rotation_x_um=px,rotation_y_um=py)

    @property
    def scale_nsigma(self):
        return self.reference_nsigma if self.target_nsigma==0 else self.target_nsigma

    @property
    def failure_threshold(self):
        return 0. if self.target_nsigma==0 else math.erfc(self.target_nsigma/math.sqrt(2))

    def validate(self,require_rdl=True):
        if not isinstance(self.per_die_enabled,bool):raise ValueError("逐芯片开关必须为布尔值")
        if not isinstance(self.die_bounds,list):raise ValueError("逐芯片幅度必须为表格")
        for i,row in enumerate(self.die_bounds):
            if not isinstance(row,(list,tuple)) or len(row)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in row) or row[2]>180:
                raise ValueError(f"DIE {i+1} 幅度须为非负有限数，角度不超过180°")
        from .pivots import PIVOT_CHOICES
        allowed={v for _,v in PIVOT_CHOICES}
        if self.rotation_center not in allowed:raise ValueError("旋转中心选项无效")
        if not isinstance(self.die_pivots,list):raise ValueError("逐芯片旋转中心必须为表格")
        for row in self.die_pivots:
            if not isinstance(row,(list,tuple)) or len(row)!=3 or row[0] not in allowed or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in row[1:]):raise ValueError("逐芯片旋转中心须包含有效选项和有限相对坐标")
        for f in fields(self):
            x = getattr(self, f.name)
            if f.name in ("per_die_enabled","die_bounds","die_pivots"):continue
            if f.type == "str":
                continue
            if f.name in ('input_nsigma','target_nsigma') and x==math.inf:continue
            if isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x):
                raise ValueError(f"参数 {f.name} 必须为有限数值")
        for key in ("panel_layers", "wafer_layers"):
            value = getattr(self, key)
            if value != int(value) or not 0 <= value <= 32:
                raise ValueError("RDL层数须为0～32的整数")
        if require_rdl and self.panel_layers+self.wafer_layers < 1:
            raise ValueError("至少需要1层运动或固定RDL")
        positive = ("line_width_um", "line_space_um", "wafer_line_width_um", "wafer_line_space_um",
                    "via_diameter_um", "transition_length_um")
        for key in positive:
            if getattr(self,key) <= 0:
                raise ValueError(f"{key} 必须大于0")
        for key in ("annulus_um", "residual_budget_um", "routing_edge_keepout_um", "min_die_gap_um",
                    "min_package_clearance_um", "single_bound_x_um", "single_bound_y_um",
                    "multi_bound_x_um", "multi_bound_y_um", "bound_angle_deg"):
            if getattr(self,key) < 0:
                raise ValueError(f"{key} 不得为负")
        if not (self.input_nsigma in range(0,13) or self.input_nsigma==math.inf) or not (self.target_nsigma in range(0,13) or self.target_nsigma==math.inf):
            raise ValueError("目标覆盖须为0～12的整数或∞")
        if self.reference_nsigma not in range(1,13):raise ValueError("保留的概率尺度级别须为1～12整数")
        if not 0 < self.max_route_angle_deg < 80 or abs(self.rho_xy) > 1:
            raise ValueError("布线斜角须在0～80°之间；相关系数须在[-1,1]")
        if self.interdie not in {"unknown","none","present"} or self.fixed_via_assignment not in {"all","nearest"}:
            raise ValueError("网络或固定过孔归属选项无效")


@dataclass
class GDSData:
    path: str
    sha256: str
    top_name: str
    top_names: list
    dbu_um: float
    layers: dict  # layer/datatype -> list of um arrays
    auto_roles: dict = field(default_factory=dict)
    recognition_notes: list = field(default_factory=list)

    @property
    def count(self):
        return sum(map(len,self.layers.values()))


def _expanded_count(cell, stack=(), cache=None):
    cache = {} if cache is None else cache
    if cell.name in stack:
        raise ValueError("GDS存在循环引用")
    if cell.name in cache:
        return cache[cell.name]
    count = sum(max(1,p.repetition.size) for p in cell.polygons)
    count += sum(max(1,p.repetition.size)*p.num_paths for p in cell.paths)
    for ref in cell.references:
        if not isinstance(ref.cell,gdstk.Cell):
            raise ValueError("GDS存在未解析的引用："+str(ref.cell))
        count += max(1,ref.repetition.size)*_expanded_count(ref.cell,stack+(cell.name,),cache)
        if count > 500_000:
            raise ValueError("展开超过500000个图形；请先裁出待评估封装，未截断几何")
    cache[cell.name]=count
    return count


def read_gds(path, top_name=None, progress: Callable=lambda *_: None):
    path=Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("GDS文件不存在："+str(path))
    progress(5,"读取GDS并换算为μm")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    _,precision=gdstk.gds_units(path)
    lib=gdstk.read_gds(path,unit=1e-6)
    tops=lib.top_level()
    names=sorted(c.name for c in tops)
    if not names:
        raise ValueError("没有有效顶层Cell")
    if top_name is None and len(names)>1:
        return {"top_names":names}
    name=top_name or names[0]
    if name not in names:
        raise ValueError("选定Cell不是该GDS的顶层："+name)
    cell=next(c for c in tops if c.name==name)
    _expanded_count(cell)
    progress(30,"展开层次、阵列、旋转和镜像")
    polygons=cell.get_polygons(apply_repetitions=True,include_paths=True,depth=None)
    if len(polygons)>500_000:
        raise ValueError("展开图形数量超限；请裁出单个封装")
    layers={}
    for p in polygons:
        points=np.asarray(p.points,dtype=float)
        layers.setdefault(f"{p.layer}/{p.datatype}",[]).append(points)
    progress(80,"并行识别图层与几何")
    data=GDSData(str(path),digest,name,names,precision/1e-6,layers)
    from .geometry_errors import sanitize_data
    sanitize_data(data)
    from .recognition import recognize,TAGS
    tags={};ambiguous=set()
    for label in cell.get_labels(apply_repetitions=True,depth=None):
        role=TAGS.get(label.text.strip().upper()) or TAGS.get(label.text.strip())
        keys=[key for key in layers if key.split('/')[0]==str(label.layer)]
        if role and len(keys)==1:
            key=keys[0]
            if key in tags and tags[key]!=role:ambiguous.add(key)
            tags[key]=role
    for key in ambiguous:tags.pop(key,None)
    data.semantic_tags=tags
    data.auto_roles,data.recognition_notes=recognize(data,tags)
    progress(100,"已读取全部几何并完成自动识别")
    return data


def rectangle(cx,cy,width,height,angle=0.):
    if min(width,height)<=0 or not all(math.isfinite(float(v)) for v in (cx,cy,width,height,angle)):
        raise ValueError("矩形尺寸须为正，坐标必须有限")
    a=np.array([[-width,-height],[width,-height],[width,height],[-width,height]])/2
    t=math.radians(angle); rot=np.array([[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]])
    return Polygon(a@rot.T+[cx,cy])


def rectangle_info(poly):
    if not poly.is_valid or poly.area<=0 or poly.interiors:
        raise ValueError("芯片边框必须为无孔的有效矩形")
    rect=poly.minimum_rotated_rectangle
    if poly.symmetric_difference(rect).area>max(1e-5,poly.area*1e-7):
        raise ValueError("芯片层含非矩形；请选真实芯片边框层")
    a=np.array(rect.exterior.coords)[:4]
    edges=np.roll(a,-1,axis=0)-a
    lengths=np.linalg.norm(edges,axis=1)
    j=int(np.argmax(lengths)); e=edges[j]
    angle=math.degrees(math.atan2(e[1],e[0]))%180
    return dict(cx=rect.centroid.x,cy=rect.centroid.y,width=float(lengths[j]),
                height=float(lengths[(j+1)%4]),angle=angle)


def _polygons(data,roles,role):
    out=[]
    for key,values in data.layers.items():
        if roles.get(key)==role:
            for value in values:
                poly=Polygon(value)
                out.append(poly)
    return out


def build_case(data,roles,geometry=None,layer_order=None,connections=None):
    from .geometry_errors import sanitize_data
    sanitize_data(data);roles=canonical_roles(roles)
    if any(k not in data.layers or v not in ROLES for k,v in roles.items()):
        raise ValueError("图层映射与当前GDS不一致")
    if geometry:
        raise ValueError("本版本仅使用GDS边框，不支持手动几何覆盖；请在GDS中提供边框层")
    boards=_polygons(data,roles,"封装边框")
    if len(boards)!=1:
        raise ValueError("封装边框层须恰有一个有效多边形")
    board=boards[0]
    dies=_polygons(data,roles,"芯片边框")
    if not dies:
        raise ValueError("请指定芯片边框层")
    if len(dies)>256:
        raise ValueError("每个评估案例最多256颗芯片，请裁出单封装")
    dies=sorted(dies,key=lambda p:(p.centroid.x,p.centroid.y))
    infos=[rectangle_info(d) for d in dies]
    for i,d in enumerate(dies):
        if not board.buffer(max(data.dbu_um,1e-7)).covers(d):
            raise ValueError(f"DIE {i+1}不在封装边框内")
        for other in dies[:i]:
            if d.intersection(other).area>max(data.dbu_um**2,1e-8):
                raise ValueError("芯片边框存在面积重叠")
    from .stack import Connections
    con=connections or Connections(data,roles,layer_order)
    case=dict(board=board,dies=dies,infos=infos,obstacles=_polygons(data,roles,"禁布区"))
    owners=con.resolve_ownership(case);pins=[[] for _ in dies];outputs=[[] for _ in dies];capture=[[] for _ in dies]
    all_pins=[[] for _ in dies]
    for key in con.order:
        if roles.get(key)!='运动过孔':continue
        for index,poly in enumerate(con.geometry[key]):
            owner=int(owners[key][index])
            if owner<0:continue
            all_pins[owner].append(poly)
            targets=con.fixed_interfaces(key,index)
            if not targets:continue
            pins[owner].append(poly);outputs[owner].append(poly)
            seen=set()
            for target,j in targets:
                component=con.component(target,j)
                if component in seen:continue
                seen.add(component)
                capture[owner].append(dict(via=poly,receiver=con.component_geometry(target,j),layer=target))
    case.update(pins=pins,outputs=outputs,fixed_via=_polygons(data,roles,'固定过孔'),
                capture=capture,all_pins=all_pins,roles=roles,connections=con,ownership=owners)
    return case



def uncertainty_bound(points,center,params,die_count,details=False,state=None):
    """Simultaneous 3D input ellipsoid + exact Taylor remainder, in um."""
    points=np.asarray(points,float).reshape(-1,2)
    if not len(points):
        return np.array([])
    p=params
    mu,covariance,k=statistical_state(p,die_count) if state is None else state
    st=math.sqrt(covariance[2,2])
    a=points-np.asarray(center)
    t=math.radians(p.mean_angle_deg)
    rot=np.array([[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]])
    b=a@rot.T
    jac=np.zeros((len(a),2,3));jac[:,0,0]=1;jac[:,1,1]=1
    jac[:,0,2]=-b[:,1];jac[:,1,2]=b[:,0]
    cov=jac@covariance@np.swapaxes(jac,1,2)
    eig=np.maximum(0,np.linalg.eigvalsh(cov)[:,-1])
    mean=np.linalg.norm(b-a+[p.mean_x_um,p.mean_y_um],axis=1)
    if math.isinf(k):
        linear=np.full(len(a),math.inf if np.any(np.diag(covariance)[:2]>0) else 0.)
        remainder=2*np.linalg.norm(a,axis=1) if st>0 else np.zeros(len(a))
    else:
        linear=k*np.sqrt(eig);remainder=np.linalg.norm(a,axis=1)*(k*st)**2/2
    from .statistics import mean_and_bounds
    _,hard=mean_and_bounds(p,die_count)
    hard_limit=mean+np.linalg.norm(hard[:2])+2*np.linalg.norm(a,axis=1)*math.sin(min(math.pi,hard[2])/2)
    q=hard_limit if p.target_nsigma==0 else np.minimum(mean+linear+remainder,hard_limit) if math.isfinite(p.target_nsigma) else np.zeros(len(a))
    if details:
        return [dict(ax=float(a[j,0]),ay=float(a[j,1]),radius=float(np.linalg.norm(a[j])),
                     bx=float(b[j,0]),by=float(b[j,1]),lambda_max=float(eig[j]),
                     mean=float(mean[j]),linear=float(linear[j]),remainder=float(remainder[j]),q=float(q[j]))
                for j in range(len(a))]
    return q


def _margin(poly,p):
    """Legacy inscribed-pad helper; retained for API/tests, not the V15 score."""
    center=poly.centroid
    radius=center.distance(poly.boundary) if poly.covers(center) else 0.
    return radius-p.via_diameter_um/2-p.annulus_um-p.residual_budget_um


def spacing_violation(polys,space):
    if len(polys)<2:return False
    tree=STRtree(polys)
    for i,poly in enumerate(polys):
        for j in tree.query(poly.buffer(space)):
            if j<i and poly.distance(polys[j])<space-1e-7:return True
    return False


def evaluate(data,roles,params,geometry=None,progress=lambda *_:None,layer_order=None,connections=None,contact_model=None,motions=None,settings=None,safe_range_cache=None):
    params.validate(); p=replace(params,residual_budget_um=0.,routing_edge_keepout_um=0.);roles=canonical_roles(roles,motions);original_case=build_case(data,roles,geometry,layer_order,connections)
    from .stack import Connections
    progress(3,'识别相邻物理层的名义重叠连接')
    connections=original_case['connections']
    case=connections.filter_case(original_case)
    from .contact import ContactModel
    contact_model=contact_model or ContactModel(data,original_case,roles,connections,motions=motions,parameters=p)
    p=replace(p,interdie='present' if contact_model.interdie_networks else 'none')
    count=len(case['dies']); alpha=p.failure_threshold
    rows=[]; common=[]; qcorners=[]
    def compute_die(item):
        i,(die,info,pins,outputs)=item;local_notes=[];p=params.for_die(i)
        p=replace(p,residual_budget_um=0.,routing_edge_keepout_um=0.)
        from .pivots import rotation_center
        center=rotation_center(case,i,p)
        corners=np.asarray(die.exterior.coords)[:-1]
        qcorner=float(max(uncertainty_bound(corners,center,p,count)))
        boundary_gap=die.distance(case['board'].boundary)-p.min_package_clearance_um
        boundary_ratio=qcorner/boundary_gap if boundary_gap>0 else math.inf
        if boundary_ratio>1:
            local_notes.append(f"DIE {i+1}到封装边界的保守偏移包络不足（比值{boundary_ratio:.3g}）")
        if not 500<=min(info['width'],info['height']) or max(info['width'],info['height'])>10000 or info['width']/info['height']>3.000001:
            local_notes.append(f"DIE {i+1}尺寸超出常规500～10000 μm或长宽比1～3；按实际尺寸继续计算")
        row=dict(die=i+1,input_bounds=[p.single_bound_x_um if count==1 else p.multi_bound_x_um,p.single_bound_y_um if count==1 else p.multi_bound_y_um,p.bound_angle_deg],**info,pin_count=len(pins),output_count=len(outputs),
            all_pin_count=len(original_case['all_pins'][i]),all_output_count=len(original_case['outputs'][i]),
            rotation_origin_um=list(map(float,center)),rotation_origin_mode=p.rotation_center,
            corner_radius_um=float(np.linalg.norm(corners-np.asarray(center),axis=1).max()),
            corner_bound_um=qcorner,package_clearance_ratio=boundary_ratio,
            package_nominal_gap_um=float(die.distance(case['board'].boundary)))
        return row,local_notes,qcorner
    profile=resource_profile()
    work=enumerate(zip(case['dies'],case['infos'],case['pins'],case['outputs']))
    for i,(row,notes,corner) in enumerate(parallel_map(compute_die,work,profile)):
        rows.append(row);common.extend(notes);qcorners.append(corner)
        progress(int(70*(i+1)/count),f"评估 DIE {i+1}/{count}（最多{profile['workers']}线程）")
    collision_ratio=0.
    for i,d in enumerate(case['dies']):
        for j in range(i):
            margin=d.distance(case['dies'][j])-p.min_die_gap_um
            collision_ratio=max(collision_ratio,(qcorners[i]+qcorners[j])/margin if margin>0 else math.inf)
    if collision_ratio>1:
        common.append("芯片间隙不足以容纳当前保守运动包络")
    if case['obstacles']:
        common.append("存在禁布区：容量估计未替代障碍感知自动布线，需进行详细验证")
    from .screening import scheme_metrics
    metrics=scheme_metrics(contact_model,p)
    for row,m in zip(rows,metrics['rows']):row['screening']=m
    schemes=[]
    mechanical=max([collision_ratio]+[r['package_clearance_ratio'] for r in rows])
    for number in (1,2,3):
        u=max((r[f'u{number}'] for r in metrics['rows']),default=math.inf)
        reasons={1:['芯片边界：所需布线带宽 / 可用边长'],
                 2:['固定过孔端：整段随动所需位移 / 孔盘可扩展净空间'],
                 3:['扇出边界带宽与运动RDL原始逃逸带宽两者取大']}[number]
        status='初筛候选' if u<=1 else '空间条件不足'
        if number==2 and contact_model.interdie_networks:status='结构不适用';reasons.append('跨DIE网络不能同时作多个独立刚体')
        if number==3 and not metrics['moving_layers']:status='结构不适用';reasons.append('缺少运动RDL层')
        if number==2 and not all(r['captures'] for r in metrics['rows']):status='资料待补充';reasons.append('缺少可沿连接追溯的固定过孔捕获端')
        if any(r['pin_count']==0 for r in rows) or connections.ownership_notes:status='资料待补充';reasons.append('接口或运动归属不完整')
        if mechanical>1 and status=='初筛候选':status='机械空间待处理';reasons.append('芯片/封装保守净距包络不足')
        if number==3:reasons.append('运动RDL需确认可预制；分类本身不证明制造阶段')
        schemes.append(dict(scheme=number,name=SCHEME_NAMES[number],status=status,gamma=u,engineering_gamma=u,
                            reasons=reasons,mode='连续型变' if number!=2 else '扩展固定端孔盘',scope='几何空间初筛；不构成布线、DRC或良率签核'))
    contact=contact_model.screen(p,settings=settings,progress=progress)
    for s in schemes:
        s['contact_gamma']=contact['ratios'][s['scheme']]
        s['contact_status']='存在接触超限' if contact['issues'] else '概率待细化' if contact.get('unresolved') else '原接口检查通过'
        s['reasons'].append(s['contact_status']+'；独立报告，不并入空间比值U')
    candidates=[s for s in schemes if s['status']=='初筛候选']
    if candidates:
        minimum=min(s['gamma'] for s in candidates)
        best=min((s for s in candidates if s['gamma']<=minimum+1e-9),key=lambda s:{2:0,1:1,3:2}[s['scheme']])
        tied=[s['scheme'] for s in candidates if s['gamma']<=minimum+1e-9]
        tie_note=('方案'+ '、'.join(map(str,tied))+'同分，按工序取舍。') if len(tied)>1 else ''
        recommendation=dict(scheme=best['scheme'],name=best['name'],
                            reason=f"空间比值U={fmt(best['gamma'])}最小，保守余量较大；{tie_note}U≤1仅为初筛，仍须型变/全层DRC验证。",provisional=True)
    else:recommendation=dict(scheme=None,name="当前空间条件无初筛候选",reason="检查各方案U、接口归属和机械净距；原图概率结果单独列示。",provisional=True)
    from .safe_ranges import safe_ranges
    progress(96,'求取各DIE连续无异常接触的安全内盒')
    if safe_range_cache is None:ranges=safe_ranges(contact_model,p,settings,progress)
    else:
        from .session_cache import fingerprint
        from .settings import ViewSettings
        cfg=settings or ViewSettings();geometric_parameters=asdict(p)
        for key in ('input_nsigma','target_nsigma','reference_nsigma','rho_xy'):geometric_parameters.pop(key,None)
        range_key=('continuous-safe-box',fingerprint((data.sha256,data.top_name,roles,connections.order,geometric_parameters,
                    cfg.safe_box_precision,cfg.safe_box_budget,cfg.safe_box_starts,cfg.safe_box_passes)))
        ranges=safe_range_cache.get_or_compute(range_key,lambda:safe_ranges(contact_model,p,settings,progress))
    from .model_yield import model_yield
    from .settings import ViewSettings
    cfg=settings or ViewSettings()
    progress(97,'计算整个输入范围内的无接触概率')
    compute_yield=lambda:model_yield(contact_model,p,cfg,progress)
    if safe_range_cache is None:whole_yield=compute_yield()
    else:
        key=('whole-input-yield',fingerprint((data.sha256,data.top_name,roles,connections.order,asdict(p),cfg.yield_method,cfg.yield_sample_exponent,cfg.random_seed,cfg.yield_box_budget,cfg.yield_tolerance)))
        whole_yield=safe_range_cache.get_or_compute(key,compute_yield)
    progress(99,"整理窗口不足位置、安全范围与无接触概率")
    issues=contact['issues']
    progress(100,"评估完成")
    return dict(version=__version__,units="μm",source_path=data.path,source_sha256=data.sha256,
        top_cell=data.top_name,dbu_um=data.dbu_um,parameters=asdict(p),roles=roles,
        die_count=count,target_failure=alpha,input_ellipsoid_radius=statistical_state(p,count)[2],
        distribution='binary' if math.isinf(p.target_nsigma) else 'normal',
        interdie_networks=contact_model.interdie_networks,
        connections=connections.summary(),contact_analysis=contact,screening=metrics,safe_ranges=ranges,model_yield=whole_yield,
        repaired_geometry=getattr(data,'repaired_geometry',[]),
        ignored_geometry=[{k:v for k,v in q.items() if k!='points'} for q in getattr(data,'ignored_geometry',[])],
        resources=profile,collision_ratio=collision_ratio,dies=rows,schemes=schemes,recommendation=recommendation,notes=common,issues=issues,
        assumptions=["图层仅分运动/固定；运动归属按向下连接追溯，不能据此确认晶圆段制造阶段",
         "有限目标k时输入幅度为±kσ；∞或具体预演仅按当前几何二值判断",
         "芯片间采用并集上界，不要求独立；同芯片所有接口共用一个状态",
         "GDS几何不等于网表：每个几何网络一条轨道、最近边分配属于初筛假设",
         "连续安全范围为原始固定RDL下经证明的较大体积内盒，不是有限极端点拼接或全局最优保证",
         "接触概率为每个接口与其他网络的联合接触概率；未把各接口概率直接相加当作整包概率",
         "源GDS只读；不会写入重构线路"],schema_version=3)


def clean_json(value):
    if isinstance(value,float) and not math.isfinite(value):return "Infinity" if value>0 else "-Infinity"
    if isinstance(value,dict):return {k:clean_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean_json(v) for v in value]
    return value


def fmt(value,digits=3):
    if value is None:return "待提供"
    if isinstance(value,(int,float)):
        if not math.isfinite(value):return "∞"
        return f"{value:.{digits}f}".rstrip('0').rstrip('.')
    return str(value).replace('|','/').replace('\n',' ')
