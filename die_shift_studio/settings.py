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
    probability_show_ranges:bool=False
    probability_ranges:tuple=()
    probability_target_color:str="#bf526d"
    contact_marker_color:str="#bc243e"
    contact_marker_size:float=5.0
    contact_marker_absolute:bool=False
    unadapted_marker_color:str="#ba7628"
    unadapted_marker_size:float=7.0
    unadapted_marker_absolute:bool=False
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
    deformation_iterations:int=6
    deformation_local_steps:int=6
    deformation_growth:float=1.6
    deformation_route_enabled:bool=True
    deformation_route_candidates:int=32
    deformation_general_bands:bool=True
    deformation_min_length:float=3.
    deformation_corner_guard:float=.5
    deformation_joint_rounds:int=6
    deformation_execution:str='auto'
    deformation_workers:int=0
    yield_method:str='monte_carlo'
    yield_sample_exponent:int=12
    yield_box_budget:int=4096
    yield_tolerance:float=.001
    safe_box_precision:float=.01
    safe_box_budget:int=512
    safe_box_starts:int=3
    safe_box_passes:int=2

    @classmethod
    def from_dict(cls,values=None):
        known={f.name for f in fields(cls)};q=cls(**{k:v for k,v in (values or {}).items() if k in known})
        choices={'yield_method':('monte_carlo','partition'),'acceleration':('auto','cpu','cuda'),'probability_method':('sobol','monte_carlo','quadrature'),
                 'space_cmap':('pastel','YlGnBu','BuPu','viridis','cividis'),
                 'space_interpolation':('nearest','bilinear','bicubic'),
                 'probability_interpolation':('nearest','bilinear','bicubic'),
                 'probability_cmap':('blue','YlGnBu','BuPu','viridis','cividis'),
                 'space_scope':('moving','package'),'deformation_continuity':('C1','C2'),'deformation_method':('bezier','arc'),
                 'deformation_execution':('auto','serial','thread','process')}
        for key,allowed in choices.items():
            if getattr(q,key) not in allowed:raise ValueError('无效设置：'+key)
        limits={'contact_marker_size':(.1,10000),'unadapted_marker_size':(.1,10000),'deformation_min_angle_deg':(1,179),'deformation_arc_angle_deg':(1,120),'deformation_tolerance_um':(.001,1.),'sample_exponent':(8,18),'random_seed':(0,2147483647),'quadrature_order':(5,81),
                'tail_sigma':(4,10),'probability_cell_um':(0,1000),'angle_bins':(4,128),
                'footprint_samples':(1,8),'space_resolution':(31,401),
                'animation_fps':(10,120),'animation_speed':(1,5),'camera_speed':(1,5),'contact_box_budget':(128,32768),'animation_duration_ms':(80,3000),'animation_steps':(2,60),'probability_alpha':(.05,.95),'space_alpha':(.05,.95),'intrusion_alpha':(.05,.85)}
        integers={'animation_fps','animation_speed','camera_speed','contact_box_budget','sample_exponent','random_seed','quadrature_order','angle_bins','footprint_samples','space_resolution','animation_duration_ms','animation_steps'}
        limits.update(yield_sample_exponent=(8,20),yield_box_budget=(64,65536),yield_tolerance=(.000001,.1),deformation_iterations=(1,64),deformation_local_steps=(1,12),deformation_growth=(1.1,3.),
                      deformation_route_candidates=(1,256),deformation_min_length=(1,20),deformation_corner_guard=(0,3),
                      deformation_joint_rounds=(0,32),deformation_workers=(0,16),safe_box_precision=(.0001,.1),
                      safe_box_budget=(32,32768),safe_box_starts=(1,6),safe_box_passes=(1,8))
        integers.update(('yield_sample_exponent','yield_box_budget','safe_box_budget','safe_box_starts','safe_box_passes','deformation_iterations','deformation_local_steps','deformation_route_candidates','deformation_joint_rounds','deformation_workers'))
        for key,(lo,hi) in limits.items():
            v=getattr(q,key)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi or (key in integers and int(v)!=v):
                raise ValueError(f'设置 {key} 超出范围 {lo}—{hi}')
        for key in ('animation_enabled','probability_show_ranges','contact_marker_absolute','unadapted_marker_absolute','deformation_route_enabled','deformation_general_bands'):
            if not isinstance(getattr(q,key),bool):raise ValueError(key+'必须为布尔值')
        import re
        color=lambda v:isinstance(v,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',v)
        for key in ('contact_marker_color','unadapted_marker_color','probability_target_color'):
            if not color(getattr(q,key)):raise ValueError('颜色须为 #RRGGBB')
        if not isinstance(q.probability_ranges,(list,tuple)) or any(not isinstance(row,(list,tuple)) or len(row)!=2 or row[0] not in range(1,13) or not color(row[1]) for row in q.probability_ranges):raise ValueError('覆盖范围须为1～12整数与颜色')
        data=asdict(q)
        for key in integers:data[key]=int(data[key])
        data["probability_ranges"]=tuple((int(level),color) for level,color in q.probability_ranges)
        return cls(**data)

    def numeric_key(self):
        return tuple(getattr(self,k) for k in ('acceleration','probability_method','sample_exponent','random_seed',
            'quadrature_order','probability_cell_um','angle_bins','space_resolution','contact_box_budget','safe_box_precision','safe_box_budget','safe_box_starts','safe_box_passes','yield_method','yield_sample_exponent','yield_box_budget','yield_tolerance'))

    def deformation_key(self):
        return tuple(getattr(self,f.name) for f in fields(self) if f.name.startswith('deformation_'))
