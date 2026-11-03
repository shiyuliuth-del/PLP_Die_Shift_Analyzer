"""Geometric networks, current-state contacts, and bounded-normal probability decisions."""
from dataclasses import replace
import math
import numpy as np
import shapely
from shapely.geometry import Polygon,Point,box
from shapely.ops import nearest_points,unary_union
from shapely.strtree import STRtree
from .stack import PHYSICAL
from .preview import rigid_transform,default_motion
from .statistics import statistical_state
from .fast_preview import GeometryCache

class UnionFind:
    def __init__(self,n):self.parent=np.arange(n)
    def find(self,x):
        while self.parent[x]!=x:self.parent[x]=self.parent[self.parent[x]];x=self.parent[x]
        return int(x)
    def union(self,a,b):
        a,b=self.find(a),self.find(b)
        if a!=b:self.parent[b]=a

def nearby_foreign(model,record,via,radius):
    """Exact dwithin filter on nominal fixed planes; preserve record ordering."""
    foreign=np.asarray(record['foreign'],int)
    trees=getattr(model,'fixed_trees',None)
    if not len(foreign) or not trees:
        return foreign[shapely.dwithin(via,model.node_geometry[foreign],radius)]
    hits=set()
    for plane in {k.split('/')[0] for k in record['target_layers']}:
        ids,tree=trees[plane]
        hits.update(ids[i] for i in tree.query(via,predicate='dwithin',distance=radius))
    return np.asarray([j for j in foreign if j in hits],int)


