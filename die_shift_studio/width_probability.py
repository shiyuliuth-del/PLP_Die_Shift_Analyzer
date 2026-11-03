"""Conservative probability of a specific retained channel losing capacity."""
import math
import numpy as np
import shapely
from scipy.special import ndtr
from .distribution import normal_interval,normalization
from .statistics import mean_and_bounds
from .preview import rigid_transform


class ChannelProbability:
    """Prove rare events by projecting the two actual walls onto a separating axis.

    Distance between the walls is at least their signed projected separation.
    Translation is Gaussian before conditioning on the hard input rectangle.
    Minima over rotation intervals and upper bounds on conditional mass never
    classify an unresolved row as rare.
    """
    def __init__(self,model,params,board,tol):
        self.board=board
        self.convex=board.equals(board.convex_hull)
        self.centers=model.bound.centers
        self.tol=tol
        self.k=params.target_nsigma
        self.threshold=params.failure_threshold
        self.laws=[]
        for i in range(len(self.centers)):
            p=params.for_die(i)
            mu,b=mean_and_bounds(p,len(self.centers))
            sigma=b/p.scale_nsigma
            z,err=normalization(p.scale_nsigma,p.rho_xy,*map(bool,b[:2]),False)
            self.laws.append((mu,b,sigma,p.rho_xy,z-err))
        self.wall_data={};self.results={};self.edge_shapes={};self.edge_results={}
        self.board_faces=[]
        if self.convex and board.geom_type=='Polygon':
            points=shapely.get_coordinates(board.exterior);inside=np.array(board.centroid.coords[0])
            for start,end in zip(points,points[1:]):
                d=end-start;length=np.linalg.norm(d)
                if not length:continue
                n=np.array([-d[1],d[0]])/length
                if (inside-start)@n<0:n=-n
                self.board_faces.append((n,float(start@n)))

    def geometry(self,wall):
        key=id(wall)
        if key not in self.wall_data:
            xy=shapely.get_coordinates(wall.geometry.convex_hull)
            sweep=wall.geometry.convex_hull
            if wall.owner>=0:
                mu,b,_,_,_=self.laws[wall.owner];center=self.centers[wall.owner]
                moved=rigid_transform(xy,center,mu)
                rotation=2*np.linalg.norm(xy-center,axis=1).max()*math.sin(min(math.pi,b[2])/2)
                lo=moved.min(axis=0)-b[:2]-rotation
                hi=moved.max(axis=0)+b[:2]+rotation
                sweep=shapely.box(*lo,*hi)
            self.wall_data[key]=(xy,sweep,self.board.covers(sweep))
        return self.wall_data[key]

    def projection(self,wall,axis,bins):
        xy=self.geometry(wall)[0]
        if wall.owner<0:
            return np.array([(xy@axis).min()]),np.ones(1),0.,0.,1.
        mu,b,sigma,rho,z=self.laws[wall.owner];center=self.centers[wall.owner]
        v=xy-center
        a=v@axis;perp=v@np.array([axis[1],-axis[0]])
        # Minimum of a*cos(t)+perp*sin(t), including extrema inside the slice.
        edges=mu[2]+np.linspace(-b[2],b[2],bins+1) if b[2] else np.array([mu[2],mu[2]])
        left,right=edges[:-1,None],edges[1:,None]
        values=np.minimum(a*np.cos(left)+perp*np.sin(left),a*np.cos(right)+perp*np.sin(right))
        phase=np.arctan2(perp,a)+math.pi
        includes=np.mod(phase-left,2*math.pi)<=right-left+1e-14
        lower=np.where(includes,-np.hypot(a,perp),values).min(axis=1)+(center+mu[:2])@axis
        weights=(normal_interval((edges[:-1]-mu[2])/sigma[2],(edges[1:]-mu[2])/sigma[2])/
                 float(normal_interval(-b[2]/sigma[2],b[2]/sigma[2]))) if b[2] else np.ones(1)
        sx,sy=sigma[:2]*axis
        variance=(sx+rho*sy)**2+sy**2*max(0.,1-rho*rho)
        return lower,weights,variance,float(np.abs(axis)@b[:2]),z

    def bound(self,a,b,axis,demand,bins):
        av,aw,avar,alim,az=self.projection(a,-axis,bins)
        bv,bw,bvar,blim,bz=self.projection(b,axis,bins)
        delta=demand+self.tol-av[:,None]-bv
        sd=math.sqrt(avar+bvar)
        tail=np.minimum(1.,ndtr(delta/sd)/(az*bz)) if sd else (delta>=0).astype(float)
        tail=np.where(delta < -alim-blim,0.,tail)
        return min(1.,float(aw@tail@bw)*(1+1e-12))

    def edge(self,key,index,geo,owner,rule):
        # A convex board inset by 'rule' is the intersection of its inward
        # half-planes. Any edge violation is in the union of their events.
        if not self.board_faces or owner<-1:return None
        token=(key,index,rule)
        if token in self.edge_results:return self.edge_results[token]
        if owner<0:upper=1.  # called only for an observed fixed-copper violation
        else:
            from .width_analysis import Wall
            wall=self.edge_shapes.get((key,index))
            if wall is None:
                wall=Wall(key,'RDL',geo,(),owner);self.edge_shapes[key,index]=wall
            upper=0.
            for n,offset in self.board_faces:
                v,weights,var,limit,z=self.projection(wall,n,8)
                delta=offset+rule+self.tol-v
                f=np.minimum(1.,ndtr(delta/math.sqrt(var))/z) if var else (delta>=0).astype(float)
                upper+=float(weights@np.where(delta < -limit,0.,f))
                if upper>=1:break
            upper=min(1.,upper*(1+1e-12))
        event=dict(upper=upper,threshold=self.threshold,low=upper<=self.threshold)
        self.edge_results[token]=event
        return event

    def classify(self,row,walls):
        if self.k==0 or math.isinf(self.k) or row['status']=='宽度足够':
            return None
        if row['locked'] or row['split'] or row['unresolved']:return None
        demand=row['minimum_um'] if row['status']=='宽度不足' else row['keep_um']
        a,b=walls
        if min(a.owner,b.owner)<-1:return None
        original=np.asarray(row['nominal_witness_um'],float)
        key=(id(a),id(b),tuple(original.ravel()),demand)
        if key in self.results:return self.results[key]
        ga,gb=self.geometry(a),self.geometry(b)
        upper=None
        if ga[2] and gb[2] and (self.convex or self.board.covers(shapely.union_all([ga[1],gb[1]]).convex_hull)):
            if a.owner==b.owner:
                upper=float(row['nominal_gap_um']<demand+self.tol)
            else:
                n=original[1]-original[0];length=np.linalg.norm(n)
                if length>self.tol:
                    n/=length
                    upper=self.bound(a,b,n,demand,1)
                    if upper>self.threshold and upper<1 and any(o>=0 and self.laws[o][1][2] for o in (a.owner,b.owner)):
                        upper=self.bound(a,b,n,demand,8)
        result=None if upper is None else dict(upper=upper,threshold=self.threshold,low=upper<=self.threshold)
        self.results[key]=result
        return result
