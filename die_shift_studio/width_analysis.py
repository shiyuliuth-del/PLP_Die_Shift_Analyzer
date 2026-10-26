"""Local routing-channel capacity at the displayed pose (all lengths in µm).

A cut joins two Pad/fixed-obstacle boundaries, or an obstacle and the package
edge. Required strands come from nominal copper crossings and cannot disappear
just because a moving Pad covers them. This is a necessary local width check
for the existing channel assignment, not a global autorouter or yield model.
"""
from dataclasses import dataclass
import math
import threading
import numpy as np
import shapely
from shapely.geometry import Polygon,LineString
from shapely.ops import nearest_points
from shapely.strtree import STRtree
from .session_cache import SessionCache
from .resources import parallel_map,resource_profile

COLORS={'充足':'#70af9c','临界':'#ddb15c','不足':'#d55e70','待确认':'#9499ae'}
RANK={'充足':0,'临界':1,'待确认':2,'不足':3}


def capacity(gap,width,space,strands):
    """n*w + (n+1)*s <= gap, including spacing at both channel walls."""
    if width<=0 or space<=0:raise ValueError('宽度分析的有效线宽、线距必须大于0')
    need=strands*width+(strands+1)*space if strands else 0.
    count=max(0,int(math.floor((gap-space+1e-9)/(width+space))))
    margin=gap-need
    status='不足' if count<strands else '临界' if count==strands else '充足'
    return dict(capacity=count,required_um=need,margin_um=margin,status=status,
                risk_ratio=need/gap if gap>0 else (math.inf if strands else 0.))


def line_intervals(geometry,cut,origin,direction,tol):
    out=[]
    pending=list(shapely.get_parts(geometry.intersection(cut)))
    while pending:
        part=pending.pop()
        if part.geom_type in ('GeometryCollection','MultiLineString'):pending.extend(part.geoms);continue
        if part.geom_type not in ('LineString','LinearRing') or part.length<=tol:continue
        t=(shapely.get_coordinates(part)-origin)@direction
        out.append((float(t.min()),float(t.max())))
    return sorted(out)


@dataclass
class Route:
    id: int
    refs: tuple
    net: int
    geometry: object
    via_connected: bool


@dataclass
class Obstacle:
    id: str
    kind: str
    refs: tuple
    net: int | None
    geometry: object
    node: int | None = None
    route: int | None = None
    hard: bool = True


