"""Auditable source locations and discrete pose IDs for the zero-view risk labels."""
import math
import numpy as np


def channel_witness(row,index):
    demand=row['minimum_um'] if row['status']=='宽度不足' else row['keep_um']
    return dict(kind='channel',index=int(index),nominal_ends=row['nominal_witness_um'],probability_key=row.get('probability_key'),
                original=row['original'],required=row['required'],current=row['current'],
                narrowing_um=row['narrowing_um'],gap_um=row['gap_um'],pair=row['pair'],
                shortfall_um=demand-row['gap_um'])


def edge_witness(row,index,model,states):
    layer=row['layer'];node=model.nodes[model.ids[layer,row['indices'][0]]]
    owner=node['owner'];point=np.array(row['point'],float)
    if owner>=0 and model.roles[layer]=='运动RDL':
        dx,dy,angle=states[owner];pivot=model.bound.centers[owner]
        x,y=point-pivot-[dx,dy];cs,sn=math.cos(angle),math.sin(angle)
        point=pivot+[cs*x+sn*y,cs*y-sn*x]
    from .width_event_ids import edge_keys
    return dict(kind='keepout' if row.get('kind')=='禁布区侵入' else 'edge',index=int(index),nominal_point=point.tolist(),probability_keys=edge_keys(row),
                intrusion_area_um2=row.get('intrusion_area_um2',0.),
                gap_um=row['gap_um'],required_um=row['required_um'],
                shortfall_um=row['required_um']-row['gap_um'])


def pose_text(grid_key,index):
    """Decode the actual grid that produced the result, including locked axes."""
    digits=[0]*len(grid_key);number=int(index)+1
    for owner in reversed(range(len(grid_key))):
        local=np.frombuffer(grid_key[owner],dtype=float).reshape(-1,3)
        number,digits[owner]=divmod(number,len(local))
    text=[f'工况 {index+1}' if index>=0 else '工况 0（零位）']
    count=0
    for owner,digit in enumerate(digits):
        if not digit:continue
        dx,dy,angle=np.frombuffer(grid_key[owner],dtype=float).reshape(-1,3)[digit]
        text.append(f'DIE {owner+1}  DX {dx:+.12g} μm  DY {dy:+.12g} μm  θ {math.degrees(angle):+.12g}°')
        count+=1
    if count<len(digits):text.append(('其余 DIE' if count else '全部 DIE')+'  DX 0 μm  DY 0 μm  θ 0°')
    return '\n'.join(text)
