"""Frozen engineering session for local measured poses and batch output."""
from dataclasses import dataclass,asdict,replace
import copy,math,os,tempfile,threading
from pathlib import Path
import numpy as np
import shapely
from .core import Parameters
from .resources import cancel_check,resource_profile


@dataclass(frozen=True)
class Options:
    output_directory:str=''
    max_memory_mb:int=0
    max_threads:int=0
    @classmethod
    def from_dict(cls,data):
        data=data or {};out=cls(**{k:v for k,v in data.items() if k in cls.__dataclass_fields__})
        if not isinstance(out.output_directory,str):raise ValueError('输出目录必须是文本')
        if type(out.max_memory_mb) is not int or (out.max_memory_mb!=0 and not 128<=out.max_memory_mb<=1048576):raise ValueError('最大内存须为0（自动）或128～1048576 MB')
        if type(out.max_threads) is not int or not 0<=out.max_threads<=256:raise ValueError('最大线程须为0～256，0为自动')
        return out

    def memory_budget_mb(self,profile=None):
        profile=profile or resource_profile()
        available=max(128,int(profile['available_memory_mb']*.35))
        return min(self.max_memory_mb or available,available)


def pose_parameters(case,parameters,rows):
    n=len(case['dies'])
    if not isinstance(rows,list) or len(rows)!=n:raise ValueError(f'必须指定全部 {n} 颗DIE，每颗恰好一次')
    states=np.zeros((n,3));pivots=[];bounds=[];seen=set()
    for i in range(n):
        p=parameters.for_die(i);pivots.append([p.rotation_center,p.rotation_x_um,p.rotation_y_um])
        prefix='single' if n==1 else 'multi';bounds.append([getattr(p,prefix+'_bound_x_um'),getattr(p,prefix+'_bound_y_um'),p.bound_angle_deg])
    for row in rows:
        if not isinstance(row,dict) or type(row.get('die')) is not int or not 1<=row['die']<=n or row['die'] in seen:raise ValueError('DIE编号须为1起、不重复的整数')
        if set(row)-{'die','dx_um','dy_um','angle_deg','rotation_center_um'}:raise ValueError('偏移含未知字段')
        seen.add(row['die']);i=row['die']-1
        xyz=[row.get(k) for k in ('dx_um','dy_um','angle_deg')]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in xyz):raise ValueError('DX/DY/角度必须为有限数值')
        if max(abs(xyz[0]),abs(xyz[1]))>1e9 or abs(xyz[2])>360:raise ValueError('偏移超出坐标范围或角度超过±360°')
        states[i]=xyz;states[i,2]=math.radians(xyz[2])
        if 'rotation_center_um' in row:
            xy=np.asarray(row['rotation_center_um'],float)
            if xy.shape!=(2,) or not np.isfinite(xy).all() or max(abs(xy))>1e9:raise ValueError('旋转中心须为有限绝对XY坐标，单位μm')
            center=np.asarray(case['dies'][i].centroid.coords[0]);pivots[i]=['custom',*(xy-center)]
    p=replace(parameters,per_die_enabled=True,die_bounds=bounds,die_pivots=pivots);p.validate()
    return states,p


