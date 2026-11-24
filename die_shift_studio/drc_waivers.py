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
        witnesses=set(record['witnesses'])
        retained=record.get('retained',[])
        events=record.get('events',{})
        sources=record.get('source_requirements',[])
        if (not isinstance(retained,(list,tuple)) or not all(map(hex_id,retained)) or not witnesses.issuperset(retained)
                or not isinstance(events,dict) or not witnesses.issuperset(events) or not all(map(hex_id,events.values()))
                or not isinstance(sources,(list,tuple))):
            raise ValueError('DRC忽略记录的事件或来源无效')
        for row in sources:
            if (not isinstance(row,(list,tuple)) or len(row)!=3 or not isinstance(row[0],str)
                    or type(row[1]) is not int or row[1]<0 or not isinstance(row[2],(list,tuple))
                    or not row[2] or not all(map(hex_id,row[2])) or not witnesses.issuperset(row[2])):
                raise ValueError('DRC忽略记录的图形来源无效')
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


def record(frame,rows,old,selected,context,index,automatic=()):
    identities=[witness_id(row) for row in rows]
    witnesses=sorted(set(identities))
    ignored=set(old.get('ignored',()))&set(witnesses)
    retained=set(old.get('retained',()))&set(witnesses)
    selected_id=witness_id(selected)
    if event_id(selected) in automatic:
        retained.symmetric_difference_update((selected_id,))
        ignored.discard(selected_id)
    else:ignored.symmetric_difference_update((selected_id,))
    groups=source_requirements(frame.get('reconstruction',{}),rows,identities)
    return dict(context=context,index=index,hard_failure=bool(frame.get('reconstruction',{}).get('hard_failures')),
                witnesses=witnesses,ignored=sorted(ignored),events={key:event_id(row) for key,row in zip(identities,rows)},
                retained=sorted(retained),source_requirements=[(*ref,keys) for ref,keys in sorted(groups.items())])


def accepted_records(waivers,context,grid,states_at):
    """Check the saved full-frame evidence against the current analysis."""
    for pose,row in waivers.items():
        index=row['index']
        if index<-1 or index>=grid.total or row['context']!=context:
            continue
        if pose==pose_id(states_at(index)):
            yield index,row


def approved(waivers,context,grid,states_at):
    return frozenset(index for index,row in accepted_records(waivers,context,grid,states_at)
                     if not row['hard_failure'] and row['ignored']==row['witnesses'])


def refs(row):
    raw=row.get('refs',())
    if not raw and row.get('layer'):
        raw=((row['layer'],i) for i in row.get('indices',()))
    return tuple(sorted({(str(key),int(index)) for key,index in raw}))


def event_id(row):
    """One rule on the same source objects, across different poses."""
    sources=refs(row)
    return sha256(repr((row['kind'],sources,row.get('source_layer'),row.get('source_index'),
                        tuple(map(int,row.get('dependencies',()))) if not sources else ())).encode()).hexdigest()


def waivable(row):
    if row.get('connection_failure') or row.get('connection') or row.get('short_circuit'):return False
    if (pair:=row.get('_measurement_pair')) is not None and pair[0].intersects(pair[1]):return False
    from .drc_policy import category
    return category(row['kind']) not in (None,'keepout')


def source_requirements(reconstruction,rows,ids):
    """Only clear a figure when the table covers each of its current violations."""
    from collections import defaultdict
    from .drc_policy import category
    from .reconstruction_events import zone_severity

    all_rows=defaultdict(list)
    for row,identity in zip(rows,ids):
        for ref in refs(row):all_rows[ref].append((row,identity))
    grouped=defaultdict(list)
    blocked={tuple(ref) for ref,_ in reconstruction.get('hard_failures',())}
    for piece in reconstruction.get('pieces',()):
        grouped[piece['layer'],int(piece['index'])].append(piece)
    tol=float(reconstruction.get('dbu_um',.001))
    output={}
    for ref,pieces in grouped.items():
        if ref in blocked or any(p.get('connection_failed') for p in pieces):continue
        if not any(zone_severity(p)=='yellow' for p in pieces):continue
        relevant=all_rows.get(ref,())
        if not relevant:continue
        # A native witness may be clipped to A; the parent row can include the
        # adjoining gap. Compare rule class and the actual geometry, not centres.
        local=[v for p in pieces for v in p.get('rule_witnesses',())
               if v.get('phase')!='original' and not v.get('connection')]
        if all(any(category(row['kind'])==category(w['kind'])
                   and row['geometry'].dwithin(w['geometry'],tol*2) for row,_ in relevant) for w in local):
            output[ref]=tuple(sorted({identity for _,identity in relevant}))
    return output


def cleared_sources(frame,rows,ignored):
    waived={i for i,flag in enumerate(ignored) if flag}
    if not waived:return frozenset()
    needed=source_requirements(frame.get('reconstruction',{}),rows,range(len(rows)))
    return frozenset(ref for ref,requirements in needed.items() if waived.issuperset(requirements))


def scene_events(solver,states,reconstruction):
    """Collect compact source/rule events from the same copper as the table."""
    from .drc import GlobalDRC
    if not hasattr(solver,'_waiver_drc'):solver._waiver_drc=GlobalDRC(solver.model,solver.p)
    rows=solver._waiver_drc.frame(states,reconstruction['replacements'],solver.settings)
    keys=[event_id(row) for row in rows]
    hard=bool(reconstruction.get('hard_failures'))
    protected={key for row,key in zip(rows,keys) if not waivable(row)}
    source=source_requirements(reconstruction,rows,keys)
    return tuple(sorted(set(keys))),tuple(sorted(protected)),source,hard


def automatic_ignores(settings,bank):
    if not settings.reconstruction_auto_waive_drc or not bank or not bank.get('complete'):
        return frozenset()
    threshold=settings.reconstruction_waiver_mantissa*10.**settings.reconstruction_waiver_exponent
    return frozenset(key for key,upper in bank.get('drc_event_probability',{}).items() if upper<threshold)


def summary(settings,bank,waivers,context,grid,states_at):
    """Per-figure and per-pose exclusions for the visible catalogue."""
    from collections import defaultdict

    auto=automatic_ignores(settings,bank)
    allowed=set()
    cleared=defaultdict(set)
    if auto:
        for index,keys in bank['drc_event_poses'].items():
            if auto.issuperset(keys):allowed.add(index)
        for ref,rows in bank['drc_event_sources'].items():
            cleared[ref].update(index for index,keys in rows.items() if auto.issuperset(keys))
    for index,row in accepted_records(waivers,context,grid,states_at):
        ignored=set(row['ignored'])
        ignored.update(key for key,event in row.get('events',{}).items() if event in auto)
        ignored.difference_update(row.get('retained',()))
        if not row['hard_failure'] and ignored.issuperset(row['witnesses']):allowed.add(index)
        else:allowed.discard(index)
        for layer,number,keys in row.get('source_requirements',()):
            if ignored.issuperset(keys):cleared[layer,number].add(index)
            else:cleared[layer,number].discard(index)
    return auto,frozenset(allowed),{ref:frozenset(indices) for ref,indices in cleared.items() if indices}
