"""Place width callouts in display points without covering a visible throat."""
import numpy as np
import shapely
from shapely.strtree import STRtree
from matplotlib.text import Text

# Scan all sides at short leader lengths before trying more distant placements.
DIRECTIONS=np.asarray([(0,1),(1,0),(-1,0),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)],float)
DIRECTIONS/=np.linalg.norm(DIRECTIONS,axis=1)[:,None]
LEADERS=(5.,13.,25.,40.)


class LabelLayout:
    def __init__(self,ax,rectangles,fontsize,measurements,avoid=True):
        self.ax=ax;self.fontsize=fontsize;self.measurements=measurements
        self.renderer=ax.figure.canvas.get_renderer()
        self.scale=self.renderer.points_to_pixels(1)
        self.viewport=np.asarray(ax.bbox.extents)/self.scale
        xy=shapely.get_coordinates(rectangles).reshape(-1,5,2)[:,:4]
        self.quads=ax.transData.transform(xy.reshape(-1,2)).reshape(-1,4,2)/self.scale
        self.geometry=shapely.polygons(self.quads)
        self.tree=STRtree(self.geometry) if avoid else None
        self.labels=[]

    def half_size(self,text):
        key=(text,self.fontsize,self.scale)
        if key not in self.measurements:
            artist=Text(text=text,fontsize=self.fontsize,ha='center',va='center',multialignment='left')
            artist.set_figure(self.ax.figure)
            b=artist.get_window_extent(self.renderer)
            # Match the annotation's boxstyle pad and allow for its border.
            half=np.array([b.width,b.height])/(2*self.scale)+.15*self.fontsize+1.5
            if len(self.measurements)>=512:self.measurements.clear()
            self.measurements[key]=half
        return self.measurements[key]

    def place(self,index,text,preferred=None):
        q=self.quads[index];low,high=self.viewport[:2],self.viewport[2:]
        if np.any(q.max(axis=0)<low) or np.any(q.min(axis=0)>high):return
        base=q.mean(axis=0)
        if np.any(base<low) or np.any(base>high):
            part=shapely.intersection(self.geometry[index],shapely.box(*self.viewport))
            if part.is_empty:return
            base=shapely.get_coordinates(part.centroid)[0]
        # Intersect each candidate ray with the real, possibly rotated gate.
        basis=np.column_stack([q[1]-q[0],q[3]-q[0]])
        if abs(np.linalg.det(basis))<1e-12:return
        inverse=np.linalg.inv(basis);local=inverse@(base-q[0])
        velocity=DIRECTIONS@inverse.T
        with np.errstate(divide='ignore',invalid='ignore'):
            travel=np.where(abs(velocity)<1e-12,np.inf,(np.where(velocity>0,1.,0.)-local)/velocity)
        tips=base+DIRECTIONS*np.maximum(0.,travel.min(axis=1))[:,None]
        half=self.half_size(text);order=list(range(len(tips)))
        if preferred in order:order.remove(preferred);order.insert(0,preferred)
        anchors=np.tile(tips[order],(len(LEADERS),1))
        directions=np.tile(DIRECTIONS[order],(len(LEADERS),1))
        centers=anchors+np.repeat(LEADERS,len(order))[:,None]*directions+half*np.sign(directions)
        lo,hi=centers-half,centers+half
        valid=np.all((anchors>=low) & (anchors<=high) & (lo>=low+1) & (hi<=high-1),axis=1)
        if self.labels and valid.any():
            old=np.asarray(self.labels)
            conflict=(np.all(hi[:,None,:]>old[:,0],axis=2) & np.all(lo[:,None,:]<old[:,1],axis=2))
            valid &= ~conflict.any(axis=1)
        indices=np.flatnonzero(valid)
        if self.tree is not None and len(indices):
            boxes=shapely.box(*lo[indices].T,*hi[indices].T)
            overlaps,_=self.tree.query(boxes,predicate='intersects')
            valid[indices[overlaps]]=False
        choices=np.flatnonzero(valid)
        if len(choices):
            i=choices[0];self.labels.append((lo[i],hi[i]))
            anchor=self.ax.transData.inverted().transform(anchors[i]*self.scale)
            return order[i%len(order)],tuple(anchor),tuple(centers[i]-anchors[i])