class ContactModel:
    def __init__(self,data,case,roles,connections,cache=None,motions=None,parameters=None):
        from .layer_roles import canonical_roles
        roles=canonical_roles(roles);self.data=data;self.case=case;self.roles=roles;self.connections=connections
        case['ownership']=connections.resolve_ownership(case)
        self.cache=cache or GeometryCache(data)
        self.motions={k:default_motion(r) for k,r in roles.items()}
        keys=[k for k in connections.order if roles.get(k) in PHYSICAL]
        self.bound=self.cache.bind(case,self.motions,keys,parameters)
        self.nodes=[];self.ids={};self.planes={}
        for key in keys:
            ids=[]
            for index,poly in enumerate(self.cache.layers[key]['geometry']):
                ids.append(len(self.nodes));self.ids[key,index]=len(self.nodes)
                self.nodes.append(dict(key=key,index=index,poly=poly,owner=int(self.bound.owners[key][index])))
            self.planes.setdefault(key.split('/')[0],[]).extend(ids)
        uf=UnionFind(len(self.nodes))
        for ids in self.planes.values():
            geom=np.array([self.nodes[i]['poly'] for i in ids],object);tree=STRtree(geom)
            for start in range(0,len(ids),512):
                a,b=tree.query(geom[start:start+512],predicate='intersects')
                for i,j in zip(a+start,b):
                    if i<j:uf.union(ids[i],ids[j])
        for link in connections.links:uf.union(self.ids[link['upper'],link['upper_index']],self.ids[link['lower'],link['lower_index']])
        for i,node in enumerate(self.nodes):node['net']=uf.find(i)
        self.records=[]
        for node in self.nodes:
            key=node['key'];index=node['index']
            if roles[key]!='运动过孔' or node['owner']<0:continue
            targets=connections.fixed_interfaces(key,index)
            if not targets:continue
            planes={target.split('/')[0] for target,_ in targets}
            lower=[j for plane in planes for j in self.planes[plane] if roles[self.nodes[j]['key']]=='固定RDL']
            self.records.append(dict(**node,schemes=(1,2,3),lower=lower,virtual=False,
                                     foreign=[j for j in lower if self.nodes[j]['net']!=node['net']],
                                     center=list(node['poly'].centroid.coords[0]),target_layers=sorted({k for k,_ in targets})))
        self.interface_masks={key:np.zeros(len(self.cache.layers[key]['geometry']),bool) for key in keys}
        for r in self.records:self.interface_masks[r['key']][r['index']]=True
        self.network_count=len({x['net'] for x in self.nodes})
        roots={}
        for node in self.nodes:
            roots.setdefault(node['net'],set()).update(connections.possible_owners.get(node['key'],[()] * len(self.cache.layers[node['key']]['geometry']))[node['index']])
        self.interdie_networks=[dict(network=net,dies=[i+1 for i in sorted(owners)]) for net,owners in roots.items() if len(owners)>1]
        self.node_geometry=np.array([x['poly'] for x in self.nodes],object)
        self.by_feature={(r['key'],r['index']):r for r in self.records}
        self.fixed_trees={}
        for plane,ids in self.planes.items():
            fixed=[j for j in ids if roles[self.nodes[j]['key']]=='固定RDL']
            if fixed:self.fixed_trees[plane]=(fixed,STRtree([self.nodes[j]['poly'] for j in fixed]))
    def configure_parameters(self,p):
        signature=(p.per_die_enabled,p.rotation_center,p.rotation_x_um,p.rotation_y_um,tuple(tuple(v) for v in p.die_pivots))
        if getattr(self,'_pivot_signature',None)==signature:return
        backend=getattr(self.bound,'acceleration','auto')
        self.bound=self.cache.bind(self.case,self.motions,self.bound.keys,p);self.bound.acceleration=backend;self._pivot_signature=signature
    def spacing(self,record,p):return p.line_space_um
    def moved(self,poly,owner,states):
        if owner<0:return poly
        c=self.bound.centers[owner]
        return shapely.set_coordinates(poly,rigid_transform(shapely.get_coordinates(poly),c,states[owner]))
    def current(self,states,p,replacements=None,counts_only=False,owners=None):
        self.configure_parameters(p)
        states=np.asarray(states);issues=[];regions=[];intrusions=[];tol=max(1e-9,self.data.dbu_um*1e-6)
        records=self.records
        if not records:return np.zeros(len(states),int) if counts_only else dict(issues=[],regions=[],intrusions=[],problem_percent=0)
        if not hasattr(self,'_record_geometry'):
            self._record_geometry=np.array([r['poly'] for r in records],object)
            self._via_owners=np.array([r['owner'] for r in records]);self._record_coordinates=shapely.get_coordinates(self._record_geometry)
            self._record_owners=np.repeat([r['owner'] for r in records],shapely.get_num_coordinates(self._record_geometry))
            self._record_planes=[{self.nodes[j]['key'].split('/')[0] for j in r['lower']} for r in records]
        selection=None
        if counts_only and owners is not None:
            selection=np.flatnonzero(np.isin(self._via_owners,list(owners)))
            if not len(selection):return np.zeros(len(states),int)
            shapes=self._record_geometry[selection]
            xy,ids=shapely.get_coordinates(shapes,return_index=True)
            coordinates=self.bound.transform_points(xy,self._via_owners[selection][ids],states)
        else:
            shapes=self._record_geometry;coordinates=self.bound.transform_points(self._record_coordinates,self._record_owners,states)
        vias=shapely.set_coordinates(shapes.copy(),coordinates)
        radius=p.annulus_um+p.line_space_um
        pairs=[];overrides={};removed=set()
        for key,values in (replacements or {}).items():
            for q in values:
                ids=[self.ids[key,index] for index in q['indices']]
                overrides[ids[0]]=q['geometry'];removed.update(ids[1:])
        for plane,(ids,tree) in self.fixed_trees.items():
            if overrides:
                ids=[j for j in ids if j not in removed]
                tree=STRtree([overrides.get(j,self.nodes[j]['poly']) for j in ids])
            a,b=tree.query(vias,predicate='dwithin',distance=radius+tol)
            if selection is not None:a=selection[a]
            pairs.extend((int(i),ids[int(j)]) for i,j in zip(a,b) if plane in self._record_planes[i] and records[i]['net']!=self.nodes[ids[j]]['net'])
        if counts_only:
            counts=np.zeros(len(states),int)
            for i,_ in pairs:counts[records[i]['owner']]+=len(records[i]['schemes'])
            return counts
        buffer_key=(p.annulus_um,radius)
        if getattr(self,'_buffer_key',None)!=buffer_key:
            self._buffer_key=buffer_key;self._buffer_templates=[]
            for distance in buffer_key:
                geometries=shapely.buffer(self._record_geometry,distance,quad_segs=24) if distance else self._record_geometry
                xy=shapely.get_coordinates(geometries);owners=np.repeat([r['owner'] for r in records],shapely.get_num_coordinates(geometries))
                self._buffer_templates.append((geometries,xy,owners))
        # Buffering commutes with rigid transforms: reuse topology and round joins.
        annuli,spacing=[shapely.set_coordinates(g.copy(),self.bound.transform_points(xy,owners,states)) for g,xy,owners in self._buffer_templates]
        region_bounds=shapely.bounds(spacing)
        for i,r in enumerate(records):
            regions.append(dict(key=r['key'],index=r['index'],virtual=False,schemes=(1,2,3),annulus=annuli[i],spacing=spacing[i],bounds=region_bounds[i]))
        if pairs:
            ri,ni=np.asarray(pairs).T;others=np.array([overrides.get(int(j),self.nodes[j]['poly']) for j in ni],object) if overrides else self.node_geometry[ni]
            distances=shapely.distance(vias[ri],others);intersections=shapely.intersection(spacing[ri],others)
            areas=shapely.area(intersections)
            for i,j,distance,intersection,area in zip(ri,ni,distances,intersections,areas):
                record=records[i];node=self.nodes[j]
                if area>0:
                    intrusions.append(dict(key=record['key'],index=record['index'],target_layer=node['key'],target_index=node['index'],geometry=intersection,area_um2=float(area)))
                    point=intersection.representative_point().coords[0]
                else:point=nearest_points(vias[i],overrides.get(int(j),node['poly']))[1].coords[0]
                issues.extend(self.issue(record,node,point,distance,radius,p.line_space_um,scheme,'已发生接触',states) for scheme in record['schemes'])
        return dict(issues=issues,regions=regions,intrusions=intrusions,problem_percent=100 if issues else 0)
    def issue(self,record,node,point,distance,radius,space,scheme,kind,states=None):
        return dict(scheme=scheme,kind=kind,die=record['owner']+1,need=float(radius),allow=float(distance),unit='μm',
            ratio=(float(radius)/distance if distance>0 else math.inf),point=list(map(float,point)),
            origin=record['center'],source_layer=record['key'],target_layer=node['key'],target_index=node['index'],
            source_index=record['index'],foreign_net=node['net'],source_net=record['net'],spacing_um=space,
            virtual=record['virtual'],states=states.tolist() if states is not None else None,
            anchors=[dict(point=list(map(float,point)),owner=-1,layer=None)],
            detail='按下层连接追溯归属；仅检查实际运动过孔与相邻固定RDL的其他网络。')
    def screen(self,p,settings=None,progress=lambda *_:None):
        self.configure_parameters(p)
        from .settings import ViewSettings
        from .contact_probability import contact_probability,reachable_targets,location_witness,local_obstacles
        from .resources import parallel_map
        cfg=settings or ViewSettings();ratios={s:0. for s in (1,2,3)}
        common=dict(network_count=self.network_count,
            checked={s:sum(bool(r['foreign']) for r in self.records) for s in (1,2,3)},
            interfaces={s:len(self.records) for s in (1,2,3)})
        if math.isinf(p.target_nsigma):
            # Blank row is the actual nominal drawing; no hypothetical random state.
            current=self.current(np.zeros((len(self.bound.centers),3)),p)
            for issue in current['issues']:
                issue['states']=None;ratios[issue['scheme']]=max(ratios[issue['scheme']],issue['ratio'])
            return dict(**common,issues=current['issues'],ratios=ratios,unresolved=[],probabilities=[],mode='binary',scope='∞：名义位置二值接触；具体预演始终二值判定')
        def solve(record):
            ids=reachable_targets(self,record,p)
            copper=local_obstacles(self,record,p,ids)
            joint=contact_probability(self,record,p,cfg.contact_box_budget,ids,copper)
            locations=[]
            if p.target_nsigma>0:
                for i,node in enumerate(ids):
                    if len(ids)==1:event=joint
                    elif joint['upper']<=p.failure_threshold:
                        # A subset inherits the union upper bound, but never its lower bound
                        # or a different target's witness.
                        w=joint['witness']
                        event=dict(lower=0.,upper=joint['upper'],witness=w if w and w['node']==node else None)
                    else:event=contact_probability(self,record,p,cfg.contact_box_budget,[node],copper[i:i+1])
                    low=event['upper']<=p.failure_threshold
                    witness=event['witness']
                    if low and witness is None and event['upper']>0:
                        witness=location_witness(self,record,p,node)
                    target=self.nodes[node]
                    locations.append(dict(source_layer=record['key'],source_index=record['index'],
                        target_layer=target['key'],target_index=target['index'],low=low,
                        lower=event['lower'],upper=event['upper'],threshold=p.failure_threshold,witness=witness))
            return joint,locations
        values=parallel_map(solve,self.records,progress=lambda done,total:progress(72+round(23*done/max(1,total)),f'接触概率积分 {done}/{total}'))
        issues=[];unresolved=[];probabilities=[];locations=[];low_issues=[]
        for record,(value,items) in zip(self.records,values):
            locations.extend(items)
            for loc in items:
                witness=loc['witness']
                if not loc['low'] or witness is None:continue
                node=self.nodes[witness['node']]
                for scheme in record['schemes']:
                    row=self.issue(record,node,witness['point'],record['poly'].distance(node['poly']),p.annulus_um+p.line_space_um,p.line_space_um,scheme,'低概率接触')
                    row.update(low_probability=True,probability_lower=loc['lower'],probability_upper=loc['upper'],threshold=loc['threshold'],
                               detail='该孔与这一处异网固定铜的接触概率上界低于或等于良率门限。坐标来自可达接触姿态。')
                    low_issues.append(row)
            q=dict(value,source_layer=record['key'],source_index=record['index'],die=record['owner']+1)
            probabilities.append(q)
            if value['verdict']=='概率待细化':unresolved.append(q)
            for scheme in record['schemes']:ratios[scheme]=max(ratios[scheme],(value['lower']/value['threshold'] if value['threshold'] else math.inf if value['verdict']=='窗口不足' else 0.))
            if value['verdict']!='窗口不足':continue
            witness=value['witness'];node=self.nodes[witness['node']]
            distance=record['poly'].distance(node['poly'])
            for scheme in record['schemes']:
                row=self.issue(record,node,witness['point'],distance,p.annulus_um+p.line_space_um,p.line_space_um,scheme,'正概率接触（0级）' if p.target_nsigma==0 else '接触概率超限')
                row.update(probability_lower=value['lower'],probability_upper=value['upper'],threshold=value['threshold'],positive_probability_proven=value.get('positive_probability_proven',False))
                row['detail']=f"接触概率区间 [{value['lower']:.6g}, {value['upper']:.6g}]，允许上限 {value['threshold']:.6g}；接口与全部其他网络的联合事件。"
                issues.append(row)
        return dict(**common,issues=issues,low_issues=low_issues,locations=locations,location_threshold=p.failure_threshold if p.target_nsigma else None,ratios=ratios,unresolved=unresolved,probabilities=probabilities,mode='probability',
            scope='名义行：每接口联合接触概率与2Φ(−k)比较；上下界未能决定则待细化，不输出虚假不足或通过')