class Session:
    def __init__(self,data,case,roles,motions,parameters,settings,cache,plan_pool=None):
        self.data=data;self.case=case;self.roles=dict(roles);self.motions=dict(motions);self.p=pose_parameters(case,parameters,[dict(die=i+1,dx_um=0.,dy_um=0.,angle_deg=0.) for i in range(len(case['dies']))])[1]
        from .pivots import rotation_centers
        centers=rotation_centers(case,self.p)
        self.p=replace(self.p,die_pivots=[['custom',*(c-np.asarray(d.centroid.coords[0]))] for c,d in zip(centers,case['dies'])])
        self.settings=copy.deepcopy(settings);self.cache=cache;self.plan_pool=plan_pool or {}
        from .session_cache import logical_fingerprint
        from . import __version__
        self.identity=logical_fingerprint((__version__,data.geometry_id,data.top_name,self.roles,list(cache.layers),self.motions,asdict(self.p),asdict(self.settings)))
        self.lock=threading.Lock();self.plan_lock=threading.Lock();self.local=threading.local();self.instances=[]
        from .session_cache import EphemeralCache
        self.geometry_pool=EphemeralCache(budget=64*1024**2,max_entries=4096)
    def model(self,p):
        from .contact import ContactModel
        from .reconstruction import Reconstructor
        from .reconstruction_status import clone_plan_pool
        key=repr(asdict(p));saved=getattr(self.local,'model',None)
        if saved is not None and saved[0]==key:return saved[1:]
        if saved is not None:
            saved[2].close()
            with self.lock:self.instances.remove(saved[2])
        model=ContactModel(self.data,self.case,self.roles,self.case['connections'],self.cache,self.motions,p)
        model.bound.acceleration=self.settings.acceleration
        with self.lock:plans=clone_plan_pool(self.plan_pool)
        r=Reconstructor(model,p,self.settings,plan_pool=plans,geometry_pool=self.geometry_pool);r.measured_only=True
        self.local.model=(key,model,r)
        with self.lock:self.instances.append(r)
        return model,r
    def prepare(self,progress):
        _,r=self.model(self.p);self.ensure_plans(r,progress)
    def ensure_plans(self,r,progress):
        # First batch request plans once; other lanes clone immutable nominal
        # routes instead of running the same expensive tracing concurrently.
        if r._plans is not None:return
        from .reconstruction_status import clone_plan_pool
        with self.plan_lock:
            with self.lock:r.plan_pool.update(clone_plan_pool(self.plan_pool))
            r._plans_for_all_poses(progress)
            with self.lock:self.plan_pool.update(clone_plan_pool(r.plan_pool))
    def run(self,rows,progress=lambda *_:None,*,preview=True,diagnostic=False,output_policy='strict'):
        if output_policy not in ('strict','omit_failed_regions'):raise ValueError('未知实测输出策略')
        states,p=pose_parameters(self.case,self.p,rows);model,r=self.model(p)
        self.ensure_plans(r,lambda n,t:progress(round(n*.2),t))
        # Measured commands check their own actual-copper DRC; no other poses,
        # probability, width analysis or yield work is started.
        result=r.frame(states,progress=lambda n,t:progress(20+round(n*.5),t),diagnostics=preview)
        bound=self.cache.bind(self.case,self.motions,list(self.cache.layers),p)
        bound.acceleration=self.settings.acceleration
        layers=bound.transform(states)
        frame=dict(states=states,layers=layers,rotation_centers=model.bound.centers.copy(),reconstruction=result,
                   checks={i:dict(failures=[],problem_percent=0) for i in (1,2,3)},contact={'issues':[],'regions':[]},motion_issues=[])
        # These per-pose rules are part of the measured command itself.
        # No catalogue of other poses, probability grid or yield is launched.
        from .drc import GlobalDRC
        from .current_drc import locations
        from .measured_issues import catalogue
        from .reconstruction_diagnostic import markers
        if not hasattr(r,'display_drc'):r.display_drc=GlobalDRC(model,p)
        frame['drc']=r.display_drc.frame(states,result['replacements'],self.settings,lambda n,t:progress(70+round(n*.15),t))
        frame['drc'].extend(markers(result,self.data.dbu_um));frame['measure_drc']=bool(preview);frame['drc_locations']=locations(frame['drc'],measure=preview)
        issues=catalogue(frame,self.case,model)
        original_failures=tuple(result.get('hard_failures',()));omissions=[]
        if original_failures and output_policy=='omit_failed_regions':
            from .partial_output import omit
            frame,omissions=omit(model,frame)
            frame['drc'].extend(r.display_drc.frame(states,frame['reconstruction']['replacements'],self.settings,lambda n,t:progress(85+round(n*.1),t)))
            for q in omissions:
                if q.get('quantization'):frame['drc'].append(dict(layer=q['layer'],layers=(q['layer'],),refs=((q['layer'],q['index']),),kind=q['reason'],geometry=shapely.box(*q['bounds_um']),connection_failure=True))
            frame['drc_locations']=locations(frame['drc'],measure=preview);issues=catalogue(frame,self.case,model)
        if result.get('hard_failures') and output_policy=='strict':
            from .reconstruction_diagnostic import display,DiagnosticFailure
            from .topology_routing import failure_message
            frame['reconstruction']=display(result,frame['rotation_centers'],states);frame['diagnostic_only']=True
            details=dict(connection_failure_count=len(result['hard_failures']),connection_failures=[dict(layer=ref[0],index=ref[1],reason=reason) for ref,reason in result['hard_failures']],fixed_rdl_drc_checked=True,diagnostic_only=True,
                         drc_issues=issues,drc_count=len(issues),drc_passed=False,coordinate_frame='GDS顶层坐标，单位μm；package_relative_bounds_um以名义封装左下角为原点')
            if diagnostic:return frame,None,details
            raise DiagnosticFailure(failure_message(result['hard_failures']),frame,details)
        failures=[dict(layer=q['layer'],index=q['index'],reason=q.get('warning','无法连接')) for q in result['pieces'] if q.get('connection_failed')]
        from .dxf_export import snapshot
        frozen=snapshot(self.data,self.cache,frame,reconstruction=True,progress=lambda n,t:progress(95+round(n*.04),t),allow_partial=bool(frame.get('partial_output_validated')))
        for blobs in frozen.layers.values():
            for b in blobs:
                g=shapely.from_wkb(b)
                if not g.is_valid or not np.isfinite(shapely.get_coordinates(g)).all():raise ValueError('重构结果无效，拒绝输出')
        # Keep the last interactive pose only; batch cannot fill the GUI LRU.
        if not preview:
            r.cache.clear()  # retain bounded, immutable per-source and nominal DRC caches
        return frame,frozen,dict(output_policy=output_policy,partial_output=bool(original_failures),omitted_regions=omissions,omitted_region_count=len(omissions),connection_failures=failures,connection_failure_count=max(len(failures),len(original_failures)),
                                fixed_rdl_drc_checked=True,drc_issues=issues,drc_count=len(issues),drc_passed=not issues,coordinate_frame='GDS顶层坐标，单位μm；package_relative_bounds_um以名义封装左下角为原点',rule_groups=len(result.get('violations',())),
                                scope='当前实测姿态的实际铜DRC及连接检查；不计算其他姿态、概率或良率，不代表工艺签核')
    def close(self):
        for r in self.instances:r.close()
        self.instances.clear();self.geometry_pool.clear();self.local=threading.local()


