"""Metal adjacency, downward ownership tracing and real moving/fixed interfaces."""
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from .resources import parallel_map
from .layer_roles import PHYSICAL,MOVING,canonical_roles

class UnionFind:
    def __init__(self,n):self.parent=list(range(n))
    def find(self,i):
        while self.parent[i]!=i:self.parent[i]=self.parent[self.parent[i]];i=self.parent[i]
        return i
    def union(self,i,j):
        i,j=self.find(i),self.find(j)
        if i!=j:self.parent[j]=i


def stack_order(data,roles,saved=None):
    # Persist the exact user sequence, including non-metal rows between metals.
    # An imported file without a saved order starts in the reader's encounter order.
    return list(dict.fromkeys([k for k in (saved or []) if k in data.layers]+list(data.layers)))

def resolve_stages(data,basic_roles,order,hints=None):
    # Compatibility entry point: fabrication stages are no longer public roles.
    return canonical_roles(basic_roles)

class Connections:
    def __init__(self,data,roles,order=None,geometries=None):
        from .geometry_errors import sanitize_data
        sanitize_data(data);self.data=data;self.roles=canonical_roles(roles)
        self.order=stack_order(data,self.roles,order);self.links=[]
        self.outgoing={k:{} for k in data.layers};self.incoming={k:{} for k in data.layers}
        self.geometry=geometries if geometries is not None else {k:np.array([Polygon(a) for a in v],object) for k,v in data.layers.items()}
        planes={}
        for key in self.order:
            if self.roles.get(key) in PHYSICAL:planes.setdefault(key.split('/')[0],[]).append(key)
        self.empty_metal_layers=[k for group in planes.values() for k in group if len(self.geometry[k])==0]
        self.groups=[group for group in planes.values() if any(len(self.geometry[k]) for k in group)];self.ids={};self.nodes=[];self.plane_nodes=[]
        for group in self.groups:
            ids=[]
            for key in group:
                for i,p in enumerate(self.geometry[key]):
                    node=len(self.nodes);self.ids[key,i]=node;ids.append(node);self.nodes.append((key,i,p))
            self.plane_nodes.append(ids)
        self.same_plane=UnionFind(len(self.nodes))
        for ids in self.plane_nodes:
            polygons=np.array([self.nodes[i][2] for i in ids],object);tree=STRtree(polygons)
            for start in range(0,len(ids),512):
                a,b=tree.query(polygons[start:start+512],predicate='intersects')
                for i,j in zip(a+start,b):
                    if i<j:self.same_plane.union(ids[i],ids[j])
        self.components={};self._component_geometry={}
        for i in range(len(self.nodes)):self.components.setdefault(self.same_plane.find(i),[]).append(i)
        def between(pair):
            up,down=pair
            lower=[(k,i,g) for k in down for i,g in enumerate(self.geometry[k])]
            if not lower:return []
            lp=np.array([q[2] for q in lower],object);tree=STRtree(lp);found=[]
            for uk in up:
                upper=self.geometry[uk]
                for start in range(0,len(upper),512):
                    pairs=tree.query(upper[start:start+512],predicate='intersects')
                    if not pairs.size:continue
                    ui=pairs[0]+start;li=pairs[1]
                    valid=shapely.area(shapely.intersection(upper[ui],lp[li]))>max(1e-12,data.dbu_um**2*.01)
                    for j in np.flatnonzero(valid):
                        lk,index,_=lower[li[j]]
                        found.append(dict(upper=uk,upper_index=int(ui[j]),lower=lk,lower_index=index))
            return found
        for links in parallel_map(between,list(zip(self.groups,self.groups[1:]))):self.links.extend(links)
        # Moving/fixed via drawings can occupy consecutive functional rows at
        # disjoint XY positions. An empty local via row must not disconnect a
        # fixed landing from the nearest actual RDL plane on the other side.
        self.inferred_interfaces=[]
        known={(q['upper'],q['upper_index'],q['lower'],q['lower_index']) for q in self.links}
        via_roles={'运动过孔','固定过孔'};rdl_roles={'运动RDL','固定RDL'}
        eps=max(1e-12,data.dbu_um**2*.01)
        def overlaps(key,geometry):
            tree=self._layer_tree(key)
            if tree is None:return []
            return [int(i) for i in tree.query(geometry,predicate='intersects')
                    if geometry.intersection(self.geometry[key][int(i)]).area>eps]
        for rank,group in enumerate(self.groups):
            for key in group:
                if self.roles[key]!='固定过孔':continue
                for index,geometry in enumerate(self.geometry[key]):
                    for direction in (-1,1):
                        at=rank+direction
                        while 0<=at<len(self.groups):
                            other_group=self.groups[at];rdls=[k for k in other_group if self.roles[k] in rdl_roles]
                            if rdls:
                                for other in rdls:
                                    for j in overlaps(other,geometry):
                                        ref=(other,j,key,index) if direction<0 else (key,index,other,j)
                                        if ref not in known:
                                            row=dict(upper=ref[0],upper_index=ref[1],lower=ref[2],lower_index=ref[3])
                                            self.links.append(row);self.inferred_interfaces.append(row);known.add(ref)
                                break  # a real RDL plane is never jumped
                            if any(self.roles[k] not in via_roles or overlaps(k,geometry) for k in other_group):break
                            at+=direction
        for link in self.links:
            self.outgoing[link['upper']].setdefault(link['upper_index'],[]).append(link)
            self.incoming[link['lower']].setdefault(link['lower_index'],[]).append(link)
        self.ownership=None;self.ownership_notes=[]
    def component(self,key,index):return self.same_plane.find(self.ids[key,index])
    def component_geometry(self,key,index):
        component=self.component(key,index)
        if component not in self._component_geometry:
            self._component_geometry[component]=shapely.union_all([self.nodes[i][2] for i in self.components[component]])
        return self._component_geometry[component]
    def fixed_interfaces(self,key,index):
        """Copper of the fixed RDL a moving via lands on.

        A 固定过孔 plane may sit between the moving via and the fixed RDL
        (e.g. 4/0 运动过孔 → 2/0 固定过孔 → 7/0 固定RDL). The moving via still
        lands on that 固定RDL electrically, so walk through fixed vias instead
        of requiring the RDL to be directly adjacent.
        """
        if self.roles.get(key)!='运动过孔':return []
        out=[];seen={(key,index)}
        def walk(layer,at,depth):
            if depth>8:return
            for links,side in [(self.outgoing[layer].get(at,[]),'lower'),(self.incoming[layer].get(at,[]),'upper')]:
                for link in links:
                    other=link[side];other_index=link[side+'_index'];role=self.roles.get(other)
                    if role=='固定RDL':out.append((other,other_index))
                    elif role=='固定过孔' and (other,other_index) not in seen:
                        seen.add((other,other_index));walk(other,other_index,depth+1)
        walk(key,index,0)
        if out:return list(dict.fromkeys(out))
        return self._fixed_below(key,index)
    def _layer_tree(self,key):
        if not hasattr(self,'_trees'):self._trees={}
        if key not in self._trees:
            geometry=self.geometry.get(key)
            self._trees[key]=STRtree(geometry) if geometry is not None and len(geometry) else None
        return self._trees[key]
    def _fixed_below(self,key,index):
        """Fixed RDL copper directly under a via whose next plane is empty there.

        A via layer such as 固定过孔 exists only where a via is drawn, so a
        moving via with no fixed via underneath still lands on the fixed RDL
        below. Stop at the first plane that really carries copper under the
        via: fixed RDL answers, anything else blocks (an unconnected physical
        plane is never jumped).
        """
        try:start=self.order.index(key)
        except ValueError:return []
        geometry=self.geometry[key][index]
        for other in self.order[start+1:]:
            role=self.roles.get(other)
            if role not in PHYSICAL:continue
            tree=self._layer_tree(other)
            if tree is None:continue
            hits=tree.query(geometry,predicate='intersects')
            if not len(hits):continue
            eps=max(1e-12,self.data.dbu_um**2*.01)
            real=[int(i) for i in hits
                  if shapely.area(shapely.intersection(geometry,self.geometry[other][int(i)]))>eps]
            if not real:continue
            if role=='固定RDL':return [(other,i) for i in real]
            return []
        return []
    def eligible(self,key):
        return np.array([bool(self.fixed_interfaces(key,i)) for i in range(len(self.geometry[key]))],bool)
    def resolve_ownership(self,case):
        signature=tuple(p.wkb for p in case['dies'])
        if self.ownership is not None and getattr(self,'_root_signature',None)==signature:return self.ownership
        self._root_signature=signature
        uf=self.same_plane;sets={uf.find(i):set() for i in range(len(self.nodes))};down={k:set() for k in sets}
        for link in self.links:
            u=uf.find(self.ids[link['upper'],link['upper_index']]);v=uf.find(self.ids[link['lower'],link['lower_index']]);down[u].add(v)
        # A via with no fixed via underneath still lands on the fixed RDL below
        # (see fixed_interfaces); ownership must follow the same contact, or the
        # via would lose its die as soon as the 固定过孔 layer is mapped.
        for key in self.order:
            if self.roles.get(key)!='运动过孔':continue
            for i in range(len(self.geometry[key])):
                if self.outgoing[key].get(i):continue
                for layer,index in self._fixed_below(key,i):
                    down[uf.find(self.ids[key,i])].add(uf.find(self.ids[layer,index]))
        # Different DIEs can end at different metal heights. Seed the deepest
        # actual terminal component for EACH DIE, not the global final plane.
        # A component with downward links is a carrier, never a new chip root:
        # fanout over another DIE must continue to follow its original DIE.
        tree=STRtree(case['dies']);eps=max(1e-12,self.data.dbu_um**2*.01)
        candidates={i:[] for i in range(len(case['dies']))}
        for rank,ids in enumerate(self.plane_nodes):
            for node in ids:
                component=uf.find(node)
                if down[component]:continue
                p=self.nodes[node][2]
                for owner in tree.query(p,predicate='intersects'):
                    if p.intersection(case['dies'][owner]).area>eps:candidates[int(owner)].append((rank,component,self.nodes[node][0]))
        self.terminal_planes_by_die=[]
        for owner,choices in candidates.items():
            rank=max((r for r,_,_ in choices),default=-1)
            roots=[(component,key) for r,component,key in choices if r==rank]
            for component,_ in roots:sets[component].add(owner)
            self.terminal_planes_by_die.append(dict(die=owner+1,layers=sorted({k for _,k in roots}),components=sorted({c for c,_ in roots})))
        for ids in reversed(self.plane_nodes):
            for component in {uf.find(i) for i in ids}:
                for child in down[component]:sets[component].update(sets[child])
        self.possible_owners={key:[tuple(sorted(sets[uf.find(self.ids[key,i])])) for i in range(len(self.geometry[key]))]
                              for key in self.order if self.roles.get(key) in PHYSICAL}
        self.ownership={key:np.array([p[0] if len(p)==1 else -2 for p in values],np.int32) for key,values in self.possible_owners.items()}
        self.ownership_notes=[]
        for key,values in self.possible_owners.items():
            if self.roles[key] not in MOVING:continue
            for i,owners in enumerate(values):
                if len(owners)!=1:self.ownership_notes.append(dict(layer=key,index=i+1,dies=[n+1 for n in owners],reason='连接多颗芯片，不能作单一刚体' if owners else '未沿相邻下层连接追溯到芯片；请检查该层用途、层序及名义接触'))
        # Moving metal that reaches no chip through the stack still rides a die:
        # the nearest one by geometry. Left unowned it would keep its nominal
        # position while the rest of its die moved away, so every moved die would
        # read as shorted against copper that should have travelled with it.
        # Moving metal that reaches no chip - or reaches several - still rides
        # the nearest die by geometry: a wire tying two chips together cannot be
        # a single rigid body, but leaving it at its nominal position reads as a
        # short against every moved die, so it follows its nearest one and the
        # guess is reported instead of hidden.
        self.positional_ownership=[]
        for key,values in self.possible_owners.items():
            if self.roles.get(key) not in MOVING:continue
            for i,owners in enumerate(values):
                if len(owners)==1:continue
                geometry=self.geometry[key][i]
                distances=[float(geometry.distance(die)) for die in case['dies']]
                nearest=int(np.argmin(distances))
                self.ownership[key][i]=nearest
                self.positional_ownership.append(dict(layer=key,index=i+1,die=nearest+1,
                                                      distance_um=round(distances[nearest],6),
                                                      mode='shared' if owners else 'unconnected',
                                                      dies=[n+1 for n in owners]))
        if self.positional_ownership:
            assigned={(q['layer'],q['index']) for q in self.positional_ownership}
            self.ownership_notes=[q for q in self.ownership_notes
                                  if (q['layer'],q['index']) not in assigned]
        return self.ownership
    def filter_case(self,case):return case
    def summary(self):
        return dict(layer_order=self.order,contact_count=len(self.links),inferred_fixed_interfaces=list(self.inferred_interfaces),
                    connected_by_layer={k:int(self.eligible(k).sum()) for k in self.order if self.roles.get(k)=='运动过孔'},
                    ownership_notes=self.ownership_notes,positional_ownership=self.positional_ownership,
                    positional_ownership_count=len(self.positional_ownership),
                    positional_shared_count=sum(q.get('mode')=='shared' for q in self.positional_ownership),
                    positional_ownership_by_layer={k:sum(q['layer']==k for q in self.positional_ownership)
                                                   for k in self.order if any(q['layer']==k for q in self.positional_ownership)},
                    chip_terminal_plane=sorted({k for row in getattr(self,'terminal_planes_by_die',[]) for k in row['layers']}),chip_terminal_planes_by_die=getattr(self,'terminal_planes_by_die',[]),ignored_empty_metal_layers=self.empty_metal_layers,
                    rule='按上→下相邻非空金属连接追溯；各DIE最深的局部末端接触定义根；跨DIE扇出不按坐标就近归属；完全没有连接到芯片、或同时连接多颗DIE的运动层金属按位置归到最近的DIE（记为推定归属）；仅运动过孔—固定RDL界面参与不足和叠加')
