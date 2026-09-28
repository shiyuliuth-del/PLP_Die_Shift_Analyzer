"""Saved numerical/display settings, independent of process acceptance parameters."""
from dataclasses import dataclass,asdict,fields
import math

@dataclass(frozen=True)
class ViewSettings:
    acceleration:str='auto'
    deformation_min_angle_deg:float=90.
    probability_method:str='sobol'
    sample_exponent:int=12
    random_seed:int=510
    quadrature_order:int=25
    tail_sigma:float=8.
    probability_cell_um:float=0.
    angle_bins:int=24
    footprint_samples:int=4
    probability_alpha:float=.42
    space_resolution:int=101
    space_cmap:str='pastel'
    space_interpolation:str='bilinear'
    space_alpha:float=.65
    intrusion_alpha:float=.52
    probability_cmap:str='blue'
    probability_interpolation:str='nearest'
    space_scope:str='moving'
    animation_enabled:bool=False
    animation_duration_ms:int=1000
    animation_steps:int=12  # legacy project field, ignored by V9 animation
    animation_fps:int=30
    animation_speed:int=3
    camera_speed:int=4
    contact_box_budget:int=2048
    deformation_continuity:str="C2"
    deformation_method:str="bezier"
    deformation_tolerance_um:float=.05
    deformation_arc_angle_deg:float=30.

    @classmethod
    def from_dict(cls,values=None):
        known={f.name for f in fields(cls)};q=cls(**{k:v for k,v in (values or {}).items() if k in known})
        choices={'acceleration':('auto','cpu','cuda'),'probability_method':('sobol','monte_carlo','quadrature'),
                 'space_cmap':('pastel','YlGnBu','BuPu','viridis','cividis'),
                 'space_interpolation':('nearest','bilinear','bicubic'),
                 'probability_interpolation':('nearest','bilinear','bicubic'),
                 'probability_cmap':('blue','YlGnBu','BuPu','viridis','cividis'),
                 'space_scope':('moving','package'),'deformation_continuity':('C1','C2'),'deformation_method':('bezier','arc')}
        for key,allowed in choices.items():
            if getattr(q,key) not in allowed:raise ValueError('无效设置：'+key)
        limits={'deformation_min_angle_deg':(1,179),'deformation_arc_angle_deg':(1,120),'deformation_tolerance_um':(.001,1.),'sample_exponent':(8,18),'random_seed':(0,2147483647),'quadrature_order':(5,81),
                'tail_sigma':(4,10),'probability_cell_um':(0,1000),'angle_bins':(4,128),
                'footprint_samples':(1,8),'space_resolution':(31,401),
                'animation_fps':(10,120),'animation_speed':(1,5),'camera_speed':(1,5),'contact_box_budget':(128,32768),'animation_duration_ms':(80,3000),'animation_steps':(2,60),'probability_alpha':(.05,.95),'space_alpha':(.05,.95),'intrusion_alpha':(.05,.85)}
        integers={'animation_fps','animation_speed','camera_speed','contact_box_budget','sample_exponent','random_seed','quadrature_order','angle_bins','footprint_samples','space_resolution','animation_duration_ms','animation_steps'}
        for key,(lo,hi) in limits.items():
            v=getattr(q,key)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi or (key in integers and int(v)!=v):
                raise ValueError(f'设置 {key} 超出范围 {lo}—{hi}')
        if not isinstance(q.animation_enabled,bool):raise ValueError('动画开关必须为布尔值')
        data=asdict(q)
        for key in integers:data[key]=int(data[key])
        return cls(**data)

    def numeric_key(self):
        return tuple(getattr(self,k) for k in ('acceleration','probability_method','sample_exponent','random_seed',
            'quadrature_order','probability_cell_um','angle_bins','space_resolution','contact_box_budget'))