def write_gds(snapshot,destination,dbu):
    import gdstk
    from .gds_merge import winding_outline
    path=Path(destination).resolve()
    if path.suffix.lower() not in ('.gds','.gdsii'):raise ValueError('单姿态输出须使用.gds后缀')
    if not path.parent.is_dir():raise ValueError('输出目录不存在')
    lib=gdstk.Library(unit=1e-6,precision=dbu*1e-6);top=lib.new_cell('MeasuredPackage');discarded=0
    for key,blobs in snapshot.layers.items():
        layer,datatype=map(int,key.split('/'))
        if not (0<=layer<=65535 and 0<=datatype<=65535):raise ValueError('图层或Datatype超过GDS 16位范围，请使用OAS输出')
        for b in blobs:
            cancel_check()
            for g in shapely.get_parts(shapely.from_wkb(b)):
                if not g.is_empty:
                    points=winding_outline(g);scaled=points/dbu
                    quantized=np.sign(scaled)*np.floor(np.abs(scaled)+.5)
                    if np.any(quantized<-(2**31)) or np.any(quantized>2**31-1):raise ValueError('GDS坐标超过32位DBU范围，拒绝溢出输出；请减小坐标或使用OAS')
                    integer=quantized.astype(np.int64)
                    integer=integer[np.any(integer!=np.roll(integer,1,axis=0),axis=1)]
                    area2=sum(int(a[0])*int(b[1])-int(a[1])*int(b[0]) for a,b in zip(integer,np.roll(integer,-1,axis=0)))
                    if len(integer)<3 or area2==0:discarded+=1;continue
                    top.add(gdstk.Polygon(points,layer=layer,datatype=datatype))
    fd,temp=tempfile.mkstemp(prefix='.dsa-measured-',suffix='.gds',dir=path.parent);os.close(fd)
    try:
        lib.write_gds(temp,max_points=8190);check=gdstk.read_gds(temp)
        if not check.top_level() or not check.top_level()[0].get_polygons():raise ValueError('GDS回读无有效图形')
        cancel_check();os.replace(temp,path)
        return dict(path=str(path),bytes=path.stat().st_size,dbu_um=dbu,discarded_subgrid_fragments=discarded)
    finally:
        if os.path.exists(temp):os.unlink(temp)