class WidthAnalyzer:
    def __init__(self,model,parameters,settings,cache_group=None):
        self.model=model;self.p=parameters;self.settings=settings;self.board=model.case['board']
        self.tol=max(1e-9,model.data.dbu_um*1e-5);self.inside=self.board.buffer(self.tol);self.planes={};self.cache=SessionCache(group=cache_group)
        con=model.connections;nodes=model.nodes;roles=model.roles
        for plane,ids in model.planes.items():
            route_ids=[i for i in ids if roles[nodes[i]['key']] in ('运动RDL','固定RDL')]
            if not route_ids:continue
            kinds={roles[nodes[i]['key']] for i in route_ids};tau=settings.drc_tolerance_um
            width=max(parameters.wafer_line_width_um if k=='运动RDL' else parameters.line_width_um for k in kinds)-tau
            space=max(parameters.wafer_line_space_um if k=='运动RDL' else parameters.line_space_um for k in kinds)-tau
            if min(width,space)<=0:raise ValueError('宽度分析：容差须小于运动/固定线宽及线距')
            grouped={}
            for i in route_ids:
                node=nodes[i];grouped.setdefault(con.component(node['key'],node['index']),[]).append(i)
            routes=[];obstacles=[]
            # All via features in an adjacent physical group reserve a landing,
            # even if a broken nominal connection is separately diagnosed.
            group_index=next(i for i,g in enumerate(con.groups) if g[0].split('/')[0]==plane)
            adjacent=[k for j in (group_index-1,group_index+1) if 0<=j<len(con.groups) for k in con.groups[j]]
            for i,node in enumerate(nodes):
                if node['key'] not in adjacent or roles[node['key']] not in ('运动过孔','固定过孔'):continue
                g=node['poly'].buffer(parameters.annulus_um,quad_segs=16) if parameters.annulus_um else node['poly']
                obstacles.append(Obstacle(f"pad:{node['key']}:{node['index']}",'Pad',((node['key'],node['index']),),node['net'],g,node=i))
            via_nets={o.net for o in obstacles}
            for group in grouped.values():
                refs=tuple((nodes[i]['key'],nodes[i]['index']) for i in group)
                geometry=shapely.union_all([nodes[i]['poly'] for i in group]);net=nodes[group[0]]['net']
                route=Route(len(routes),refs,net,geometry,net in via_nets);routes.append(route)
                # Large standalone fixed copper is an obstacle, not an invented
                # wire. Thin unlabelled copper remains a trace to be counted.
                all_fixed=all(roles[k]=='固定RDL' for k,_ in refs)
                broad=not geometry.buffer(-1.5*width).is_empty
                if all_fixed:
                    obstacles.append(Obstacle('fixed:'+str(route.id),'固定图形',refs,net,geometry,route=route.id,hard=not route.via_connected and broad))
            for key,role in roles.items():
                if role=='禁布区':
                    for i,g in enumerate(model.cache.layers[key]['geometry']):obstacles.append(Obstacle(f'keepout:{key}:{i}','禁布区',((key,i),),None,g))
            edge_index=0
            for ring in (self.board.exterior,*self.board.interiors):
                xy=shapely.get_coordinates(ring)
                for a,b in zip(xy,xy[1:]):
                    if np.linalg.norm(b-a)<=self.tol:continue
                    obstacles.append(Obstacle(f'edge:{edge_index}','边框',tuple((k,i) for k,r in roles.items() if r=='封装边框' for i in range(len(model.cache.layers[k]['geometry']))),None,LineString([a,b])));edge_index+=1
            fixed_ids={o.route for o in obstacles if o.route is not None and o.hard}
            net_routes={}
            for route in routes:net_routes.setdefault(route.net,[]).append(route.id)
            self.planes[plane]=dict(routes=routes,obstacles=obstacles,route_ids={r.id for r in routes if r.id not in fixed_ids},
                width=width,space=space,keys=sorted({nodes[i]['key'] for i in route_ids}),mixed_rules=len(kinds)>1,net_routes=net_routes,
                templates=None,candidate_count=0)

    def close(self):self.cache.close()

    def _routes(self,plane,frame):
        rows=plane['routes'];layers=frame['layers'];replacements=frame.get('deformation',{}).get('replacements',{})
        replaced={}
        for key,items in replacements.items():
            for q in items:
                token=(key,tuple(q['indices']))
                for i in q['indices']:replaced[key,i]=(token,q['geometry'])
        geometries=[]
        for r in rows:
            pieces=[];used=set()
            for key,i in r.refs:
                if (key,i) in replaced:
                    token,g=replaced[key,i]
                    if token not in used:pieces.append(g);used.add(token)
                else:pieces.append(Polygon(layers[key]['polygons'][i]))
            geometries.append(shapely.union_all(pieces))
        return geometries

    def _obstacles(self,plane,frame,routes):
        states=frame['states'];out=[]
        for o in plane['obstacles']:
            if o.node is not None:
                node=self.model.nodes[o.node]
                out.append(self.model.moved(o.geometry,node['owner'],states))
            elif o.route is not None:out.append(routes[o.route])
            else:out.append(o.geometry)
        return out

    def _cut(self,a,b,routes,plane,obstacles,central=False,raw_line=None):
        if raw_line is not None:line=raw_line
        elif central:
            axis=LineString([a.representative_point(),b.representative_point()])
            line=shapely.shortest_line(a.intersection(axis),b.intersection(axis))
        else:line=shapely.shortest_line(a,b)
        xy=shapely.get_coordinates(line)
        if len(xy)<2:return None
        start,end=xy[0],xy[-1];gap=float(np.linalg.norm(end-start))
        if gap<=self.tol:return None
        direction=(end-start)/gap
        # Retain actual endpoint landing copper, including larger-than-minimum
        # pads in the GDS. Do not trim a separated middle strand of the same net.
        trim_left=0.;trim_right=gap
        left,right=obstacles
        for rid in set(plane['net_routes'].get(left.net,[])+plane['net_routes'].get(right.net,[])):
            r=plane['routes'][rid];g=routes[rid]
            intervals=line_intervals(g,line,start,direction,self.tol)
            if r.net==left.net:
                for lo,hi in intervals:
                    if lo<=trim_left+self.tol:trim_left=max(trim_left,hi)
            if r.net==right.net:
                for lo,hi in reversed(intervals):
                    if hi>=trim_right-self.tol:trim_right=min(trim_right,lo)
        start=start+direction*trim_left;end=xy[-1]-direction*(gap-trim_right)
        gap=max(0.,trim_right-trim_left)
        return start,end,direction,gap,LineString([start,start+direction*gap])

    def _strands_many(self,cuts,plane,routes,tree):
        """Batch GEOS intersections/probes; no Python call for each wire edge."""
        out=[{} for _ in cuts]
        if not cuts:return out
        lines=np.asarray([q[4] for q in cuts],object)
        ci,ri=tree.query(lines,predicate='intersects')
        mask=np.isin(ri,list(plane['route_ids']));ci,ri=ci[mask],ri[mask]
        if not len(ci):return out
        pieces=shapely.intersection(np.asarray(routes,object)[ri],lines[ci])
        parts,source=shapely.get_parts(pieces,return_index=True)
        valid=np.isin(shapely.get_type_id(parts),[1,2])&(shapely.length(parts)>self.tol)
        parts,source=parts[valid],source[valid]
        if not len(parts):return out
        cx,rx=ci[source],ri[source]
        starts=np.asarray([q[0] for q in cuts]);directions=np.asarray([q[2] for q in cuts]);gaps=np.asarray([q[3] for q in cuts])
        xy,pi=shapely.get_coordinates(parts,return_index=True)
        distance=np.einsum('ij,ij->i',xy-starts[cx[pi]],directions[cx[pi]])
        lo=np.full(len(parts),np.inf);hi=np.full(len(parts),-np.inf)
        np.minimum.at(lo,pi,distance);np.maximum.at(hi,pi,distance)
        eps=max(self.tol,plane['width']*.02)
        inside=(lo>eps)&(hi<gaps[cx]-eps)
        lo,hi,cx,rx=lo[inside],hi[inside],cx[inside],rx[inside]
        if not len(cx):return out
        direction=directions[cx];normal=np.column_stack((-direction[:,1],direction[:,0]))
        mid=starts[cx]+((lo+hi)/2)[:,None]*direction
        probe=np.minimum(plane['width']*.1,np.maximum(eps,(hi-lo)*.1))[:,None]
        front=mid+normal*probe;back=mid-normal*probe;objects=np.asarray(routes,object)[rx]
        crosses=shapely.contains_xy(objects,front[:,0],front[:,1])&shapely.contains_xy(objects,back[:,0],back[:,1])
        for c,r in zip(cx[crosses],rx[crosses]):out[int(c)][int(r)]=out[int(c)].get(int(r),0)+1
        return out

    def _strands(self,cut,plane,routes,tree):return self._strands_many([cut],plane,routes,tree)[0]

    def _discover(self,plane,geoms,routes,progress):
        obstacles=plane['obstacles'];hard_ids=np.asarray([i for i,o in enumerate(obstacles) if o.hard],int)
        tree=STRtree([geoms[i] for i in hard_ids]);rtree=STRtree(routes);templates=[]
        shapely.prepare(np.asarray(routes,object))
        # Exhaustive pairs in bounded chunks; also rediscovered at the current
        # pose so a newly opened channel cannot be omitted by a nominal cache.
        total=len(obstacles)*(len(obstacles)-1)//2;done=0
        representatives=shapely.point_on_surface(np.asarray(geoms,object))
        rep_xy=shapely.get_coordinates(representatives)
        for i,left in enumerate(obstacles):
            targets=list(range(i+1,len(obstacles)))
            for offset in range(0,len(targets),192):
                js=targets[offset:offset+192];targets_geom=np.asarray([geoms[j] for j in js],object)
                lines=shapely.shortest_line(geoms[i],targets_geom)
                blocked=set();pairs=tree.query(lines,predicate='intersects')
                for row,k in zip(pairs[0],hard_ids[pairs[1]]):
                    if int(k) not in (i,js[row]) and obstacles[int(k)].hard:blocked.add(int(row))
                usable=(shapely.length(lines)>self.tol)&shapely.covers(self.inside,lines)
                candidates=[];cuts=[]
                for row,j in enumerate(js):
                    if row in blocked or not usable[row]:continue
                    cut=self._cut(geoms[i],geoms[j],routes,plane,(left,obstacles[j]),raw_line=lines[row])
                    if cut is not None:candidates.append((j,False));cuts.append(cut)
                central_rows=[r for r,j in enumerate(js) if left.kind=='固定图形' or obstacles[j].kind=='固定图形']
                if central_rows:
                    cj=np.asarray(js)[central_rows]
                    axes=shapely.linestrings(np.stack((np.repeat(rep_xy[i:i+1],len(cj),axis=0),rep_xy[cj]),axis=1))
                    center_lines=shapely.shortest_line(shapely.intersection(geoms[i],axes),shapely.intersection(np.asarray(geoms,object)[cj],axes))
                    bad=set();pairs=tree.query(center_lines,predicate='intersects')
                    for row,k in zip(pairs[0],hard_ids[pairs[1]]):
                        if int(k) not in (i,cj[row]) and obstacles[int(k)].hard:bad.add(int(row))
                    usable=(shapely.length(center_lines)>self.tol)&shapely.covers(self.inside,center_lines)
                    for row,j in enumerate(cj):
                        if row in bad or not usable[row]:continue
                        cut=self._cut(geoms[i],geoms[j],routes,plane,(left,obstacles[j]),raw_line=center_lines[row])
                        if cut is not None:candidates.append((int(j),True));cuts.append(cut)
                strands=self._strands_many(cuts,plane,routes,rtree)
                seen={}
                for (j,central),cut,counts in zip(candidates,cuts,strands):
                    if not counts:continue
                    # Equivalent central/shortest sections need only one row.
                    equivalent=(j,tuple(sorted(counts.items())),tuple(np.round(np.r_[cut[0],cut[1]],6)))
                    if equivalent in seen:continue
                    seen[equivalent]=True
                    templates.append(dict(pair=(i,j),central=central,nominal=counts,nominal_cut=cut[:4],cut=cut))
                done+=len(js);progress(round(100*done/max(1,total)),f'识别拓扑通道 {done:,}/{total:,}')
        return templates,total

    def _templates(self,plane,progress):
        if plane['templates'] is None:
            plane['templates'],plane['candidate_count']=self._discover(plane,[o.geometry for o in plane['obstacles']],[r.geometry for r in plane['routes']],progress)
        return plane['templates']

    def frame(self,frame,progress=lambda *_:None,quick=False):
        replacements=frame.get('deformation',{}).get('replacements',{})
        fingerprint=tuple((k,tuple(v['indices']),v['geometry'].wkb) for k,items in sorted(replacements.items()) for v in items)
        quick=bool(quick and np.any(frame['states']) and all('tracked' in p for p in self.planes.values()))
        key=(frame['states'].tobytes(),fingerprint,quick)
        hit=self.cache.get(key)
        if hit is not None:return hit
        unknown={(q['layer'],q['index']-1) for q in frame.get('motion_issues',[])}
        partial=np.zeros(len(self.planes));progress_lock=threading.Lock()
        def analyze_plane(item):
            pi,(name,plane)=item;rows=[];highlights={};total_templates=0;candidate_count=0
            def record(n,t):
                with progress_lock:
                    partial[pi]=max(partial[pi],n);progress(round(float(np.mean(partial))),f'{name}/ · '+t)
            templates=self._templates(plane,lambda n,t:record(n*.45,t))
            candidate_count+=plane['candidate_count']
            routes=self._routes(plane,frame);obstacles=self._obstacles(plane,frame,routes);hard_ids=np.asarray([i for i,o in enumerate(plane['obstacles']) if o.hard],int);otree=STRtree([obstacles[i] for i in hard_ids])
            nominal=not np.any(frame['states']) and not fingerprint
            if quick:
                templates=plane['tracked'];current=[];cuts=[];chosen=[]
                for q in templates:
                    i,j=q['pair'];cut=self._cut(obstacles[i],obstacles[j],routes,plane,(plane['obstacles'][i],plane['obstacles'][j]),q.get('central',False))
                    if cut is not None:cuts.append(cut);chosen.append(q)
                for q,cut,counts in zip(chosen,cuts,self._strands_many(cuts,plane,routes,STRtree(routes))):current.append(dict(q,cut=cut,nominal=counts))
            elif nominal:current=templates
            else:current,_=self._discover(plane,obstacles,routes,lambda n,t:record(45+n*.45,'当前姿态 '+t))
            current_map={(q['pair'],q['central']):q for q in current}
            all_templates={(q['pair'],q['central']):q for q in templates}
            all_templates.update({k:dict(q,nominal={}) for k,q in current_map.items() if k not in all_templates})
            # A current blocker hides a cut from discovery, but it does not
            # erase either old demand OR new strands that moved into that cut.
            missing=[];missing_cuts=[]
            for token,q in all_templates.items():
                if token in current_map:continue
                i,j=q['pair'];cut=self._cut(obstacles[i],obstacles[j],routes,plane,(plane['obstacles'][i],plane['obstacles'][j]),q.get('central',False))
                if cut is not None:missing.append((token,q));missing_cuts.append(cut)
            for (token,q),cut,counts in zip(missing,missing_cuts,self._strands_many(missing_cuts,plane,routes,STRtree(routes))):
                current_map[token]=dict(q,cut=cut,nominal=counts)
            total_templates+=len(all_templates)
            tracked=[]
            for ti,(token,template) in enumerate(all_templates.items()):
                if ti%64==0:record(90+10*ti/max(1,len(all_templates)),f'通道容量 {ti+1}/{len(all_templates)}')
                i,j=template['pair'];a,b=plane['obstacles'][i],plane['obstacles'][j];now=current_map.get(token)
                cut=now['cut'] if now else self._cut(obstacles[i],obstacles[j],routes,plane,(a,b),template.get('central',False))
                if cut is None:
                    point=np.array(nearest_points(obstacles[i],obstacles[j])[0].coords[0]);direction=template['nominal_cut'][2];cut=(point,point,direction,0.,LineString([point,point]))
                start,end,direction,gap,line=cut
                strands=dict(template['nominal'])
                for rid,n in (now['nominal'] if now else {}).items():strands[rid]=max(strands.get(rid,0),n)
                count=sum(strands.values());result=capacity(gap,plane['width'],plane['space'],count)
                reason='两侧保留线距；保持名义通道的线路分配'
                others=[int(k) for k in hard_ids[otree.query(line,predicate='intersects')] if int(k) not in (i,j) and plane['obstacles'][int(k)].hard] if gap>self.tol else []
                if others:
                    # A newly intruding obstacle splits the original channel.
                    # Count a physical upper bound for the remaining slots, but
                    # never certify a new homotopy assignment from that count.
                    free=line.difference(shapely.union_all([obstacles[k] for k in others]))
                    slots=[g.length for g in shapely.get_parts(free) if g.geom_type=='LineString']
                    result['capacity']=sum(capacity(g,plane['width'],plane['space'],0)['capacity'] for g in slots)
                    result['status']='不足' if result['capacity']<count else '待确认'
                    result['available_slots_um']=slots;result['margin_um']=None
                    reason='其他Pad/固定图形进入通道；分段容量已计算，绕行拓扑需复核'
                if not self.inside.covers(line):result.update(status='不足',capacity=0);reason='原通道超出封装边界'
                affected=tuple(sorted({ref for rid in strands for ref in plane['routes'][rid].refs}))
                if unknown.intersection((*a.refs,*b.refs,*affected,*[ref for k in others for ref in plane['obstacles'][k].refs])):
                    result['status']='待确认';reason='相关运动图形归属未解析，不能确认偏移后容量'
                # Only tight sections are painted. All examined sections remain
                # in the result list for audit; no numerical test is viewport-only.
                draw=result['capacity']<=count+2 or result['status']=='不足' or bool(unknown.intersection((*a.refs,*b.refs,*affected)))
                middle=(start+end)/2;normal=np.array([-direction[1],direction[0]])
                length=max(gap,plane['width']*2);depth=4*(plane['width']+plane['space'])
                rectangle=Polygon([middle+direction*x+normal*y for x,y in ((-length/2,-depth/2),(length/2,-depth/2),(length/2,depth/2),(-length/2,depth/2))])
                entry=dict(plane=name,layers=plane['keys'],pair=(a.id,b.id),ends=(a.kind,b.kind),gap_um=gap,width_um=plane['width'],space_um=plane['space'],strands=count,
                    nominal_strands=sum(template['nominal'].values()),refs=affected,boundary_refs=tuple(sorted(set(a.refs+b.refs+tuple(ref for k in others for ref in plane['obstacles'][k].refs)))),rectangle=rectangle,point=middle.tolist(),draw=draw,detail=reason,mixed_rules=plane['mixed_rules'],**result)
                rows.append(entry)
                if draw:tracked.append(dict(template,nominal=strands))
                if result['status']!='充足':
                    for rid in strands:
                        route=plane['routes'][rid];token=(name,route.refs)
                        old=highlights.get(token)
                        if old is None or RANK[result['status']]>RANK[old['status']]:highlights[token]=dict(layers=sorted({k for k,_ in route.refs}),refs=route.refs,geometry=routes[rid],status=result['status'])
            if not quick:plane['tracked']=tracked
            record(100,'通道检查完成')
            return rows,highlights,total_templates,candidate_count
        rows=[];highlights={};total_templates=0;candidate_count=0
        profile=resource_profile()
        # Independent physical planes own separate arrays and topology caches.
        # Bound the pool using the same per-job hardware budget as preview.
        for rr,hh,tt,cc in parallel_map(analyze_plane,list(enumerate(self.planes.items())),profile):
            rows.extend(rr);highlights.update(hh);total_templates+=tt;candidate_count+=cc
        rows.sort(key=lambda q:(-RANK[q['status']],q['margin_um'] if q['margin_um'] is not None else -math.inf,q['plane'],q['pair']))
        result=dict(complete=not quick,rows=rows,highlights=list(highlights.values()),candidate_pairs=candidate_count,topology_sections=total_templates,
            counts={s:sum(q['status']==s for q in rows) for s in COLORS},scope='当前姿态下保持现有通道分配的局部宽度必要条件；通过不代表整图可布通或完整DRC通过。',states=frame['states'].copy())
        if quick:result['scope']='动画中仅跟踪最近完整状态已定位的窄口，终态重新全量检查；未证明整图通过。'
        if not np.any(frame['states']) and not fingerprint:self.cache.pin(key)
        self.cache[key]=result;progress(100,'宽度分析完成');return result
