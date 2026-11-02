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
        self.labels=[];self.viable=None;self.occupied=None

    @staticmethod
    def summed(grid):
        return np.pad(grid.cumsum(0).cumsum(1),((1,0),(1,0)))

    def cell_count(self,table,lo,hi,contained):
        # Only a *whole* occupied cell inside a text box proves an overlap.
        # For possible label centres include every tile that touches the box.
        lower,upper=(np.ceil,np.floor) if contained else (np.floor,np.ceil)
        extent=np.array([table.shape[1]-1,table.shape[0]-1])
        x0,y0=np.clip(lower((lo-self.viewport[:2])/4),0,extent).astype(int).T
        x1,y1=np.clip(upper((hi-self.viewport[:2])/4),0,extent).astype(int).T
        return (x1>x0)&(y1>y0)&((table[y1,x1]-table[y1,x0]-table[y0,x1]+table[y0,x0])>0)

    def prescreen(self,texts):
        if self.tree is None or len(texts)<64:return
        # The actual gate corners, midpoints and centres are valid witnesses.
        # A prefix sum cheaply proves dense areas have no place for ANY label.
        half=np.array([self.half_size(t) for t in texts]);small=half.min(axis=0)
        low,high=self.viewport[:2],self.viewport[2:]
        nx,ny=np.maximum(1,np.ceil((high-low)/4).astype(int))
        samples=np.concatenate([self.quads,(self.quads+np.roll(self.quads,1,axis=1))/2,self.quads.mean(axis=1)[:,None,:]],axis=1).reshape(-1,2)
        xy=np.floor((samples-low)/4).astype(int)
        inside=np.all((xy>=0)&(xy<[nx,ny]),axis=1);x,y=xy[inside].T
        occupied=np.zeros((ny,nx),int);occupied[y,x]=1;self.occupied=self.summed(occupied)
        ix,iy=np.meshgrid(np.arange(nx),np.arange(ny))
        corner=low+4*np.column_stack((ix.ravel(),iy.ravel()))
        free=~self.cell_count(self.occupied,corner+4-small,corner+small,True)
        free &= np.all((corner+4>=low+small)&(corner<=high-small),axis=1)
        margin=half+max(LEADERS)+4
        self.viable=self.cell_count(self.summed(free.reshape(ny,nx)),self.quads.min(axis=1)-margin,
                                    self.quads.max(axis=1)+margin,False)

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
        if self.viable is not None and not self.viable[index]:return
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
        if self.occupied is not None:valid &= ~self.cell_count(self.occupied,lo,hi,True)
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
