"""Stable physical mouth identities shared by preview and probability integration."""
from collections import defaultdict
import numpy as np
from .stack import UnionFind

def mouths(gates,half_depth,tol):
    groups=defaultdict(list)
    for i,g in enumerate(gates):groups[g.pair,g.strand_refs].append(i)
    for ids in groups.values():
        if len(ids)==1:
            yield ids;continue
        group=[gates[i] for i in ids]
        points=np.array([g.ends.mean(axis=0) for g in group])
        normal=np.array([(g.ends[1]-g.ends[0])/g.nominal_gap for g in group])
        clusters=UnionFind(len(ids));ordered={g.band:i for i,g in enumerate(group)}
        for i,g in enumerate(group):
            previous=ordered.get((*g.band[:-1],g.band[-1]-1)) if g.band is not None else None
            if previous is not None:clusters.union(previous,i)
        for i in range(len(ids)):
            nearby=np.linalg.norm(points[i+1:]-points[i],axis=1)<=2*half_depth+tol
            aligned=normal[i+1:]@normal[i]>.995
            for offset in np.flatnonzero(nearby & aligned):
                j=i+1+int(offset)
                if group[i].features!=group[j].features:clusters.union(i,j)
        result=defaultdict(list)
        for i,index in enumerate(ids):result[clusters.find(i)].append(index)
        yield from result.values()

def assign(name,plane,half_depth,tol):
    gates=plane['gates']
    for ids in mouths(gates,half_depth,tol):
        first=min((gates[i] for i in ids),key=lambda g:tuple(g.ends.ravel()))
        key=('channel',name,tuple(plane['walls'][i].name for i in first.pair),
             first.strand_refs,tuple(float(v) for v in first.ends.ravel()))
        for i in ids:gates[i].event_key=key

def edge_keys(row):
    kind='keepout' if row.get('kind') in ('禁布区侵入','keepout') else 'edge'
    return tuple((kind,row['layer'],int(i)) for i in row['indices'])
