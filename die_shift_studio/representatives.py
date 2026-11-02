"""Focused joint poses for costly, coupled copper deformation; the full grid is lazy."""
from itertools import combinations,product
import numpy as np
import shapely
from .statistics import ExtremeCatalog,PREVIEW_SCOPE
from .pivots import rotation_centers
from .preview import rigid_transform


def pair_tables(case,p,grid,tol):
    centers=rotation_centers(case,p);polys=[];envelopes=[]
    for i,(die,poses) in enumerate(zip(case['dies'],grid.locals)):
        vertices=np.asarray(die.exterior.coords)
        shapes=shapely.polygons(np.asarray([rigid_transform(vertices,centers[i],z) for z in poses]))
        polys.append(shapes);envelopes.append(shapely.box(*shapely.total_bounds(shapes)))
    candidates=shapely.STRtree(envelopes).query(envelopes,predicate='dwithin',distance=p.min_die_gap_um+tol)
    tables=[]
    for i,j in sorted((int(i),int(j)) for i,j in candidates.T if i<j):
        distances=shapely.distance(polys[i][:,None],polys[j][None,:])
        low=[int(v) for v in np.unravel_index(distances.argmin(),distances.shape)]
        high=[int(v) for v in np.unravel_index(distances.argmax(),distances.shape)]
        tables.append(dict(owners=[i,j],near=low,far=high,hits=(distances<=p.min_die_gap_um+tol).tolist()))
    return tables


class RepresentativeExtremes:
    def __init__(self,case,p,full=None,coupled=(),pairs=None):
        self.full=full if full is not None else ExtremeCatalog(case,p)
        grid=self.full;n=grid.count;self.indices=[];self.names={};self.unbounded=False;self.k=1.
        def add(digits,title):
            index=grid.index(digits)
            if index>=0 and index not in self.names:
                self.indices.append(index);self.names[index]=title
        zero=[0]*n
        for i,local in enumerate(grid.locals):
            for digit in range(1,len(local)):
                d=zero.copy();d[i]=digit;add(d,f'DIE {i+1} · {grid.labels[i][digit]}')
        for signs in product((0,-1,1),repeat=3):
            add([lookup[signs] for lookup in grid.sign_digits],'同向 · '+grid._label(signs))
        pairs=pair_tables(case,p,grid,1e-6) if pairs is None else pairs
        self.pairs={tuple(info['owners']) for info in pairs}
        for info in pairs:
            i,j=info['owners']
            for label,digits in [('相向',info['near']),('背向',info['far'])]:
                d=zero.copy();d[i],d[j]=digits;add(d,f'DIE {i+1}/{j+1} {label}边界')
        for i,j in sorted(self.pairs|{tuple(sorted(q)) for q in coupled}):
            if len(grid.locals[i])==1 or len(grid.locals[j])==1:continue
            for signs in product((-1,1),repeat=3):
                d=zero.copy();d[i]=grid.sign_digits[i][signs];d[j]=grid.sign_digits[j][tuple(-s for s in signs)]
                add(d,f'DIE {i+1}/{j+1} 反向 · '+grid._label(signs))
        self.total=len(self.indices)

    @classmethod
    def from_result(cls,case,p,result,full=None):
        result=result or {}
        shared={pair for net in result.get('interdie_networks',()) for pair in combinations([i-1 for i in net['dies']],2)}
        return cls(case,p,full,shared,(result.get('extreme_factors') or {}).get('pairs'))

    def __getitem__(self,index):
        if not 0<=index<self.total:raise IndexError(index)
        key=self.indices[index];return dict(self.full[key],name=self.names[key])
    def __len__(self):return self.total
    def page(self,page,size=64):
        return [(i,dict(self.full[i],name=self.names[i])) for i in self.indices[page*size:(page+1)*size]]
