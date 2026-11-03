"""Rank/select of mixed-radix poses matching a union of local binary events."""
from dataclasses import dataclass
from functools import lru_cache
import math
import numpy as np


@dataclass(frozen=True)
class PoseClause:
    # One Cartesian product of allowed digit sets; all omitted owners are free.
    allowed: tuple


class ProblemIndex:
    def __init__(self,grid,factors):
        self.grid=grid
        sizes=grid.radices
        self.suffix=[math.prod(sizes[i:]) for i in range(len(sizes)+1)]
        self.nodes=[None,None]
        intern={}
        def branch(axis,children):
            if all(n==children[0] for n in children):return children[0]
            key=(axis,tuple(children))
            if key not in intern:
                intern[key]=len(self.nodes);self.nodes.append(key)
            return intern[key]
        @lru_cache(None)
        def either(a,b):
            if a==1 or b==1:return 1
            if a==0 or a==b:return b
            if b==0:return a
            i,ca=self.nodes[a];j,cb=self.nodes[b];axis=min(i,j)
            return branch(axis,[either(ca[d] if i==axis else a,cb[d] if j==axis else b) for d in range(sizes[axis])])
        def encode(axes,values):
            if not np.any(values):return 0
            if np.all(values):return 1
            return branch(axes[0],[encode(axes[1:],v) for v in values])
        self.root=0
        for factor in factors:
            if self.root==1:break
            if isinstance(factor,PoseClause):
                term=1
                for i,allowed in reversed(factor.allowed):
                    term=branch(i,[term if d in allowed else 0 for d in range(sizes[i])])
            else:
                owners,table=factor
                term=encode(owners,np.asarray(table,bool))
            self.root=either(self.root,term)
        @lru_cache(None)
        def count(n,level):
            if n<2:return n*self.suffix[level]
            axis,children=self.nodes[n]
            return self.suffix[level]//self.suffix[axis]*sum(count(ch,axis+1) for ch in children)
        self.count=count
        self.zero=int(self.matches([0]*grid.count))
        self.total=count(self.root,0)-self.zero
        either.cache_clear()

    def matches(self,digits):
        n=self.root
        while n>=2:
            k,children=self.nodes[n];n=children[digits[k]]
        return bool(n)

    def at(self,rank):
        if rank<0 or rank>=self.total:raise IndexError(rank)
        rank+=self.zero;n=self.root;digits=[]
        for i,radix in enumerate(self.grid.radices):
            children=self.nodes[n][1] if n>=2 and self.nodes[n][0]==i else None
            if children is None:
                ways=self.count(n,i+1);digit,rank=divmod(rank,ways)
            else:
                for digit,child in enumerate(children):
                    ways=self.count(child,i+1)
                    if rank<ways:n=child;break
                    rank-=ways
            digits.append(digit)
        return self.grid.index(digits)


class ExplicitIndex:
    def __init__(self,grid,indices):
        self.grid=grid;self.indices=sorted(i for i in indices if i>=0);self.total=len(self.indices)
    def at(self,rank):return self.indices[rank]
    def matches(self,digits):
        from bisect import bisect_left
        i=self.grid.index(digits);k=bisect_left(self.indices,i)
        return k<self.total and self.indices[k]==i


class FilteredCatalog:
    def __init__(self,catalog,index):
        self.catalog=catalog;self.index=index
        self.focus=[i for i in catalog.indices if index.matches(index.grid.choices(i))] if hasattr(catalog,'indices') else None
        self.total=len(self.focus) if self.focus is not None else index.total

    def page(self,page,size):
        grid=self.index.grid;rows=[]
        for rank in range(page*size,min((page+1)*size,self.total)):
            i=self.focus[rank] if self.focus is not None else self.index.at(rank)
            q=grid[i]
            if self.focus is not None:q=dict(q,name=self.catalog.names[i])
            rows.append((i,q))
        return rows


def contact_factors(checks):
    if checks['base'][0]:return [((),True)]
    return [((i,),[row[0]>0 for row in rows]) for i,rows in enumerate(checks['local'])]
