"""Frozen engineering session for local measured poses and batch output."""
from dataclasses import dataclass,asdict,replace
import copy,math,os,tempfile,threading
from pathlib import Path
import numpy as np
import shapely
from .core import Parameters
from .resources import cancel_check,resource_profile,progress_slice


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

    def memory_budget_mb(self,profile=None,*,measured=False):
        profile=profile or resource_profile()
        available=max(128,int(profile['available_memory_mb']*(.65 if measured else .35)))
        if self.max_memory_mb:
            ceiling=max(128,int(profile['available_memory_mb']*.85)) if measured else profile.get('memory_budget_mb',available)
            return min(self.max_memory_mb,ceiling)
        return available if measured else profile.get('memory_budget_mb',available)


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
        self._nominal_wkb=None;self._ref_owners=None;self._always_active_refs=None;self._zero_pose_cache={}
    def _static_signature(self,p):
        d=asdict(p);d.pop('die_pivots',None);return repr(d)
    def model(self,p):
        from .contact import ContactModel
        from .reconstruction import Reconstructor
        from .reconstruction_status import clone_plan_pool
        from .pivots import rotation_centers
        key=self._static_signature(p);saved=getattr(self.local,'model',None)
        if saved is not None and saved[0]==key:
            model,r,full_bound=saved[1:]
            centers=rotation_centers(self.case,p)
            model.bound.centers=centers;full_bound.centers=centers
            model.p=p;r.p=p
            if hasattr(r,'display_drc'):r.display_drc.p=p
            return model,r
        if saved is not None:
            saved[2].close()
            with self.lock:
                if saved[2] in self.instances:self.instances.remove(saved[2])
        model=ContactModel(self.data,self.case,self.roles,self.case['connections'],self.cache,self.motions,p)
        model.bound.acceleration=self.settings.acceleration
        full_bound=self.cache.bind(self.case,self.motions,list(self.cache.layers),p)
        full_bound.acceleration=self.settings.acceleration
        with self.lock:plans=clone_plan_pool(self.plan_pool)
        r=Reconstructor(model,p,self.settings,plan_pool=plans,geometry_pool=self.geometry_pool);r.measured_only=True
        self.local.model=(key,model,r,full_bound)
        with self.lock:self.instances.append(r)
        return model,r
    def _full_bound(self,p):
        saved=getattr(self.local,'model',None)
        if saved is not None and saved[0]==self._static_signature(p):
            return saved[3]
        bound=self.cache.bind(self.case,self.motions,list(self.cache.layers),p)
        bound.acceleration=self.settings.acceleration
        return bound
    def _nominal_wkbs(self):
        if self._nominal_wkb is not None:return self._nominal_wkb
        with self.lock:
            if self._nominal_wkb is not None:return self._nominal_wkb
            from .short_edges import contract
            minimum_edge_um=getattr(self.data,'minimum_edge_um',0.)
            cached={}
            for key,values in self.data.layers.items():
                geoms=self.cache.layers[key]['geometry']
                blobs=[]
                for i in range(len(values)):
                    g=geoms[i]
                    if g.is_empty:blobs.append(None);continue
                    try:clean=contract(g,minimum_edge_um)
                    except ValueError as exc:raise ValueError(f'图层 {key} 无法安全删除短边：{exc}') from exc
                    if clean.is_empty:blobs.append(None);continue
                    if not clean.is_valid or not np.isfinite(shapely.get_coordinates(clean)).all():
                        raise ValueError('重构结果无效，拒绝输出')
                    blobs.append(clean.wkb)
                cached[key]=tuple(blobs)
            self._nominal_wkb=cached
            return cached
    def _fast_snapshot(self,frame,*,reconstruction=True,allow_partial=False,progress=None):
        from .dxf_export import Snapshot
        if frame and (frame.get('diagnostic_only') or frame.get('reconstruction',{}).get('hard_failures')) and not (allow_partial and frame.get('partial_output_validated')):
            raise ValueError('当前为重构失败诊断图，不能输出为制造图纸；请修正连接问题。')
        replacements={}
        if frame and reconstruction:
            from .fast_preview import PreviewEngine
            replacements=PreviewEngine.final_copper({},frame.get('reconstruction',{}).get('replacements',{}))
        from .short_edges import contract
        minimum_edge_um=getattr(self.data,'minimum_edge_um',0.)
        nominal_wkbs=self._nominal_wkbs()
        layers={}
        for key,values in self.data.layers.items():
            current=frame.get('layers',{}).get(key,{}).get('polygons',values) if frame else values
            items=replacements.get(key,())
            hidden={i for q in items for i in q['indices']}
            cached_blobs=nominal_wkbs[key]
            cache_geoms=self.cache.layers[key]['geometry']
            out_blobs=[]
            for i,xy in enumerate(current):
                if i%256==0:cancel_check()
                if i in hidden:continue
                nominal=np.asarray(values[i]);actual=np.asarray(xy)
                if np.array_equal(nominal,actual):
                    blob=cached_blobs[i]
                    if blob is not None:out_blobs.append(blob)
                    continue
                g=cache_geoms[i]
                if g.is_empty:continue
                if g.geom_type=='Polygon' and not g.interiors:
                    moved=shapely.Polygon(actual)
                else:
                    delta=np.diff(nominal,axis=0);j=int(np.argmax(np.sum(delta*delta,axis=1)))
                    a=delta[j];b=actual[j+1]-actual[j];norm=float(a@a)
                    if not norm:raise ValueError(f'图层 {key} 图形 {i+1} 无有效边，不能输出')
                    co=float(a@b)/norm;si=float(a[0]*b[1]-a[1]*b[0])/norm
                    rotation=np.array([[co,si],[-si,co]])
                    moved=shapely.transform(g,lambda points:(points-nominal[j])@rotation+actual[j])
                try:clean=contract(moved,minimum_edge_um)
                except ValueError as exc:raise ValueError(f'图层 {key} 无法安全删除短边：{exc}') from exc
                if not clean.is_empty:
                    if not clean.is_valid or not np.isfinite(shapely.get_coordinates(clean)).all():
                        raise ValueError('重构结果无效，拒绝输出')
                    out_blobs.append(clean.wkb)
            for q in items:
                cancel_check()
                try:clean=contract(q['geometry'],minimum_edge_um)
                except ValueError as exc:raise ValueError(f'图层 {key} 无法安全删除短边：{exc}') from exc
                if not clean.is_empty:
                    if not clean.is_valid or not np.isfinite(shapely.get_coordinates(clean)).all():
                        raise ValueError('重构结果无效，拒绝输出')
                    out_blobs.append(clean.wkb)
            layers[key]=tuple(out_blobs)
        return Snapshot(layers,self.data.top_name,None if frame is None else np.array(frame['states'],copy=True),
                        len(getattr(self.data,'ignored_geometry',())),minimum_edge_um)
    def _zero_die_index(self,r):
        if self._ref_owners is not None:return self._ref_owners,self._always_active_refs
        with self.lock:
            if self._ref_owners is not None:return self._ref_owners,self._always_active_refs
            catalogue=r._plans_for_all_poses(None)
            by_ref={}
            for entry in catalogue:
                by_ref.setdefault((entry['layer'],entry['index']),[]).append(entry)
            ref_owners={};always_active=set()
            for ref,entries in by_ref.items():
                owners=set()
                for entry in entries:
                    owners.add(entry['owner'])
                    owners.update(entry.get('follow_owners',()))
                    part,_=r.interval(entry)
                    if part is not None and part.get('box_invalid'):always_active.add(ref)
                for v in r.vias_by_target.get(ref,()):
                    if v['owner']>=0:owners.add(v['owner'])
                ref_owners[ref]=frozenset(o for o in owners if o>=0)
            self._ref_owners=ref_owners;self._always_active_refs=frozenset(always_active)
            return ref_owners,self._always_active_refs
    def prepare(self,progress=None):
        _,r=self.model(self.p);self.ensure_plans(r,progress);self._nominal_wkbs()
    def ensure_plans(self,r,progress):
        # First batch request plans once; other lanes clone immutable nominal
        # routes instead of running the same expensive tracing concurrently.
        if r._plans is not None:return
        from .reconstruction_status import clone_plan_pool
        with self.plan_lock:
            with self.lock:r.plan_pool.update(clone_plan_pool(self.plan_pool))
            r._plans_for_all_poses(progress)
            with self.lock:self.plan_pool.update(clone_plan_pool(r.plan_pool))
    def run(self,rows,progress=None,*,preview=True,diagnostic=False,output_policy='strict'):
        if output_policy not in ('strict','omit_failed_regions'):raise ValueError('未知实测输出策略')
        states,p=pose_parameters(self.case,self.p,rows);model,r=self.model(p)
        self.ensure_plans(r,progress_slice(progress,0,.2))
        # Measured commands check their own actual-copper DRC; no other poses,
        # probability, width analysis or yield work is started.
        zero_dies={d for d in range(len(states)) if not np.any(states[d])}
        use_zero_skip=(not preview and bool(zero_dies))
        if use_zero_skip:
            ref_owners,always_active=self._zero_die_index(r)
            from .ignored_motion import linked_owners
            linked,link_failures=linked_owners(r,states)
            linked_active={ref for ref in ref_owners if any(linked.get((v['key'],v['index']),v['owner']) not in zero_dies for v in r.vias_by_target.get(ref,()))}
            active_refs=set(always_active)|linked_active|{ref for ref,owners in ref_owners.items() if any(o not in zero_dies for o in owners)}
            if not active_refs and not link_failures and len(zero_dies)==len(states):
                result=r.frame(states,progress=progress_slice(progress,20,.5),targets=set(),diagnostics=False)
                if not result.get('hard_failures') and not result.get('replacements') and not result.get('pieces'):
                    cached_zero=self._zero_pose_cache.get(output_policy)
                    if cached_zero is not None:
                        z_frame,z_snap,z_details=cached_zero
                        return dict(z_frame,rotation_centers=model.bound.centers.copy()),z_snap,copy.deepcopy(z_details)
            elif not link_failures and len(active_refs)<len(ref_owners):
                result=r.frame(states,progress=progress_slice(progress,20,.5),targets=active_refs,diagnostics=False)
                # Safety guard: if any moving net collided or needed electrical repair near a
                # skipped stationary zero-die feature, fall back to full reconstruction so
                # pair separation produces identical results.
                if result.get('hard_failures') or result.get('repair_refs'):
                    skipped=set(ref_owners)-active_refs
                    if skipped:
                        touched_refs=set(result.get('repair_refs',()))|{tuple(ref) for ref,_ in result.get('hard_failures',())}
                        pad=r.rule_space+r.tol*32
                        need_full=False
                        for tref in touched_refs:
                            if tref in skipped:need_full=True;break
                            g=next((row['geometry'] for row in result['replacements'].get(tref[0],()) if tref[1] in row['indices']),None)
                            if g is None and tref in model.ids:g=model.nodes[model.ids[tref]]['poly']
                            if g is None:continue
                            b=g.bounds;box=shapely.box(b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad)
                            for sref in skipped:
                                if sref[0].split('/')[0]==tref[0].split('/')[0] and sref in model.ids and model.nodes[model.ids[sref]]['poly'].intersects(box):
                                    need_full=True;break
                            if need_full:break
                        if need_full:
                            result=r.frame(states,progress=progress_slice(progress,20,.5),diagnostics=False)
            else:
                result=r.frame(states,progress=progress_slice(progress,20,.5),diagnostics=False)
        else:
            result=r.frame(states,progress=progress_slice(progress,20,.5),diagnostics=preview)
        bound=self._full_bound(p)
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
        frame['drc']=r.display_drc.frame(states,result['replacements'],self.settings,progress_slice(progress,70,.15),electrical=True)
        frame['drc'].extend(markers(result,self.data.dbu_um));frame['measure_drc']=bool(preview)
        original_failures=tuple(result.get('hard_failures',()));omissions=[]
        if original_failures and output_policy=='omit_failed_regions':
            from .partial_output import omit
            frame,omissions=omit(model,frame)
            frame['drc'].extend(r.display_drc.frame(states,frame['reconstruction']['replacements'],self.settings,progress_slice(progress,85,.1),electrical=True))
            for q in omissions:
                if q.get('quantization'):frame['drc'].append(dict(layer=q['layer'],layers=(q['layer'],),refs=((q['layer'],q['index']),),kind=q['reason'],geometry=shapely.box(*q['bounds_um']),connection_failure=True))
        frame['drc_locations']=locations(frame['drc'],measure=preview,separate_shorts=True)
        issues=catalogue(frame,self.case,model)
        if result.get('hard_failures') and output_policy=='strict':
            from .reconstruction_diagnostic import display,DiagnosticFailure
            from .topology_routing import failure_message
            frame['reconstruction']=display(result,frame['rotation_centers'],states);frame['diagnostic_only']=True
            details=dict(connection_failure_count=len(result['hard_failures']),connection_failures=[dict(layer=ref[0],index=ref[1],reason=reason) for ref,reason in result['hard_failures']],fixed_rdl_drc_checked=True,diagnostic_only=True,
                         drc_issues=issues,drc_count=len(issues),drc_passed=False,coordinate_frame='GDS顶层坐标，单位μm；package_relative_bounds_um以名义封装左下角为原点')
            if diagnostic:return frame,None,details
            raise DiagnosticFailure(failure_message(result['hard_failures']),frame,details)
        failures=[dict(layer=q['layer'],index=q['index'],reason=q.get('warning','无法连接')) for q in result['pieces'] if q.get('connection_failed')]
        if not preview:
            frozen=self._fast_snapshot(frame,reconstruction=True,progress=progress_slice(progress,95,.04),allow_partial=bool(frame.get('partial_output_validated')))
            r.cache.clear()  # retain bounded, immutable per-source and nominal DRC caches
        else:
            from .dxf_export import snapshot
            frozen=snapshot(self.data,self.cache,frame,reconstruction=True,progress=progress_slice(progress,95,.04),allow_partial=bool(frame.get('partial_output_validated')))
            for blobs in frozen.layers.values():
                for b in blobs:
                    cancel_check()
                    g=shapely.from_wkb(b)
                    if not g.is_valid or not np.isfinite(shapely.get_coordinates(g)).all():raise ValueError('重构结果无效，拒绝输出')
        details=dict(output_policy=output_policy,partial_output=bool(original_failures),omitted_regions=omissions,omitted_region_count=len(omissions),connection_failures=failures,connection_failure_count=max(len(failures),len(original_failures)),
                     fixed_rdl_drc_checked=True,drc_issues=issues,drc_count=len(issues),drc_passed=not issues,coordinate_frame='GDS顶层坐标，单位μm；package_relative_bounds_um以名义封装左下角为原点',rule_groups=len(result.get('violations',())),
                     scope='当前实测姿态的实际铜DRC及连接检查；不计算其他姿态、概率或良率，不代表工艺签核')
        if use_zero_skip and len(zero_dies)==len(states) and not active_refs and not link_failures and not result.get('hard_failures') and not result.get('replacements') and not result.get('pieces'):
            with self.lock:
                self._zero_pose_cache[output_policy]=(frame,frozen,copy.deepcopy(details))
        return frame,frozen,details
    def close(self):
        for r in self.instances:r.close()
        self.instances.clear();self.geometry_pool.clear();self.local=threading.local();self._zero_pose_cache.clear()


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