def support_face_length(poly,normal,maximum):
    points=np.asarray(poly.exterior.coords);values=points@normal
    extreme=values.max() if maximum else values.min()
    face=points[np.abs(values-extreme)<=1e-10]@np.array([-normal[1],normal[0]])
    return float(np.ptp(face)) if len(face)>1 else 0.

def support_shape(poly,owner,centers,p,count,padding=0.):
    if owner<0:return dict(kind='poly',poly=poly,padding=padding,random=False)
    p=p.for_die(owner);mu,cov,_=statistical_state(p,count);c=centers[owner]
    sx=(p.single_bound_x_um if count==1 else p.multi_bound_x_um)/p.scale_nsigma
    sy=(p.single_bound_y_um if count==1 else p.multi_bound_y_um)/p.scale_nsigma
    # A positive normal variance must never be rounded to zero for infinity.
    rank=2 if sx>0 and sy>0 and abs(p.rho_xy)<1 else (1 if sx>0 or sy>0 else 0)
    if rank==2:return dict(kind='plane',random=True)
    transformed=Polygon(rigid_transform(np.asarray(poly.exterior.coords),c,mu))
    if rank==1:
        axis=np.array([sx,p.rho_xy*sy]) if sx>0 and sy>0 else np.array([float(sx>0),float(sy>0)])
        axis/=np.linalg.norm(axis);normal=np.array([-axis[1],axis[0]])
        if p.bound_angle_deg/p.scale_nsigma>0:
            r=float(np.linalg.norm(np.asarray(poly.exterior.coords)-c,axis=1).max())+padding;middle=float((c+mu[:2])@normal);lo,hi=middle-r,middle+r
        else:
            values=np.asarray(transformed.exterior.coords)@normal;lo,hi=float(values.min())-padding,float(values.max())+padding
        faces=None if p.bound_angle_deg/p.scale_nsigma>0 else (support_face_length(transformed,normal,False),support_face_length(transformed,normal,True))
        return dict(kind='band',normal=normal,lo=lo,hi=hi,faces=faces,random=True)
    if p.bound_angle_deg/p.scale_nsigma>0:
        return dict(kind='annulus',center=c+mu[:2],inner=max(0.,poly.distance(Point(c))-padding),outer=float(np.linalg.norm(np.asarray(poly.exterior.coords)-c,axis=1).max())+padding,random=True)
    return dict(kind='poly',poly=transformed,padding=padding,random=False)

