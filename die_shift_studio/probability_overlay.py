"""Via-area occupancy, using full rigid poses and footprint convolution.

Sampling is optional: deterministic mode integrates all translation grid cells
and all conditional-CDF Gauss-Legendre angle nodes. Continuous Gaussian states are not finitely
enumerable. Values are occupancy probabilities, never centre-mass histograms.
"""
import math
from functools import lru_cache
import numpy as np
import shapely
from scipy.special import ndtri,ndtr,roots_legendre
from scipy.signal import fftconvolve
from .acceleration import convolve
from .statistics import statistical_state,mean_and_bounds
from .distribution import normal_interval,normalization,bounded_poses
from .settings import ViewSettings
from .resources import parallel_map

def input_envelope(poly,center,p,count):
    """Conservative finite-input swept hull, explicitly not a probability contour."""
    from shapely.geometry import MultiPoint
    from .preview import rigid_transform
    mu,b=mean_and_bounds(p,count)
    points=np.asarray(poly.exterior.coords);r=float(np.linalg.norm(points-center,axis=1).max())
    n=max(2,min(257,int(math.ceil(2*b[2]/.002))+1))
    angles=np.linspace(mu[2]-b[2],mu[2]+b[2],n)
    moved=np.concatenate([rigid_transform(points,center,[mu[0],mu[1],a]) for a in angles])
    corners=np.array([[-b[0],-b[1]],[-b[0],b[1]],[b[0],-b[1]],[b[0],b[1]]])
    hull=MultiPoint((moved[:,None,:]+corners).reshape(-1,2)).convex_hull
    # Arc sagitta between adjacent angular samples bounds continuous rotation.
    tolerance=r*(1-math.cos(b[2]/max(1,n-1)))
    return hull.buffer(tolerance/math.cos(math.pi/32),quad_segs=8) if tolerance else hull


def _translation_kernel(cov,cell,tail,order,hard_k=None):
    """Deterministic Gaussian rectangle probabilities on a zero-centred lattice."""
    sx,sy=np.sqrt(np.maximum(np.diag(cov)[:2],0))
    if (2*tail*sx/cell+3)*(2*tail*sy/cell+3)>4_000_000:raise ValueError('平移概率网格超过400万格，请增大概率网格尺寸')
    nx=int(math.ceil(tail*sx/cell));ny=int(math.ceil(tail*sy/cell))
    xe=(np.arange(-nx,nx+2)-.5)*cell;ye=(np.arange(-ny,ny+2)-.5)*cell
    if hard_k is not None:
        if sx:xe=np.clip(xe,-hard_k*sx,hard_k*sx)
        if sy:ye=np.clip(ye,-hard_k*sy,hard_k*sy)
    if sx==0 or sy==0:
        x=normal_interval(xe[:-1]/sx,xe[1:]/sx) if sx else np.array([1.])
        y=normal_interval(ye[:-1]/sy,ye[1:]/sy) if sy else np.array([1.])
        return np.outer(y,x)
    rho=float(np.clip(cov[0,1]/sx/sy,-1,1))
    if abs(rho)<1e-14:return np.outer(normal_interval(ye[:-1]/sy,ye[1:]/sy),normal_interval(xe[:-1]/sx,xe[1:]/sx))
    if abs(rho)==1:
        a,b=ye[:-1]/sy,ye[1:]/sy
        if rho<0:a,b=-b,-a
        low=np.maximum(a[:,None],xe[:-1]/sx);high=np.minimum(b[:,None],xe[1:]/sx)
        return normal_interval(low,high)
    # Integrate conditional Y CDF across every X bin; no random CDF solver.
    nodes,weights=roots_legendre(min(512,max(order,int(math.ceil(8/math.sqrt(1-rho*rho))))))
    width=np.diff(xe)[:,None]
    x=(xe[:-1,None]+xe[1:,None])/2+width/2*nodes
    mass=weights[None,:]*np.exp(-.5*(x/sx)**2)/(math.sqrt(2*math.pi)*sx)*width/2
    conditional=(ye[:,None,None]-rho*sy/sx*x[None,:,:])/(sy*math.sqrt(1-rho*rho))
    return np.maximum(0,np.sum(normal_interval(conditional[:-1],conditional[1:])*mass[None,:,:],axis=2))


