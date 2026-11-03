"""Immutable geometry cache, vector transforms and equivalent state checks."""
from collections import OrderedDict
import math
import time
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from .resources import parallel_map,resource_profile
from .preview import rigid_transform,default_motion


def polygons_from_arrays(arrays):
    if isinstance(arrays,np.ndarray) and arrays.ndim==3:return shapely.polygons(arrays)
    groups={};result=np.empty(len(arrays),object)
    for i,a in enumerate(arrays):groups.setdefault(len(a),[]).append(i)
    for indices in groups.values():result[indices]=shapely.polygons(np.asarray([arrays[i] for i in indices]))
    return result

class GeometryCache:
    def __init__(self,data):
        from .geometry_errors import sanitize_data
        sanitize_data(data)
        self.data=data;self.profile=resource_profile();self.layers={}
        def compile_layer(item):
            key,arrays=item;counts=np.array([len(a) for a in arrays],np.int32);offsets=np.r_[0,counts.cumsum()]
            flat=np.concatenate(arrays) if arrays else np.empty((0,2));geometry=polygons_from_arrays(arrays)
            centers=shapely.get_coordinates(shapely.centroid(geometry))
            radii=shapely.distance(shapely.centroid(geometry),shapely.boundary(geometry))
            radii=np.where(shapely.covers(geometry,shapely.centroid(geometry)),radii,0.)
            return key,dict(flat=flat,counts=counts,offsets=offsets,centers=centers,geometry=geometry,radii=radii,
                            uniform=int(counts[0]) if len(counts) and np.all(counts==counts[0]) else 0,nominal=arrays)
        self.layers=dict(parallel_map(compile_layer,data.layers.items(),self.profile))
        self.bytes=sum(q['flat'].nbytes+q['centers'].nbytes+q['radii'].nbytes for q in self.layers.values())
    @property
    def geometries(self):return {k:v['geometry'] for k,v in self.layers.items()}
    def bind(self,case,motions,keys,parameters=None):
        return BoundGeometry(self,case,motions,keys,parameters)

class BoundGeometry:
    def __init__(self,cache,case,motions,keys,parameters=None):
        self.cache=cache;self.case=case;self.keys=list(keys);self.owners={};self.vertex_owners={};self.moving_owners={};self.workers=1;self.calibration=None
        from .pivots import rotation_centers
        self.centers=rotation_centers(case,parameters)
        for key in self.keys:
            q=cache.layers[key];mode=default_motion(case.get('roles',{}).get(key));n=len(q['centers'])
            if mode=='fixed':own=np.full(n,-1,np.int32)
            elif case.get('roles',{}).get(key)=='芯片边框':
                own=np.array([next(i for i,d in enumerate(case['dies']) if d.equals(poly)) for poly in q['geometry']],np.int32)
            elif key in case.get('ownership',{}):
                own=np.asarray(case['ownership'][key],np.int32).copy()
            else:
                # Auxiliary graphics do not acquire an owner from XY proximity.
                own=np.full(n,-2,np.int32)
            self.owners[key]=own;self.moving_owners[key]=np.unique(own[own>=0]);self.vertex_owners[key]=np.repeat(own,q['counts']) if len(self.moving_owners[key]) else None
        self.owner_radii=np.zeros(len(self.centers))
        for key in self.keys:
            owners=self.vertex_owners[key]
            if owners is None:continue
            valid=owners>=0;radius=np.linalg.norm(cache.layers[key]['flat'][valid]-self.centers[owners[valid]],axis=1)
            np.maximum.at(self.owner_radii,owners[valid],radius)
    def transform_points(self,points,owners,states):
        from .acceleration import rigid_points
        return rigid_points(np.asarray(points),np.asarray(owners),self.centers,np.asarray(states),getattr(self,'acceleration','auto'))
    def transform(self,states,workers=None):
        states=np.asarray(states,float)
        def one(key):
            q=self.cache.layers[key];own=self.vertex_owners[key]
            if own is None or not np.any(states[self.moving_owners[key]]):return key,dict(polygons=q['nominal'],centers=q['centers'])
            flat=self.transform_points(q['flat'],own,states)
            arrays=flat.reshape(-1,q['uniform'],2) if q['uniform'] else [flat[a:b] for a,b in zip(q['offsets'][:-1],q['offsets'][1:])]
            return key,dict(polygons=arrays,centers=self.transform_points(q['centers'],self.owners[key],states))
        profile=dict(self.cache.profile)
        profile['workers']=workers if workers is not None else self.workers
        return dict(parallel_map(one,self.keys,profile))
    def calibrate(self,states):
        """Measure once in the preview worker; parallelism must earn its overhead."""
        moving=[k for k in self.keys if self.vertex_owners[k] is not None]
        workers=min(self.cache.profile['workers'],len(moving))
        if workers<2 or sum(len(self.cache.layers[k]['flat']) for k in moving)<80000:
            self.calibration=dict(workers=1,reason='Small workload');return self.transform(states)
        samples={1:[],workers:[]};frames={}
        for _ in range(2):
            for n in (1,workers):
                start=time.perf_counter();frames[n]=self.transform(states,workers=n);samples[n].append((time.perf_counter()-start)*1000)
        self.workers=workers if np.median(samples[workers])<.9*np.median(samples[1]) else 1
        self.calibration=dict(workers=self.workers,samples_ms=samples,reason='Use threads only with at least 10% measured reduction')
        return frames[self.workers]


