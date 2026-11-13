"""Saved numerical/display settings, independent of process acceptance parameters."""
from dataclasses import dataclass,asdict,fields,field
import math


ELEMENTS={'固定RDL':'原始固定 RDL 线路','运动RDL':'原始运动 RDL 线路','固定过孔':'固定过孔实体','运动过孔':'运动过孔实体',
          '封装边框':'封装边框','芯片边框':'芯片边框','其他':'其他 GDS 图形','运动过孔孔盘':'运动孔盘',
          '固定过孔孔盘':'固定孔盘','固定障碍':'整块固定障碍','禁布区':'禁布区','截面':'通道矩形','描边':'风险线路描边',
          '标注':'文字和箭头','边距':'封装边缘与禁布区违规'}
DEFAULTS={k:'dim' if k in ('固定RDL','运动RDL') else 'show' for k in ELEMENTS}


def normalize_display(values):
    if not isinstance(values,dict):raise ValueError('宽度分析显示设置必须是对象')
    result={k:values.get(k,v) for k,v in DEFAULTS.items()}
    if any(v not in ('show','dim','hide') for v in result.values()):raise ValueError('宽度分析元素仅支持显示、低亮或隐藏')
    return result


@dataclass(frozen=True)
class ViewSettings:
    width_display:dict=field(default_factory=lambda:dict(DEFAULTS),hash=False)
    width_label_font_pt:float=8.
    width_label_decimals:int=2
    width_yield_decimals:int=6
    width_yield_method:str='grid'
    width_show_probability:bool=True
    width_yield_exponent:int=12
    width_yield_seed:int=510
    width_grid_nodes:int=5
    show_contact_markers:bool=False
    show_low_contact:bool=False
    show_low_width:bool=True
    cache_max_entries:int=2000
    acceleration:str='auto'
    drc_min_angle_deg:float=90.
    drc_tolerance_um:float=0.
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
    show_annulus_range:bool=True
    show_spacing_range:bool=True
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
    probability_gamma:float=.35
    probability_interpolation:str='nearest'
    space_scope:str='moving'
    animation_enabled:bool=True
    animation_return_zero:bool=True
    animation_strategy:str='prepared'
    animation_duration_ms:int=1000
    animation_steps:int=12  # legacy project field, ignored by V9 animation
    animation_fps:int=30
    animation_speed:int=3
    camera_speed:int=4
    contact_box_budget:int=2048
    repair_landing_pad:bool=False
    repair_patch_width_um:float=0.
    reconstruction_regions:tuple=()

    # 重构预演初始参数
    # Manual cut planes, one per chain, written as
    # (layer, feature index, (via indices,), nx, ny, ox, oy).
    reconstruction_cuts:tuple=()
    reconstruction_extra_cuts:tuple=()
    reconstruction_second_cuts:tuple=()
    reconstruction_dropped_cuts:tuple=()
    reconstruction_enabled:bool=False
    reconstruction_live:bool=True
    reconstruction_style:str="c2"
    reconstruction_boundary_mode:str="perpendicular"
    reconstruction_ignored:tuple=()
    reconstruction_grid_nodes:int=3
    reconstruction_intervals:tuple=()  # layer,index,owner,via refs,route fingerprint,s0,s1
    reconstruction_corner_guard_um:float=3.0
    yield_method:str='monte_carlo'
    yield_sample_exponent:int=12
    yield_box_budget:int=4096
    yield_tolerance:float=.001
    safe_box_precision:float=.01
    safe_box_budget:int=512
    safe_box_starts:int=3
    safe_box_passes:int=2

    @staticmethod
    def _normalize_cuts(rows):
        cuts={}
        if not isinstance(rows,(list,tuple)):return ()
        for row in rows:
            try:
                layer,index,vias,nx,ny,ox,oy=row
                index=int(index)
                if all(isinstance(v,(list,tuple)) for v in vias):
                    refs=tuple(sorted((str(key),int(i)) for key,i in vias))
                else:
                    refs=tuple(sorted(int(i) for i in vias))
                nx,ny,ox,oy=map(float,(nx,ny,ox,oy))
                normal=math.hypot(nx,ny)
                if not refs or index<0 or not normal or not all(map(math.isfinite,(normal,ox,oy))):continue
            except (TypeError,ValueError,OverflowError):
                continue
            cuts[str(layer),index,refs]=(nx/normal,ny/normal,ox,oy)
        return tuple((*key,*plane) for key,plane in cuts.items())

    @staticmethod
    def _normalize_regions(rows):
        """Normalize finite manual boxes, preserving override order."""
        if not isinstance(rows,(list,tuple)):return ()
        regions=[]
        for row in rows:
            try:
                layer,bounds,role=row
                x0,y0,x1,y1=map(float,bounds)
                if role not in ('move','stay') or not all(map(math.isfinite,(x0,y0,x1,y1))):continue
                if x0==x1 or y0==y1:continue
            except (TypeError,ValueError,OverflowError):continue
            regions.append((str(layer),(min(x0,x1),min(y0,y1),max(x0,x1),max(y0,y1)),role))
        return tuple(regions)

    @staticmethod
    def _normalize_dropped(rows):
        if not isinstance(rows,(list,tuple)):return ()
        normalized=[]
        for row in rows:
            try:
                # Older files may contain a full cut plane rather than an identity.
                layer,index,vias=row[:3]
                cut=ViewSettings._normalize_cuts(((layer,index,vias,1.,0.,0.,0.),))
                if cut and cut[0][:3] not in normalized:normalized.append(cut[0][:3])
            except (TypeError,ValueError,IndexError):continue
        return tuple(normalized)

    @classmethod
    def from_dict(cls,values=None):
        values=dict(values or {})
        values.setdefault('drc_min_angle_deg',values.get('deformation_min_angle_deg',90.))
        intervals=[]
        for row in values.get('reconstruction_intervals',()):
            try:
                layer,index,owner,refs,fingerprint,a,b=row
                refs=tuple(sorted((str(k),int(i)) for k,i in refs))
                a,b=float(a),float(b)
                if refs and math.isfinite(a) and math.isfinite(b) and 0<=a<b:
                    intervals.append((str(layer),int(index),int(owner),refs,str(fingerprint),a,b))
            except (TypeError,ValueError):continue
        values['reconstruction_intervals']=tuple(intervals)
        refs=values.get('reconstruction_ignored',())
        if not isinstance(refs,(list,tuple)) or any(not isinstance(r,(list,tuple)) or len(r)!=2 or not isinstance(r[0],str) or type(r[1]) is not int or r[1]<0 for r in refs):raise ValueError('忽略重构图形编号无效')
        values['reconstruction_ignored']=tuple(sorted(set(tuple(r) for r in refs)))
        if 'reconstruction_cuts' in values:
            values['reconstruction_cuts']=cls._normalize_cuts(values['reconstruction_cuts'])
        for key in ('reconstruction_extra_cuts','reconstruction_second_cuts'):
            if key in values:
                values[key]=cls._normalize_cuts(values[key])
        values['reconstruction_regions']=cls._normalize_regions(values.get('reconstruction_regions',()))
        values['reconstruction_dropped_cuts']=cls._normalize_dropped(values.get('reconstruction_dropped_cuts',()))
        values['width_display']=normalize_display(values.get('width_display',{}))
        for new,old in (('width_yield_exponent','yield_sample_exponent'),('width_yield_seed','random_seed')):
            if new not in values and old in values:values[new]=values[old]
        if 'width_yield_method' not in values and values.get('yield_method')=='partition':values['width_yield_method']='grid'
        known={f.name for f in fields(cls)};q=cls(**{k:v for k,v in (values or {}).items() if k in known})
        choices={'reconstruction_boundary_mode':('perpendicular','wide','balanced'),'reconstruction_style':('linear','c1','c2','arc'),'width_yield_method':('importance','grid'),'yield_method':('monte_carlo','partition'),'acceleration':('auto','cpu','cuda'),'probability_method':('sobol','monte_carlo','quadrature'),
                 'space_cmap':('pastel','YlGnBu','BuPu','viridis','cividis'),
                 'space_interpolation':('nearest','bilinear','bicubic'),
                 'probability_interpolation':('nearest','bilinear','bicubic'),
                 'probability_cmap':('blue','teal','amber','rose','YlGnBu','BuPu','viridis','cividis','plasma','magma','turbo'),
                 'animation_strategy':('prepared','stream'),
                 'space_scope':('moving','package')}
        for key,allowed in choices.items():
            if getattr(q,key) not in allowed:raise ValueError('无效设置：'+key)
        limits={'width_yield_decimals':(0,12),'width_label_decimals':(0,6),'width_label_font_pt':(6,24),'drc_tolerance_um':(-10000,10000),'probability_gamma':(.1,1.),'contact_marker_size':(.1,10000),'unadapted_marker_size':(.1,10000),'drc_min_angle_deg':(1,179),'sample_exponent':(8,18),'random_seed':(0,2147483647),'quadrature_order':(5,81),
                'tail_sigma':(4,10),'probability_cell_um':(0,1000),'angle_bins':(4,128),
                'footprint_samples':(1,8),'space_resolution':(31,401),
                'animation_fps':(10,120),'animation_speed':(1,5),'camera_speed':(1,5),'contact_box_budget':(128,32768),'animation_duration_ms':(80,3000),'animation_steps':(2,60),'probability_alpha':(.05,.95),'space_alpha':(.05,.95),'intrusion_alpha':(.05,.85)}
        integers={'width_yield_decimals','width_label_decimals','animation_fps','animation_speed','camera_speed','contact_box_budget','sample_exponent','random_seed','quadrature_order','angle_bins','footprint_samples','space_resolution','animation_duration_ms','animation_steps'}
        limits.update(width_yield_exponent=(8,20),width_yield_seed=(0,2147483647),width_grid_nodes=(3,21))
        limits.update(reconstruction_grid_nodes=(3,9),reconstruction_corner_guard_um=(0,1000),cache_max_entries=(10,100000),yield_sample_exponent=(8,20),yield_box_budget=(64,65536),yield_tolerance=(.000001,.1),

                      safe_box_precision=(.0001,.1),
                      safe_box_budget=(32,32768),safe_box_starts=(1,6),safe_box_passes=(1,8),repair_patch_width_um=(0,10000))
        integers.update(('reconstruction_grid_nodes','width_yield_exponent','width_yield_seed','width_grid_nodes','cache_max_entries','yield_sample_exponent','yield_box_budget','safe_box_budget','safe_box_starts','safe_box_passes'))
        for key,(lo,hi) in limits.items():
            v=getattr(q,key)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi or (key in integers and int(v)!=v):
                raise ValueError(f'设置 {key} 超出范围 {lo}—{hi}')
        for key in ('show_contact_markers','reconstruction_live','width_show_probability','show_annulus_range','show_spacing_range','show_low_contact','show_low_width','animation_enabled','animation_return_zero','probability_show_ranges','contact_marker_absolute','unadapted_marker_absolute','repair_landing_pad'):
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

    def width_key(self):
        return (True,'nominal-physical-throat-v36',self.drc_tolerance_um)

    def numeric_key(self):
        return tuple(getattr(self,k) for k in ('acceleration','probability_method','sample_exponent','random_seed',
            'quadrature_order','probability_cell_um','angle_bins','space_resolution','contact_box_budget','safe_box_precision','safe_box_budget','safe_box_starts','safe_box_passes','yield_method','yield_sample_exponent','yield_box_budget','yield_tolerance'))

    def reconstruction_key(self):
        """Everything the reconstruction result depends on besides the pose."""
        return (True,'nominal-route-partition-v40',self.reconstruction_style,self.reconstruction_boundary_mode,self.reconstruction_ignored,self.drc_tolerance_um,
                self.reconstruction_corner_guard_um,tuple(self.reconstruction_intervals),
                tuple(self.reconstruction_regions),tuple(self.reconstruction_cuts),
                tuple(self.reconstruction_extra_cuts),tuple(self.reconstruction_second_cuts),
                tuple(self.reconstruction_dropped_cuts))
    def deformation_key(self):
        return ('retired-v38',)