def _footprint(poly,angle,cell,sub):
    from shapely.affinity import rotate,translate
    center=poly.centroid
    local=rotate(translate(poly,-center.x,-center.y),angle,origin=(0,0),use_radians=True)
    x0,y0,x1,y1=local.bounds
    nx=int(math.ceil(max(abs(x0),abs(x1))/cell+.5));ny=int(math.ceil(max(abs(y0),abs(y1))/cell+.5))
    # Exact polygon/cell intersection on boundary cells; interior cells are 1.
    # This also preserves holes and area without subpixel sampling artefacts.
    if (2*nx+1)*(2*ny+1)>4_000_000:raise ValueError('过孔实体栅格过大，请增大概率网格尺寸')
    xs=np.arange(-nx,nx+1)*cell;ys=np.arange(-ny,ny+1)*cell
    result=np.zeros((len(ys),len(xs)))
    for start in range(0,len(ys),128):
        xx,yy=np.meshgrid(xs,ys[start:start+128])
        cells=shapely.box(xx-cell/2,yy-cell/2,xx+cell/2,yy+cell/2)
        values=shapely.intersects_xy(local,xx,yy).astype(float)
        boundary=shapely.intersects(local.boundary,cells)
        values[boundary]=shapely.area(shapely.intersection(cells[boundary],local))/(cell*cell)
        result[start:start+len(yy)]=np.clip(values,0,1)
    return result



def _deposit(shape,points,weights,origin,cell):
    """Linear deposition limits centre rounding to one pixel; weights conserved."""
    out=np.zeros(shape,float);uv=(points-origin)/cell;base=np.floor(uv).astype(int);fraction=uv-base
    for dy in (0,1):
        for dx in (0,1):
            ix=base[:,0]+dx;iy=base[:,1]+dy
            w=weights*(fraction[:,0] if dx else 1-fraction[:,0])*(fraction[:,1] if dy else 1-fraction[:,1])
            valid=(ix>=0)&(ix<shape[1])&(iy>=0)&(iy<shape[0])
            np.add.at(out,(iy[valid],ix[valid]),w[valid])
    if not np.isclose(out.sum(),np.sum(weights),rtol=1e-11,atol=1e-13):raise ValueError('概率中心网格越界：质量未守恒，已拒绝显示不完整概率云')
    return out


def probability_maps(cache,bound,roles,p,exponent=12,masks=None,settings=None,progress=lambda *_:None):
    if settings and settings.probability_show_ranges:
        from dataclasses import replace
        p=replace(p,target_nsigma=0)
    if p.per_die_enabled:
        values=[];maps=[]
        for owner in range(len(bound.centers)):
            progress(round(100*owner/len(bound.centers)),f'概率云 DIE {owner+1}/{len(bound.centers)}')
            selected={k:np.asarray(mask)&(bound.owners[k]==owner) for k,mask in (masks or {}).items()}
            q=probability_maps(cache,bound,roles,p.for_die(owner),exponent,selected,settings,progress)
            values.append(dict(owner=owner,cell_um=q['cell_um'],sample_standard_deviations=q['sample_standard_deviations']))
            maps.extend(q['maps'])
        q.update(maps=maps,per_die=values);return q
    return _probability_maps(cache,bound,roles,p,exponent,masks,settings,progress)