def check_frame(bound,roles,p,result,states,layers,scheme,connections=None,check_interfaces=True,include_pairs=True,active_owners=None):
    case=bound.case;tol=max(1e-9,bound.cache.data.dbu_um*1e-6);failures=[]
    def fail(kind,points,owners=None):
        points=np.asarray(points).reshape(-1,2)
        if owners is None:owners=[-1]*len(points)
        failures.extend(dict(kind=kind,point=point.tolist(),owner=int(owner)) for point,owner in zip(points,owners))
    if include_pairs and np.any(states):
        for key,owners in bound.owners.items():
            if roles.get(key) not in ('运动RDL','运动过孔'):continue
            for index in np.flatnonzero(owners<0):
                fail(f'运动归属未解析：{key} #{index+1}，保持名义位置，不能判为随动通过',[bound.cache.layers[key]['centers'][index]])
    moved=[Polygon(rigid_transform(np.asarray(d.exterior.coords),c,z)) for d,c,z in zip(case['dies'],bound.centers,states)]
    for i,die in enumerate(moved):
        if active_owners is not None and i not in active_owners:continue
        if not case['board'].covers(die) or die.distance(case['board'].boundary)<=p.min_package_clearance_um+tol:fail(f'DIE {i+1}触碰封装净距边界',[die.centroid.coords[0]],[i])
        for j in range(i if include_pairs else 0):
            if die.distance(moved[j])<=p.min_die_gap_um+tol:fail(f'DIE {j+1}/{i+1}触碰芯片净距边界',[np.mean([die.centroid.coords[0],moved[j].centroid.coords[0]],axis=0)])
    obstacle_keys=[k for k in layers if roles.get(k)=='禁布区']
    if obstacle_keys:
        obstacles=np.concatenate([polygons_from_arrays(layers[k]['polygons']) for k in obstacle_keys]);tree=STRtree(obstacles)
        for key,layer in layers.items():
            if roles.get(key) not in ('运动过孔','固定过孔','运动RDL','固定RDL'):continue
            indices=np.arange(len(bound.owners[key]))
            if active_owners is not None:indices=indices[np.isin(bound.owners[key],list(active_owners))]
            if not len(indices):continue
            polygons=polygons_from_arrays([layer['polygons'][i] for i in indices])
            if not np.all(shapely.is_valid(polygons)):raise ValueError(f'图层{key}含无效几何，无法检查禁布区')
            pairs=tree.query(polygons,predicate='intersects')
            if pairs.size:
                hits=indices[np.unique(pairs[0])];fail(f'图层{key}触碰禁布区',layer['centers'][hits],bound.owners[key][hits])
    if check_interfaces:
        connections=connections or case.get('connections')
        from .contact import ContactModel
        model=ContactModel(bound.cache.data,case,roles,connections,bound.cache,parameters=p)
        for issue in model.current(states,p)['issues']:
            if issue['scheme']==scheme:fail(issue['kind'],[issue['point']])
        from .edge_clearance import edge_violations
        for q in edge_violations(model,states,p,None):fail(q['kind'],[q['point']])
    return dict(problem_percent=100 if failures else 0,failures=failures,scope='已导入几何的净距、禁布区和有名义向下连接的接口窗口；不是完整DRC')