def support_contact(a,oa,b,ob,centers,p,count,padding_a=0.):
    if oa==ob:
        # Shared DIE states cancel exactly, including in unbounded support.
        left=a.buffer(padding_a) if padding_a else a
        return left.intersects(b),nearest_points(left,b)[1].coords[0]
    A=support_shape(a,oa,centers,p,count,padding_a);B=support_shape(b,ob,centers,p,count)
    # Unbounded full-rank normal translation has positive density everywhere.
    def sample(S):
        if S['kind']=='poly':return np.array(S['poly'].representative_point().coords[0])
        if S['kind']=='band':return S['normal']*((S['lo']+S['hi'])/2)
        if S['kind']=='annulus':return S['center']+[(S['inner']+S['outer'])/2,0]
        return np.zeros(2)
    if A['kind']=='plane' or B['kind']=='plane':return True,sample(B if A['kind']=='plane' else A)
    if A['kind']=='poly' and B['kind']=='poly':
        return A['poly'].distance(B['poly'])<=A['padding']+B['padding'],nearest_points(A['poly'],B['poly'])[1].coords[0]
    if A['kind']=='band' and B['kind']=='band':
        matrix=np.array([A['normal'],B['normal']]);values=np.array([(A['lo']+A['hi'])/2,(B['lo']+B['hi'])/2])
        if abs(np.linalg.det(matrix))>1e-10:return True,np.linalg.solve(matrix,values)
        sign=float(A['normal']@B['normal']);lo,hi=(B['lo'],B['hi']) if sign>0 else (-B['hi'],-B['lo'])
        left=max(A['lo'],lo);right=min(A['hi'],hi)
        hit=right>left
        if right==left and A['faces'] is not None and B['faces'] is not None:
            faces=B['faces'] if sign>0 else B['faces'][::-1]
            hit=(A['faces'][1]+faces[0]>0 if A['hi']==lo else A['faces'][0]+faces[1]>0)
        return hit,A['normal']*((left+right)/2)
    if A['kind']=='annulus' and B['kind']=='annulus':
        d=float(np.linalg.norm(A['center']-B['center']))
        hit=d<A['outer']+B['outer'] and d+A['outer']>B['inner'] and d+B['outer']>A['inner']
        # Circle intersection is only used to display a point, never to decide reachability.
        ga=Point(A['center']).buffer(A['outer'],quad_segs=128).difference(Point(A['center']).buffer(A['inner'],quad_segs=128))
        gb=Point(B['center']).buffer(B['outer'],quad_segs=128).difference(Point(B['center']).buffer(B['inner'],quad_segs=128))
        inter=ga.intersection(gb)
        return hit,(inter.representative_point().coords[0] if not inter.is_empty else sample(B))
    if {A['kind'],B['kind']}=={'band','annulus'}:
        band,ring=(A,B) if A['kind']=='band' else (B,A);n=band['normal'];middle=float(ring['center']@n)
        offset=min(max(middle,band['lo']),band['hi'])-middle
        hit=abs(offset)<ring['outer']
        r=max(ring['inner'],abs(offset))+(ring['outer']-max(ring['inner'],abs(offset)))/2
        return hit,ring['center']+n*offset+np.array([-n[1],n[0]])*math.sqrt(max(0,r*r-offset*offset))
    def geometry(S,extent):
        if S['kind']=='poly':return S['poly'].buffer(S['padding'],quad_segs=64) if S['padding'] else S['poly']
        if S['kind']=='annulus':
            outer=Point(S['center']).buffer(S['outer'],quad_segs=128)
            return outer.difference(Point(S['center']).buffer(S['inner'],quad_segs=128)) if S['inner']>0 else outer
        n=S['normal'];u=np.array([-n[1],n[0]]);lo,hi=S['lo'],S['hi']
        return Polygon([n*lo-u*extent,n*hi-u*extent,n*hi+u*extent,n*lo+u*extent])
    if A['kind']=='annulus' and B['kind']=='poly' or B['kind']=='annulus' and A['kind']=='poly':
        ring,other=(A,B) if A['kind']=='annulus' else (B,A);poly=other['poly'];c=ring['center']
        minimum=max(0.,poly.distance(Point(c))-other['padding']);maximum=float(np.linalg.norm(np.asarray(poly.exterior.coords)-c,axis=1).max())+other['padding']
        hit=minimum<ring['outer'] and maximum>ring['inner']
        # The support test above is analytic; only the marker uses polygonized circles.
        inter=geometry(ring,1).intersection(poly)
        return hit,(inter.representative_point().coords[0] if not inter.is_empty else nearest_points(Point(c),poly)[1].coords[0])
    # Remaining case is band/polygon. Projection gives an analytic decision;
    # polygonized buffers below are used solely to pick a display marker.
    band,poly=(A,B) if A['kind']=='band' else (B,A)
    projection=np.asarray(poly['poly'].exterior.coords)@band['normal']
    lower=float(projection.min())-poly['padding'];upper=float(projection.max())+poly['padding']
    hit=upper>band['lo'] and lower<band['hi']
    # Rank-one translation can slide along a shared edge for a positive-length
    # interval. Vertex-only tangency has zero probability and remains excluded.
    if not hit and band['faces'] is not None:
        if upper==band['lo']:hit=band['faces'][0]+support_face_length(poly['poly'],band['normal'],True)>0
        elif lower==band['hi']:hit=band['faces'][1]+support_face_length(poly['poly'],band['normal'],False)>0
    extent=max(1.,np.linalg.norm(sample(A)),np.linalg.norm(sample(B)))
    for S in (A,B):
        if S['kind']=='poly':extent=max(extent,*map(abs,S['poly'].bounds))
        if S['kind']=='annulus':extent=max(extent,np.linalg.norm(S['center'])+S['outer'])
    ga,gb=geometry(A,extent*4+100),geometry(B,extent*4+100);inter=ga.intersection(gb)
    return hit,(inter.representative_point().coords[0] if not inter.is_empty else nearest_points(ga,gb)[1].coords[0])
