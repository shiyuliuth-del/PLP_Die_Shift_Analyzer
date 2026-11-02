"""Discrete coverage of each connected moving via, computed in its owner's grid."""
import numpy as np
import shapely
from matplotlib.collections import PatchCollection
from .acceleration import rigid_pose_batch
from .resources import parallel_map
from .statistics import ExtremeCatalog
from .viewport import overlaps


def extreme_footprints(model,p,progress=lambda *_:None):
    grid=ExtremeCatalog(model.case,p)
    def one(r):
        owner=r['owner'];g=r['poly'];xy=shapely.get_coordinates(g)
        poses=grid.locals[owner]
        vertices=rigid_pose_batch(xy,model.bound.centers[owner],poses,getattr(model.bound,'acceleration','auto'))
        copies=shapely.set_coordinates(np.full(len(poses),g,object),vertices.reshape(-1,2))
        shape=shapely.union_all(copies)
        return dict(key=r['key'],index=r['index'],owner=owner,geometry=shape,bounds=shape.bounds,poses=len(copies))
    moving=[r for r in model.records if grid.radices[r['owner']]>1]
    shapes=parallel_map(one,moving,progress=lambda done,total:progress(round(100*done/total),f'离散过孔足迹 {done}/{total}'))
    return dict(features=shapes,poses=sum(q['poses'] for q in shapes),package_states=grid.total+1)


class FootprintOverlay:
    def __init__(self,window):
        self.window=window;self.artist=None;self.stamp=None

    def draw(self):
        w=self.window
        if self.artist is None or self.artist not in w.ax.collections:
            self.artist=PatchCollection([],facecolor='#7e89b726',edgecolor='#73639faa',linewidths=.8,zorder=6.8)
            w.ax.add_collection(self.artist);self.stamp=None
        data=w.footprint_data if w.footprint_toggle.isChecked() else None
        visible=frozenset(k for k,v in w.visible_boxes.items() if v.isChecked())
        stamp=(id(data),visible,w.view_bounds)
        if stamp==self.stamp:return
        features=[q for q in (data or {}).get('features',()) if q['key'] in visible and overlaps(q['bounds'],w.view_bounds)]
        self.artist.set_paths([w.geometry_patch(q['geometry']) for q in features])
        self.artist.set_visible(bool(features));self.artist._static_preview=('discrete-footprint',stamp)
        self.stamp=stamp
