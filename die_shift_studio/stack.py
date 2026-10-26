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
            up,down=pair;lower=[(k,i,g) for k in down for i,g in enumerate(self.geometry[k])]
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
        if self.roles.get(key)!='运动过孔':return []
        out=[]
        for links,side in [(self.outgoing[key].get(index,[]),'lower'),(self.incoming[key].get(index,[]),'upper')]:
            for link in links:
                if self.roles.get(link[side])=='固定RDL':out.append((link[side],link[side+'_index']))
        return list(dict.fromkeys(out))
    def eligible(self,key):
        return np.array([bool(self.fixed_interfaces(key,i)) for i in range(len(self.geometry[key]))],bool)
    def resolve_ownership(self,case):
        signature=tuple(p.wkb for p in case['dies'])
        if self.ownership is not None and getattr(self,'_root_signature',None)==signature:return self.ownership
        self._root_signature=signature
        uf=self.same_plane;sets={uf.find(i):set() for i in range(len(self.nodes))};down={k:set() for k in sets}
        for link in self.links:
            u=uf.find(self.ids[link['upper'],link['upper_index']]);v=uf.find(self.ids[link['lower'],link['lower_index']]);down[u].add(v)
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
        return self.ownership
    def filter_case(self,case):return case
    def summary(self):
        return dict(layer_order=self.order,contact_count=len(self.links),
                    connected_by_layer={k:int(self.eligible(k).sum()) for k in self.order if self.roles.get(k)=='运动过孔'},
                    ownership_notes=self.ownership_notes,chip_terminal_plane=sorted({k for row in getattr(self,'terminal_planes_by_die',[]) for k in row['layers']}),chip_terminal_planes_by_die=getattr(self,'terminal_planes_by_die',[]),ignored_empty_metal_layers=self.empty_metal_layers,
                    rule='按上→下相邻非空金属连接追溯；各DIE最深的局部末端接触定义根；跨DIE扇出不按坐标就近归属；仅运动过孔—固定RDL界面参与不足和叠加')
