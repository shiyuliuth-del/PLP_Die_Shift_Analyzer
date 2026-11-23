"""Persist local DRC decisions without changing the underlying checks."""
from hashlib import sha256
import re

import numpy as np


def normalize(value=None):
    if value is None:return {}
    hex_id=lambda x: isinstance(x,str) and re.fullmatch(r'[a-f0-9]{64}',x) is not None
    if not isinstance(value,dict):raise ValueError('DRC忽略记录格式无效')
    for pose,record in value.items():
        if (not hex_id(pose) or not isinstance(record,dict) or not hex_id(record.get('context'))
                or type(record.get('index')) is not int or record['index']<-2
                or type(record.get('hard_failure')) is not bool
                or any(not isinstance(record.get(key),list) or not all(map(hex_id,record[key]))
                       for key in ('witnesses','ignored'))
                or not set(record['ignored'])<=set(record['witnesses'])):
            raise ValueError('DRC忽略记录的姿态或位置无效')
    return value


def pose_id(states):
    array=np.array(states,dtype='<f8',copy=True)
    array[array==0]=0.  # signed zero is the same physical pose
    return sha256(array.tobytes()).hexdigest()


def witness_id(row):
    # Keep separate locations of the same rule and include any participating
    # geometry refs. Millimicron-equivalent GEOS noise must not undo a choice.
    import shapely
    geometry=shapely.set_precision(row['geometry'],1e-6,mode='pointwise')
    identity=(row['kind'],tuple(sorted(row.get('layers',(row.get('layer',''),)))),
              tuple(sorted((key,int(index)) for key,index in row.get('refs',()))),
              tuple(map(int,row.get('indices',()))))
    return sha256(repr(identity).encode()+geometry.normalize().wkb).hexdigest()


def record(frame,rows,old,selected,context,index):
    witnesses=sorted({witness_id(row) for row in rows})
    ignored=set(old.get('ignored',()))&set(witnesses)
    ignored.symmetric_difference_update((witness_id(selected),))
    return dict(context=context,index=index,hard_failure=bool(frame.get('reconstruction',{}).get('hard_failures')),
                witnesses=witnesses,ignored=sorted(ignored))


def approved(waivers,context,grid,states_at):
    """Check the saved full-frame evidence against the current analysis."""
    effective=set()
    for pose,row in waivers.items():
        index=row['index']
        if (index<-1 or index>=grid.total or row['hard_failure'] or row['context']!=context
                or row['ignored']!=row['witnesses']):
            continue
        if pose==pose_id(states_at(index)):
            effective.add(index)
    return frozenset(effective)
