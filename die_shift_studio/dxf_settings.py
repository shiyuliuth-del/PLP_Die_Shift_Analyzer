"""Saved, output-only DXF controls. The imported top-cell model is always flat."""
from dataclasses import dataclass,fields
import math

@dataclass(frozen=True)
class DxfSettings:
    merge_layers:bool=True
    layer_scope:str='all'
    polygon_mode:str='outline'
    scale:float=1.
    separate_holes:bool=False
    show_fill:bool=False

    @classmethod
    def from_dict(cls,values=None):
        if values is not None and not isinstance(values,dict):raise ValueError('DXF输出参数必须是对象')
        names={f.name for f in fields(cls)};q=cls(**{k:v for k,v in (values or {}).items() if k in names})
        for key in ('merge_layers','separate_holes','show_fill'):
            if type(getattr(q,key)) is not bool:raise ValueError('DXF '+key+'必须为布尔值')
        if q.layer_scope not in ('all','visible','participating'):raise ValueError('无效DXF导出图层范围')
        if q.polygon_mode not in ('outline','hatch'):raise ValueError('无效DXF实体类型')
        if type(q.scale) not in (int,float) or not math.isfinite(q.scale) or not 1e-6<=q.scale<=1e6:raise ValueError('DXF输出比例须为0.000001至1000000')
        return q
