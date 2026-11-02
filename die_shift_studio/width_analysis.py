"""Landing air-gap at the displayed pose: two readings of the same landing.

The shift carries a via along with its die, so the copper that lands on that via
moves too. Two different things can then go wrong, and the mode picks which one
is measured:

``vertical``
    The shifted landing against **other nets' copper on the vertically adjacent
    fixed RDL layer** — a shifted via can short to a fixed RDL trace it was never
    meant to touch.

``landing``
    The shifted landing against the **unshifted original landing regions of other
    vias** — the space the original layout reserved for other pads. Comparing with
    the nominal footprints shows intrusion even before anything is re-routed.

In both modes the landing is the via grown by the overlap amount (a circular via
of radius r becomes r+overlap), and the air gap is the shortest distance from
that landing to a landing/trace of a *different* net. Only targets inside a local
search radius R are considered, which keeps the check local; R is therefore a
stated limit, not a hidden one. The same net is never a risk — it is meant to be
touching — and in ``landing`` mode the via's own nominal footprint is excluded,
since measuring a landing against its own original would only restate the offset.

This module supersedes the earlier channel-capacity reading: there is no strand
counting, no cut catalogue and no route assignment left here.
"""
import math
import threading
import numpy as np
import shapely
from shapely.strtree import STRtree
from .session_cache import SessionCache
from .resources import parallel_map,resource_profile

COLORS={'充足':'#70af9c','临界':'#ddb15c','不足':'#d55e70','待确认':'#9499ae'}
RANK={'充足':0,'临界':1,'待确认':2,'不足':3}
STATUSES=tuple(COLORS)
CHUNK=256
MODES=('vertical','landing')
MODE_LABELS={'vertical':'垂直方向固定RDL','landing':'其他落盘区原始位置'}
TARGET_LABELS={'vertical':'最近异网固定RDL','landing':'最近异网原始落盘'}
TARGET_NOUN={'vertical':'其他网络的固定RDL铜','landing':'其他网络的落盘区'}

SCOPE={'vertical':('以每个运动过孔为对象：过孔按 overlap 外扩后的落盘区，到垂直方向固定RDL层上**其他网络**铜的最短距离为 air-gap。'
                   '只在局部半径 R 内查找，R 之外的铜未参与测量；air-gap 通过不代表整图可布通、完整DRC通过或量产良率。'),
       'landing':('以每个运动过孔为对象：过孔按 overlap 外扩后的落盘区，到附近**其他过孔未偏移的原始落盘区**（按同一 overlap 定义）的最短距离为 air-gap。'
                  '只比较不同网络的落盘，并排除该孔自身原位落盘；只在局部半径 R 内查找，R 之外未参与测量；'
                  '这是一个与原始版图预留空间比较的局部必要条件，不代表整图可布通或量产良率。')}


def classify(gap_um,rule_space_um,threshold_um):
    """A rule violation first, then the user's annotation threshold."""
    if gap_um is None:return '充足'
    if gap_um<rule_space_um:return '不足'
    if gap_um<threshold_um:return '临界'
    return '充足'


