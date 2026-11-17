"""Pure data contract for saved view measurements."""
import numpy as np

def normalize(rows):
    if not isinstance(rows,(list,tuple)) or len(rows)>10000:raise ValueError('测量记录须为有限线段列表')
    out=[]
    for row in rows:
        xy=np.asarray(row,float)
        if xy.shape!=(2,2) or not np.isfinite(xy).all() or np.max(abs(xy))>1e12:raise ValueError('标尺端点须为有限XY坐标')
        out.append(xy.tolist())
    return out