class PreviewEngine:
    """One engine per geometry/role/motion generation; called by one UI worker."""
    def __init__(self,cache,case,roles,motions,p,result,connections,cache_group=None,cache_identity=None,contact_model=None):
        self.bound=cache.bind(case,motions,[k for k in cache.layers if roles.get(k,'不参与')!='不参与'],p)
        self.roles=roles;self.p=p;self.result=result;self.connections=connections;self.cache_group=cache_group
        from .session_cache import SessionCache
        if cache_group is not None and cache_identity is not None:
            with cache_group.lock:
                if cache_identity not in cache_group.frame_stores:cache_group.frame_stores[cache_identity]=SessionCache(group=cache_group)
                self.frames=cache_group.frame_stores[cache_identity]
                self.frames.pose_source=(cache_identity,'预演帧',None)
        else:self.frames=SessionCache(group=cache_group)
        self.deformer=None;self.deformers={}
        from .contact import ContactModel
        self.contact=contact_model if contact_model is not None else ContactModel(cache.data,case,roles,connections,cache,motions,p)
        from .session_cache import EphemeralCache
        self.intermediate=EphemeralCache()
    def configure_deformation(self,enabled,settings):
        self.bound.acceleration=self.contact.bound.acceleration=settings.acceleration
        key=(bool(enabled and self.result is not None),*settings.deformation_key())
        if getattr(self,'deformation_key',None)==key:return
        if self.deformer is not None:self.deformer.release_workers()
        # Commit state only after successful initialization: retry remains possible.
        from .deformation import DeformationPreview
        deformer=self.deformers.get(key)
        if key[0] and deformer is None:
            deformer=DeformationPreview(self.contact,self.p,self.result,settings,cache_group=self.cache_group)
            self.deformers[key]=deformer
            deformer.cache.pose_source=(getattr(self,'signature',None),'型变',key)
        self.deformer=deformer;self.deformation_key=key
    def configure_drc(self,enabled,settings):
        key=(bool(enabled),settings.deformation_min_angle_deg,settings.drc_tolerance_um)
        if enabled and not hasattr(self,'global_drc'):
            from .drc import GlobalDRC
            self.global_drc=GlobalDRC(self.contact,self.p)
        self.drc_key=key;self.drc_enabled=enabled;self.drc_settings=settings
    def configure_width(self,enabled,settings):
        key=settings.width_key() if enabled else (False,)
        if getattr(self,'width_key',(False,))==key:return
        if enabled:
            from .width_analysis import WidthAnalyzer
            if not hasattr(self,'width_analyzers'):self.width_analyzers={}
            if key not in self.width_analyzers:self.width_analyzers[key]=WidthAnalyzer(self.contact,self.p,settings,self.cache_group)
            self.width_analyzer=self.width_analyzers[key]
            self.width_analyzer.cache.pose_source=(getattr(self,'signature',None),'宽度',key)
        self.width_key=key
    def configure_reconstruction(self,enabled,settings):
        key=settings.reconstruction_key() if enabled else (False,)
        if getattr(self,'reconstruction_config',(False,))==key:return
        self.reconstructor=None
        if enabled:
            from .reconstruction import Reconstructor
            if not hasattr(self,'reconstructors'):self.reconstructors={}
            if key not in self.reconstructors:self.reconstructors[key]=Reconstructor(self.contact,self.p,settings,self.cache_group)
            self.reconstructor=self.reconstructors[key]
            self.reconstructor.cache.pose_source=(getattr(self,'signature',None),'重构',key)
        self.reconstruction_config=key
    def cache_key(self):
        return (getattr(self,'deformation_key',None),getattr(self,'drc_key',None),getattr(self,'width_key',(False,)),
                getattr(self,'reconstruction_config',(False,)))
    def close(self):
        self.frames.close();self.intermediate.clear()
        for d in self.deformers.values():d.close()
        self.deformers.clear()
        for analyzer in getattr(self,'width_analyzers',{}).values():analyzer.close()
        for reconstructor in getattr(self,'reconstructors',{}).values():reconstructor.close()
    def frame(self,states,progress=lambda *_:None,quick=False):
        states=np.array(states,dtype=float,copy=True);states[states==0]=0.
        if not np.any(states):quick=False
        key=(self.cache_key(),np.asarray(states,float).tobytes(),bool(quick))
        complete=(self.cache_key(),np.asarray(states,float).tobytes(),False)
        cached=self.frames.get(complete) if quick else None
        if cached is None:cached=self.frames.get(key)
        if cached is None and quick:cached=self.intermediate.get(key)
        if cached is not None:
            progress(100,'重用已完成的预演状态缓存');return cached
        progress(0,'生成当前几何状态')
        layers=self.bound.calibrate(states) if self.bound.calibration is None else self.bound.transform(states)
        current=self.contact.current(states,self.p)
        deformation=self.deformer.frame(states,current,progress,quick=quick) if self.deformer is not None else None
        if deformation and deformation['replacements']:current=self.contact.current(states,self.p,deformation['replacements'])
        mechanical=check_frame(self.bound,self.roles,self.p,self.result,states,layers,1,self.connections,False)
        checks={s:dict(mechanical,failures=list(mechanical['failures'])) for s in (1,2,3)}
        for s,check in checks.items():
            check['failures'] += [dict(kind=q['kind'],point=q['point']) for q in current['issues'] if q['scheme']==s]
            check['problem_percent']=100 if check['failures'] else 0
            check['scope']='当前状态：线距控制区与下一金属层其他几何网络接触，另检查净距/禁布区'
        frame=dict(motion_issues=list(self.connections.ownership_notes) if np.any(states) else [],layers=layers,checks=checks,states=np.asarray(states).copy(),rotation_centers=self.bound.centers.copy(),contact=current)
        if deformation is not None:frame['deformation']=deformation
        if getattr(self,'reconstructor',None) is not None:
            # Reconstructed fixed RDL is a hypothesis about an operator's split,
            # so the plot and the piece table share its accepted display geometry.
            frame['reconstruction']=self.reconstructor.frame(states,deformation,progress,quick=quick)
        if getattr(self,'drc_enabled',False):
            frame['drc']=deformation['violations'] if deformation is not None else self.global_drc.frame(states,settings=self.drc_settings,progress=progress)
        if deformation is not None or getattr(self,'drc_enabled',False):
            from .drc_rules import effective_rules
            cfg=self.deformer.settings if self.deformer is not None else self.drc_settings
            width,space=effective_rules(self.p,cfg)
            frame['drc_rules']=dict(tolerance_um=cfg.drc_tolerance_um,nominal_width_um=self.p.line_width_um,nominal_space_um=self.p.line_space_um,effective_width_um=width,effective_space_um=space,annulus_um=self.p.annulus_um)
        from .edge_clearance import edge_violations
        cfg=getattr(self,'drc_settings',None) or getattr(getattr(self,'width_analyzer',None),'settings',None)
        overrides=frame.get('deformation',{}).get('replacements',{})
        edges=([v for v in frame['drc'] if v['kind']=='封装边缘线距不足'] if 'drc' in frame else
               edge_violations(self.contact,states,self.p,cfg,overrides))
        frame['edge_clearance']=edges
        for check in checks.values():
            check['failures'].extend(dict(kind=q['kind'],layer=q['layer'],point=q['point']) for q in edges)
            if edges:check['problem_percent']=100
        if getattr(self,'width_key',(False,))[0]:
            if frame.get('reconstruction',{}).get('replacements'):
                by_ref={}
                for source in (overrides,frame['reconstruction']['replacements']):
                    for k,items in source.items():
                        for q in items:
                            for i in q['indices']:by_ref[k,i]=q
                changed={}
                for (k,i),q in by_ref.items():
                    if i==q['indices'][0]:changed.setdefault(k,[]).append(q)
                edges=edge_violations(self.contact,states,self.p,cfg,changed)
            probability=self.width_analyzer.probability
            changed_copper=bool(overrides or frame.get('reconstruction',{}).get('replacements'))
            if probability is not None and edges and not changed_copper:
                annotated=[]
                for q in edges:
                    layer=q['layer'];index=q['indices'][0];node=self.contact.nodes[self.contact.ids[layer,index]]
                    event=probability.edge(layer,index,node['poly'],node['owner'],q['required_um'])
                    annotated.append(dict(q,event_probability=event))
                edges=annotated
            frame['width_analysis']=dict(self.width_analyzer.frame(frame,progress,quick=quick),edge_violations=edges)
        if quick:self.intermediate.put(key,frame)
        else:
            if not np.any(states):self.frames.pin(key)
            self.frames[key]=frame
        return frame

    def global_frame(self,nominal,settings,progress=lambda *_:None):
        """Iterate every representative pose, with honest post-adaptation bounds.

        A finite pose catalogue cannot certify the probability of an adaptive
        route. Original passing probability upper bounds remain valid because
        adaptation may never introduce a previously absent contact. A previously
        failing lower bound cannot be reused after changing the geometry.
        """
        if self.deformer is None:return nominal
        global_key=('global',self.cache_key(),settings.numeric_key())
        cached=self.frames.get(global_key)
        if cached is not None:return cached
        from .representatives import RepresentativeExtremes
        catalogue=RepresentativeExtremes.from_result(self.bound.case,self.p,self.result)
        poses=[('名义位置',nominal['states'])]+[(q['name'],q['states']) for q in catalogue]
        summaries=[];remaining={};released=set()
        for i,(name,states) in enumerate(poses):
            progress(round(95*i/len(poses)),f'全局重点型变预演 {i+1}/{len(poses)}')
            f=nominal if i==0 else self.frame(states,lambda n,t:progress(round(95*(i+n/100)/len(poses)),t))
            d=f['deformation'];summaries.append(dict(name=name,states=states.tolist(),
                accepted=d['accepted_count'],best_effort=d.get('best_effort_count',0),initial=d['initial_blocked_count'],remaining=d['blocked_count'],released=d['released_count'],iterations=d['iterations']))
            for q in f['contact']['issues']:
                identity=(q['source_layer'],q['source_index'],q['scheme'])
                if identity not in remaining or q['ratio']>remaining[identity]['ratio']:remaining[identity]=dict(q)
        keys=('single_bound_x_um','single_bound_y_um','multi_bound_x_um','multi_bound_y_um','bound_angle_deg','target_nsigma','reference_nsigma','mean_x_um','mean_y_um','mean_angle_deg','rho_xy','per_die_enabled','die_bounds','rotation_center','rotation_x_um','rotation_y_um','die_pivots','annulus_um','line_space_um')
        result=self.result or {};saved=result.get('parameters',{})
        current=(all(saved.get(k)==getattr(self.p,k) for k in keys) and result.get('roles')==self.roles and result.get('connections',{}).get('layer_order')==self.connections.order)
        baseline=result.get('contact_analysis',{}) if current else self.contact.screen(self.p,settings,progress)
        probabilities={(q['source_layer'],q['source_index']):q for q in baseline.get('probabilities',[])}
        issues=[]
        originals={(q['source_layer'],q['source_index'],q['scheme']):q for q in baseline.get('issues',[])}
        for v in baseline.get('unresolved',[]):
            record=self.contact.by_feature.get((v['source_layer'],v['source_index']))
            if record is None or not record['foreign']:continue
            node=self.contact.nodes[record['foreign'][0]]
            for scheme in (1,2,3):
                row=self.contact.issue(record,node,record['center'],record['poly'].distance(node['poly']),self.p.annulus_um+self.p.line_space_um,self.p.line_space_um,scheme,'原概率待细化')
                row.update(probability_upper=v['upper'],threshold=v['threshold']);originals[record['key'],record['index'],scheme]=row
        # Best-effort candidates may introduce contacts that did not exist in
        # the source. Include those interfaces; the old probability bound is
        # not a certificate for a changed, unverified route.
        for identity,q in remaining.items():
            if identity not in originals:originals[identity]=dict(q,probability_upper=1.,threshold=self.p.failure_threshold)
        has_best=any(q.get('best_effort',0) for q in summaries)
        for identity,original in originals.items():
            q=dict(remaining.get(identity,original));v=probabilities.get(identity[:2],{})
            q.update(adaptive_unresolved=True,kind='型变后仍有代表状态接触' if identity in remaining else '代表状态已释放；概率待细化',
                     probability_lower=0.,probability_upper=1. if has_best else v.get('upper',original.get('probability_upper',1.)),threshold=original.get('threshold',0.))
            q['detail']='所有代表状态均迭代到无新增充足窗口；型变后连续接触概率尚无证书，不能把有限预演无接触当作全局通过。'
            issues.append(q)
            if identity not in remaining:released.add(identity[:2])
        out=dict(nominal);out['contact']=dict(nominal['contact'],issues=issues)
        out['global_deformation']=dict(discrete_package_states=catalogue.full.total+1,focused_shifted_states=catalogue.total,drc_tolerance_um=settings.drc_tolerance_um,statistics_match_saved_evaluation=current,target_sigma=self.p.target_nsigma,annulus_um=self.p.annulus_um,line_width_um=self.p.line_width_um,line_space_um=self.p.line_space_um,die_bounds=[[(self.p.for_die(i).single_bound_x_um if len(self.bound.centers)==1 else self.p.for_die(i).multi_bound_x_um),(self.p.for_die(i).single_bound_y_um if len(self.bound.centers)==1 else self.p.for_die(i).multi_bound_y_um),self.p.for_die(i).bound_angle_deg] for i in range(len(self.bound.centers))],states=summaries,best_effort_states=sum(bool(q.get('best_effort')) for q in summaries),representative_released=len(released),continuous_probability='待细化；带DRC候选不复用原概率上界；有限代表状态不构成连续概率证书',scope='代表状态最终布局，不是连续概率穷举')
        self.frames.pin(global_key);self.frames[global_key]=out
        progress(100,'全局代表状态型变完成；已标明连续概率未决项')
        return out