def _probability_maps(cache,bound,roles,p,exponent=12,masks=None,settings=None,progress=lambda *_:None):
    cfg=settings or ViewSettings(sample_exponent=exponent)
    mu,cov,_=statistical_state(p,len(bound.centers));eligible=[];radii=[];sizes=[]
    for key in bound.keys:
        if roles.get(key)!='运动过孔':continue
        for index,(point,owner) in enumerate(zip(cache.layers[key]['centers'],bound.owners[key])):
            if owner<0 or masks is None or not masks.get(key,np.zeros(len(bound.owners[key]),bool))[index]:continue
            poly=cache.layers[key]['geometry'][index];radius=np.linalg.norm(np.asarray(poly.exterior.coords)-point,axis=1).max()
            eligible.append((key,index,point,owner,poly,radius));radii.append(np.linalg.norm(point-bound.centers[owner]));sizes.append(radius*2)
    # Resolution follows physical input support and via size, not k-dependent
    # parent sigma. Lowering k changes probabilities, never the physical domain.
    _,hard=mean_and_bounds(p,len(bound.centers))
    reach=max(hard[0],hard[1],max(radii or [0])*min(math.pi,hard[2]))
    cell=cfg.probability_cell_um or max(cache.data.dbu_um,min(max(reach/32,.05),min(sizes or [1])/12),max(sizes or [0])/128,.05)
    deterministic=cfg.probability_method=='quadrature' or not np.any(hard);sigma_angle=math.sqrt(cov[2,2]);samples=0;angle_error=0.
    if deterministic:
        if sigma_angle:
            nodes,weights=roots_legendre(cfg.quadrature_order)
            # Integrate in conditional CDF coordinates: positive weights sum to
            # one even at low order / 12 sigma, avoiding area inflation.
            u=ndtr(-p.scale_nsigma)+(nodes+1)/2*float(normal_interval(-p.scale_nsigma,p.scale_nsigma))
            angles=mu[2]+sigma_angle*ndtri(u);weights=weights/2
        else:angles=np.array([mu[2]]);weights=np.array([1.])
        limit=p.scale_nsigma if math.isfinite(p.scale_nsigma) else 0.
        translation=_translation_kernel(cov,cell,limit,cfg.quadrature_order,hard_k=limit)
        zxy,_=normalization(p.scale_nsigma,p.rho_xy,cov[0,0]>0,cov[1,1]>0,False);translation/=zxy
        offsets=np.array([translation.shape[1]//2,translation.shape[0]//2])*cell
        groups=[(float(angle),float(weight)) for angle,weight in zip(angles,weights)]
    else:
        samples=2**cfg.sample_exponent
        poses=bounded_poses(p,len(bound.centers),cfg)
        if sigma_angle:
            span=float(np.ptp(poses[:,2]));max_radius=max(sizes or [0])/2
            bins=min(cfg.angle_bins,max(1,int(math.ceil(span*max_radius*4/cell))))
            edges=np.linspace(poses[:,2].min(),poses[:,2].max(),bins+1);ids=np.clip(np.searchsorted(edges,poses[:,2],side='right')-1,0,bins-1)
        else:ids=np.zeros(samples,int)
        groups=[]
        for i in np.unique(ids):
            mask=ids==i;angle=float(np.mean(poses[mask,2]));angle_error=max(angle_error,float(np.max(abs(poses[mask,2]-angle))))
            groups.append((angle,mask))
    @lru_cache(maxsize=128)
    def kernel(shape_bytes,angle):
        local=shapely.from_wkb(shape_bytes)
        result=_footprint(local,angle,cell,cfg.footprint_samples)
        return fftconvolve(result,translation,mode='full') if deterministic else result
    def one(item):
        key,index,point,owner,poly,radius=item;c=bound.centers[owner];a=point-c
        if deterministic:
            centers=c+mu[:2]+np.column_stack((a[0]*np.cos(angles)-a[1]*np.sin(angles),a[0]*np.sin(angles)+a[1]*np.cos(angles)))
            low=centers.min(axis=0)-offsets-radius-3*cell;high=centers.max(axis=0)+offsets+radius+3*cell
        else:
            angles_sample=poses[:,2];centers=c+poses[:,:2]+np.column_stack((a[0]*np.cos(angles_sample)-a[1]*np.sin(angles_sample),a[0]*np.sin(angles_sample)+a[1]*np.cos(angles_sample)))
            low=centers.min(axis=0)-radius-3*cell;high=centers.max(axis=0)+radius+3*cell
        envelope=input_envelope(poly,c,p,len(bound.centers))
        x0,y0,x1,y1=envelope.bounds;low=np.minimum(low,[x0-cell,y0-cell]);high=np.maximum(high,[x1+cell,y1+cell])
        origin=np.floor(low/cell)*cell;size=np.ceil((high-origin)/cell).astype(int)+1;shape=(int(size[1]),int(size[0]))
        if np.prod(shape)>4_000_000:raise ValueError(f'{key} 图形{index+1}概率网格超过400万格；请增大设置中的概率网格尺寸')
        grid=np.zeros(shape,float);area_integral=0.
        from shapely.affinity import translate
        shape_bytes=shapely.normalize(shapely.set_precision(translate(poly,-point[0],-point[1]),1e-9,mode='pointwise')).wkb
        for g,(angle,weight) in enumerate(groups):
            footprint=kernel(shape_bytes,angle)
            if deterministic:
                hist=_deposit(shape,centers[g:g+1],np.ones(1),origin,cell)
                hist*=weight
                area_integral+=float(weight)*float(footprint.sum())*cell*cell
            else:
                hist=_deposit(shape,centers[weight],np.full(int(weight.sum()),1/samples),origin,cell)
                area_integral+=float(weight.sum())/samples*float(footprint.sum())*cell*cell
            # Linear, zero-padded convolution: no periodic wrap into the
            # opposite edge. Domain includes support + footprint + raster guards.
            grid+=convolve(hist,footprint,mode='same',backend=cfg.acceleration)
        numerical_mass=float(grid.sum()*cell*cell)
        if abs(numerical_mass-area_integral)>max(1e-7,area_integral*1e-8):
            raise ValueError(f'{key} 图形{index+1}概率云边界质量丢失，已停止显示并保留旧结果')
        edge=float(max(abs(grid[0]).max(),abs(grid[-1]).max(),abs(grid[:,0]).max(),abs(grid[:,-1]).max()))
        if edge>1e-10 or grid.min() < -1e-10 or grid.max()>1+1e-8:
            raise ValueError(f'{key} 图形{index+1}概率卷积边缘或数值范围自检失败')
        grid[np.abs(grid)<1e-14]=0;grid=np.clip(grid,0,1).astype(np.float32)
        return dict(key=key,index=index,owner=int(owner),probability=grid,input_envelope=np.asarray(envelope.exterior.coords),
            nominal_polygon=poly,rotation_center=c.copy(),
            extent=[origin[0]-cell/2,origin[0]+(size[0]-.5)*cell,origin[1]-cell/2,origin[1]+(size[1]-.5)*cell],
            edge_probability=edge,convolution_mass_error=abs(numerical_mass-area_integral),cell_um=cell,via_area_um2=float(poly.area),integrated_area_um2=float(grid.sum(dtype=float)*cell*cell),
            raster_area_relative_error=abs(area_integral-poly.area)/poly.area,
            orientation_error_um=float(2*radius*math.sin(min(math.pi,angle_error)/2)))
    # GEOS vector operations and FFT release the GIL. Limit peak live grids.
    maps=parallel_map(one,eligible,progress=lambda done,total:progress(round(100*done/max(1,total)),f'过孔实体概率 {done}/{total}'))
    methods={'sobol':'Sobol准蒙特卡洛','monte_carlo':'蒙特卡洛','quadrature':'确定性高斯积分（全部角度节点×全部平移网格，无随机采样）'}
    return dict(maps=maps,cell_um=cell,samples=samples,method=methods[cfg.probability_method],
                quadrature_order=cfg.quadrature_order if deterministic else None,angle_groups=len(groups),
                sample_standard_deviations=None if deterministic else np.std(poses,axis=0).tolist(),
                translation_tail_bound=0. if deterministic else None,
                support='输入单边幅度硬边界；正态联合分布在该盒内截断归一化',
                definition='网格位置被过孔实体覆盖的概率；0—1；面积积分近似等于过孔面积，网格值之和不归一为1',
                accuracy='空间栅格和角度积分为数值近似；提高精度需细化网格并增加节点/样本复算。低于采样分辨率或数值精度的尾部不伪造色值；虚线输入包络独立标示全部极端。接触判定使用同一截断分布的概率界。')


def coverage_envelope(poly,center,p,count,level):
    """Conservative via swept hull for |Z_j-mu_j| <= min(B_j, level*sigma_j).

    Every displayed level uses the SAME distribution scale. This box-envelope
    includes the via area and rotation, and is not an equal-density contour or
    a claim of exact joint probability in correlated 3-D pose space.
    """
    from dataclasses import replace
    if math.isinf(level):
        # Infinity is the application's nominal binary mode, not infinite sigma.
        return poly
    if level==0:return input_envelope(poly,center,p,count)
    _,bounds=mean_and_bounds(p,count)
    scale=p.reference_nsigma if math.isinf(p.scale_nsigma) else p.scale_nsigma
    limit=bounds*min(1.,level/scale)
    q=replace(p,per_die_enabled=False,single_bound_x_um=limit[0],multi_bound_x_um=limit[0],
              single_bound_y_um=limit[1],multi_bound_y_um=limit[1],bound_angle_deg=math.degrees(limit[2]))
    return input_envelope(poly,center,q,count)
