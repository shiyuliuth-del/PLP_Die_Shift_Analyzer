"""Display geometry for enclosure and clearance rings. Never changes contact results."""
import numpy as np
import shapely

# Muted teal and violet remain distinct on the light copper fills.
RANGES=(('annulus','show_annulus_range','过孔包围量范围','#74aaa5',.63),
        ('spacing','show_spacing_range','过孔线距范围','#b28eb5',.59))

def outlines(regions,name,indices):
    # Batch GEOS calls and retain independent polygon shells and interior holes.
    paths={i:[] for i in indices}
    if not indices:return paths
    parts,owners=shapely.get_parts([regions[i][name] for i in indices],return_index=True)
    rings,parents=shapely.get_rings(parts,return_index=True)
    ends=np.cumsum(shapely.get_num_coordinates(rings));xy=shapely.get_coordinates(rings)
    for lo,hi,owner in zip(np.r_[0,ends[:-1]],ends,owners[parents]):
        paths[indices[owner]].append(xy[lo:hi])
    return paths

class ContactRanges:
    def __init__(self,regions):
        self.regions=regions
        self.bounds=np.asarray([q['bounds'] if 'bounds' in q else q['spacing'].bounds for q in regions]).reshape(-1,4)
        self.groups={}
        for i,q in enumerate(regions):self.groups.setdefault(q['key'],[]).append(i)
        self.groups={k:np.asarray(v,dtype=int) for k,v in self.groups.items()}
        self.path_cache={};self.drawn={}

    def draw(self,artists,settings,visible,view):
        enabled=[getattr(settings,setting) for _,setting,*_ in RANGES]
        if not any(enabled):
            for artist in artists:artist.set_visible(False)
            return
        x0,y0,x1,y1=view;b=self.bounds
        within=(b[:,0]<=x1)&(b[:,2]>=x0)&(b[:,1]<=y1)&(b[:,3]>=y0)
        indices=tuple(int(i) for k,ids in self.groups.items() if k in visible for i in ids[within[ids]])
        for (name,*_),show,artist in zip(RANGES,enabled,artists):
            if not show:
                artist.set_visible(False);continue
            if self.drawn.get(name)!=indices:
                old=self.path_cache.get(name,{})
                current={i:old[i] for i in indices if i in old}
                current.update(outlines(self.regions,name,[i for i in indices if i not in old]))
                artist.set_segments([line for i in indices for line in current[i]])
                self.path_cache[name]=current;self.drawn[name]=indices
            artist.set_visible(bool(indices))