class WidthAnalyzer:
    def __init__(self,model,parameters,settings,cache_group=None):
        from .drc_rules import effective_rules
        self.model=model;self.p=parameters;self.settings=settings;self.cache=SessionCache(group=cache_group)
        self.tol=max(1e-9,model.data.dbu_um)
        self.rule_width,self.rule_space=effective_rules(parameters,settings)
        self.overlap_um=max(0.,float(getattr(settings,'width_overlap_um',0.)))
        self.radius_um=max(self.tol,float(getattr(settings,'width_search_radius_um',100.)))
        requested=float(getattr(settings,'width_gap_threshold_um',0.))
        self.threshold_um=requested if requested>0 else self.rule_space
        self.mode=str(getattr(settings,'width_mode','vertical'))
        if self.mode not in MODES:raise ValueError('无效的宽度分析对象：'+self.mode)
        roles=model.roles;groups=model.connections.groups
        index_of={}
        for i,group in enumerate(groups):
            for key in group:index_of.setdefault(key,i)
        def pool(kind,geoms,nets,ids):
            return dict(kind=kind,geoms=geoms,nets=nets,ids=ids,tree=STRtree(geoms) if len(geoms) else None)
        self.pools={}
        if self.mode=='vertical':
            # Fixed RDL copper is the only thing measured against, per layer.
            for key,role in roles.items():
                if role!='固定RDL':continue
                geoms=np.asarray(model.cache.layers[key]['geometry'],object)
                if not len(geoms):continue
                nets=np.array([model.nodes[model.ids[key,i]]['net'] for i in range(len(geoms))],int)
                self.pools[key]=pool('fixed',geoms,nets,np.arange(len(geoms)))
        self.vias=[]
        for key,role in roles.items():
            if role!='运动过孔':continue
            position=index_of.get(key);adjacent=[]
            if position is not None:
                for j in (position-1,position+1):
                    if 0<=j<len(groups):
                        adjacent.extend(k for k in groups[j] if roles.get(k)=='固定RDL' and k in self.pools)
            adjacent=sorted(set(adjacent))
            landed=[]
            for index in range(len(model.cache.layers[key]['geometry'])):
                node=model.nodes[model.ids[key,index]]
                if self.mode=='landing':
                    # Every via's own landing is the target the others are compared to.
                    landed.append(node['poly'].buffer(self.overlap_um,quad_segs=32)
                                  if self.overlap_um>self.tol else node['poly'])
                targets=set(adjacent)
                if self.mode=='vertical':
                    # A nominal interface is a vertical neighbour even when the
                    # layer order alone would not make it adjacent.
                    for target,_ in model.connections.fixed_interfaces(key,index):
                        if roles.get(target)=='固定RDL' and target in self.pools:targets.add(target)
                else:
                    targets={key}
                self.vias.append(dict(key=key,index=index,owner=int(node['owner']),net=int(node['net']),
                                      poly=node['poly'],targets=tuple(sorted(targets)),
                                      self_pool=key if self.mode=='landing' else None,self_index=index))
            if self.mode=='landing' and landed:
                self.pools[key]=pool('via',np.asarray(landed,object),
                                     np.array([v['net'] for v in self.vias if v['key']==key],int),
                                     np.array([v['index'] for v in self.vias if v['key']==key],int))
        self.tracked=None

    def close(self):self.cache.close()

    def _copper(self,pool,layer,index,replaced):
        # Accepted deformation moves fixed copper; original landings stay nominal.
        if pool['kind']=='fixed':
            return replaced.get((layer,index),pool['geoms'][index])
        return pool['geoms'][index]

    def _land(self,via,states):
        moved=self.model.moved(via['poly'],via['owner'],states)
        land=moved.buffer(self.overlap_um,quad_segs=32) if self.overlap_um>self.tol else moved
        return moved,land

    def _measure(self,via,states,replaced):
        """Nearest other-net target to this via's landing, inside R."""
        moved,land=self._land(via,states)
        best=None
        for layer in via['targets']:
            entry=self.pools.get(layer)
            if entry is None or entry['tree'] is None:continue
            near=entry['tree'].query(land,predicate='dwithin',distance=self.radius_um)
            if not len(near):continue
            near=np.asarray(near,int)
            if via['self_pool']==layer:near=near[near!=via['self_index']]
            near=near[entry['nets'][near]!=via['net']]
            if not len(near):continue
            geoms=np.asarray([self._copper(entry,layer,int(j),replaced) for j in near],object)
            distances=shapely.distance(land,geoms)
            pick=int(np.argmin(distances))
            if best is None or float(distances[pick])<best[0]:
                best=(float(distances[pick]),layer,int(near[pick]),geoms[pick])
        return moved,land,best

    def _target(self,pool,layer,index):
        return dict(layer=layer,index=int(index),net=int(pool['nets'][index]),kind=pool['kind'])

    def _row(self,via,states,replaced,unknown):
        moved,land,best=self._measure(via,states,replaced)
        point=[float(v) for v in moved.centroid.coords[0]]
        land_radius=float(math.sqrt(max(0.,land.area)/math.pi))
        layers=(via['key'],)+tuple(via['targets']) if self.mode=='vertical' else (via['key'],)
        unresolved=(via['key'],via['index']) in unknown
        if best is None:
            detail=(f'半径 {self.radius_um:g} μm 内没有{TARGET_NOUN[self.mode]}；'
                    f'落盘区等效半径 {land_radius:.3f} μm（过孔外扩 overlap {self.overlap_um:g} μm）')
            row=dict(status='待确认' if unresolved else '充足',key=via['key'],index=via['index'],
                     via_index=via['index']+1,owner=via['owner'],net=via['net'],layers=layers,
                     plane=via['key'],point=point,land=land,land_radius_um=land_radius,gap_um=None,
                     target=None,link=None,draw=False,
                     detail=detail+f'；R={self.radius_um:g} μm 之外未检查')
            return row,False
        gap,layer,index,geometry=best
        line=shapely.shortest_line(land,geometry)
        xy=shapely.get_coordinates(line)
        link=None
        if len(xy)>=2:link=[[float(xy[0][0]),float(xy[0][1])],[float(xy[-1][0]),float(xy[-1][1])]]
        status='待确认' if unresolved else classify(gap,self.rule_space,self.threshold_um)
        pool=self.pools[layer]
        reference=('固定RDL' if pool['kind']=='fixed' else '过孔原始落盘')
        detail=(f'落盘区等效半径 {land_radius:.3f} μm（过孔外扩 overlap {self.overlap_um:g} μm）；'
                f'{TARGET_LABELS[self.mode]}：{layer} #{int(index)+1}（{reference}，net {int(pool["nets"][index])}）；'
                f'air-gap {gap:.3f} μm（有效线距 {self.rule_space:g} μm，标注阈值 {self.threshold_um:g} μm）')
        if unresolved:detail+='；该运动过孔归属未解析，按名义位置测量，不能据此确认'
        row=dict(status=status,key=via['key'],index=via['index'],via_index=via['index']+1,
                 owner=via['owner'],net=via['net'],layers=layers,plane=layer,point=point,land=land,
                 land_radius_um=land_radius,gap_um=gap,rule_space_um=self.rule_space,
                 threshold_um=self.threshold_um,target=self._target(pool,layer,index),
                 link=link,draw=status!='充足',detail=detail)
        return row,status!='充足'

    def frame(self,frame,progress=lambda *_:None,quick=False):
        states=frame['states']
        replacements=frame.get('deformation',{}).get('replacements',{})
        reconstructed=(frame.get('reconstruction') or {}).get('replacements',{})
        replaced={}
        for key,items in replacements.items():
            for q in items:
                for i in q['indices']:replaced[key,i]=q['geometry']
        # A reconstruction supersedes deformation for the copper it re-cut.
        for key,items in reconstructed.items():
            for q in items:
                for i in q['indices']:replaced[key,i]=q['geometry']
        fingerprint=tuple((k,tuple(v['indices']),v['geometry'].wkb)
                          for source in (replacements,reconstructed)
                          for k,items in sorted(source.items()) for v in items)
        quick=bool(quick and np.any(states) and self.tracked is not None)
        key=(states.tobytes(),fingerprint,quick)
        hit=self.cache.get(key)
        if hit is not None:return hit
        unknown={(q['layer'],q['index']-1) for q in frame.get('motion_issues',[])}
        if quick:
            chosen=[(i,self.vias[i]) for i in self.tracked]
        else:
            chosen=list(enumerate(self.vias))
        total=len(chosen);done=[0];lock=threading.Lock()
        chunks=[chosen[i:i+CHUNK] for i in range(0,total,CHUNK)] or [[]]
        progress(0,f'落盘air-gap：{total} 个运动过孔' if not quick else '动画：更新已定位的风险落盘')

        def run(chunk):
            rows=[];tracked=[];beyond=0
            for position,via in chunk:
                row,risky=self._row(via,states,replaced,unknown)
                if row['gap_um'] is None:beyond+=1
                if risky:tracked.append(position)
                rows.append(row)
            with lock:
                done[0]+=len(chunk)
                progress(round(100*done[0]/max(1,total)),f'落盘air-gap {done[0]}/{total}')
            return rows,tracked,beyond

        rows=[];tracked=[];beyond=0
        for part in parallel_map(run,chunks,resource_profile()):
            rows.extend(part[0]);tracked.extend(part[1]);beyond+=part[2]
        rows.sort(key=lambda q:(-RANK[q['status']],q['gap_um'] if q['gap_um'] is not None else math.inf,
                                q['key'],q['index']))
        result=dict(complete=not quick,rows=rows,counts={s:sum(q['status']==s for q in rows) for s in STATUSES},
                    examined=len(chosen),total_vias=len(self.vias),beyond_radius=beyond,
                    mode=self.mode,mode_label=MODE_LABELS[self.mode],target_label=TARGET_LABELS[self.mode],
                    parameters=dict(overlap_um=self.overlap_um,radius_um=self.radius_um,
                                    threshold_um=self.threshold_um,rule_space_um=self.rule_space,
                                    rule_width_um=self.rule_width),
                    scope=SCOPE[self.mode],states=states.copy())
        if quick:
            result['scope']='动画中只按当前几何更新上次已定位的风险落盘；终态重新全量测量；未证明整图通过。'
        if not quick:self.tracked=tracked
        if not np.any(states) and not fingerprint:self.cache.pin(key)
        self.cache[key]=result;progress(100,'落盘air-gap完成')
        return result
