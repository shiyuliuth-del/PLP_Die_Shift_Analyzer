"""Compact domain-boundary states; continuous assurance remains analytical."""
import math
from itertools import product
import numpy as np
from shapely.ops import nearest_points
from .statistics import mean_and_bounds

PREVIEW_SCOPE=('预演采用输入均值±单边幅度的有界组合，与目标σ独立。列出8个XYZ角点组合、各芯片不利方向、最危险芯片对与封装边界方向。'
 '平移严格不超输入幅度；孔的实际位移另含绕所选旋转中心转动。代表预演不枚举多芯片全部组合，有界连续概率不能用有限代表状态替代；统计窗口另按目标σ计算。')

class RepresentativeExtremes:
    def __init__(self,case,p):
        self.unbounded=False
        self.items=[];m=len(case['dies']);mu,_=mean_and_bounds(p,m);bounds=np.array([mean_and_bounds(p.for_die(i),m)[1] for i in range(m)]);k=1.;self.k=k
        nominal=np.tile(mu,(m,1));centers=__import__('die_shift_studio.pivots',fromlist=['rotation_centers']).rotation_centers(case,p)
        def support(c,i):
            return mu+bounds[i]*np.sign(c)
        def add(name,states,detail):
            if np.allclose(states,0,atol=1e-12,rtol=0):return
            if any(np.allclose(states,e['states'],atol=1e-11,rtol=1e-12) for e in self.items):return
            self.items.append(dict(name=name,states=np.asarray(states),kind=detail,detail=detail,per_die_labels=[detail]*m,k=k))
        def jac(point,i):
            a=np.asarray(point)-centers[i];c,s=math.cos(mu[2]),math.sin(mu[2]);bx,by=np.array([[c,-s],[s,c]])@a
            return np.array([[1,0,-by],[0,1,bx]])
        if np.any(mu):add('均值偏移',nominal,'输入均值')
        for signs in product((-1,1),repeat=3):
            label=' / '.join(axis+('+' if sign>0 else '−') for axis,sign in zip(('X','Y','O'),signs))
            add(label,mu+bounds*np.array(signs),'输入单边幅度的平移与转角叠加组合')
        vectors=[]
        for i in range(m):
            polygons=case['pins'][i]+case['outputs'][i]
            points=np.array([poly.centroid.coords[0] for poly in polygons]) if polygons else np.asarray(case['dies'][i].exterior.coords)[:-1]
            relative=points-centers[i];c,s=math.cos(mu[2]),math.sin(mu[2]);rot=relative@np.array([[c,s],[-s,c]])
            js=np.zeros((len(points),2,3));js[:,0,0]=1;js[:,1,1]=1;js[:,0,2]=-rot[:,1];js[:,1,2]=rot[:,0]
            vals,vec=np.linalg.eigh(js@np.diag(bounds[i]**2)@np.swapaxes(js,1,2));index=int(np.argmax(vals[:,-1]))
            vectors.append(js[index].T@vec[index,:,-1])
        for sign in (-1,1):add('各芯片最不利位移'+('−' if sign<0 else '+'),np.array([support(sign*v,i) for i,v in enumerate(vectors)]),'各芯片最大线性接口位移方向；精确刚体显示')
        if m>1:
            # Rank every pair by corner-envelope demand / nominal free gap.
            corner=[np.linalg.norm(bounds[i,:2])+2*max(np.linalg.norm(np.asarray(d.exterior.coords)-centers[i],axis=1))*math.sin(min(math.pi,bounds[i,2])/2) for i,d in enumerate(case['dies'])]
            pairs=[]
            for i in range(m):
                for j in range(i):
                    gap=case['dies'][i].distance(case['dies'][j])-p.min_die_gap_um
                    pairs.append(((corner[i]+corner[j])/gap if gap>0 else math.inf,i,j))
            _,i,j=max(pairs);a,b=nearest_points(case['dies'][i],case['dies'][j]);a=np.array(a.coords[0]);b=np.array(b.coords[0]);u=b-a
            if np.linalg.norm(u)<1e-12:u=centers[j]-centers[i]
            u=u/max(np.linalg.norm(u),1e-12)
            for sign,label in [(1,'相向'),(-1,'背向')]:
                states=nominal.copy();states[i]=support(sign*jac(a,i).T@u,i);states[j]=support(-sign*jac(b,j).T@u,j)
                add(f'DIE {j+1}/{i+1} {label}',states,'全体芯片对中输入幅度间隙比值最高的一对')
        states=[]
        for i,die in enumerate(case['dies']):
            a,b=nearest_points(die,case['board'].boundary);a=np.array(a.coords[0]);u=np.array(b.coords[0])-a;u/=max(np.linalg.norm(u),1e-12)
            states.append(support(jac(a,i).T@u,i))
        add('各芯片趋近封装边界',np.array(states),'每颗芯片最近封装边界的支撑方向')
        self.total=len(self.items)
    def __getitem__(self,index):return self.items[index]
    def __len__(self):return self.total
    def page(self,page,size=64):return list(enumerate(self.items))[page*size:(page+1)*size]
