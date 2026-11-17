"""Complete, durable per-pose DRC catalogues with bounded IPC pages."""
import json,sqlite3
from pathlib import Path
import numpy as np

PAGE_SIZE=128


def catalogue(frame,case,model=None):
    from .current_drc import locations
    owners_by_net={}
    if model is not None:
        for node in model.nodes:
            if node['owner']>=0:owners_by_net.setdefault(node['net'],set()).add(int(node['owner'])+1)
    rows=[];origin=np.asarray(case['board'].bounds[:2])
    for i,q in enumerate(locations(frame.get('drc',()))):
        bounds=list(map(float,q['geometry'].bounds));point=list(map(float,q['point']))
        dies={int(o)+1 for o in q.get('dependencies',()) if o>=0}
        if q.get('owner',-1)>=0:dies.add(int(q['owner'])+1)
        if model is not None:
            for ref in q.get('refs',()):
                if tuple(ref) in model.ids:dies.update(owners_by_net.get(model.nodes[model.ids[tuple(ref)]]['net'],()))
        rows.append(dict(die_ids=sorted(dies) or [0],number=i+1,kind=q['kind'],layers=list(q.get('layers',(q.get('layer',''),))),
                         bounds_um=bounds,point_um=point,package_relative_bounds_um=(np.asarray(bounds)-np.tile(origin,2)).tolist(),
                         required_um=q.get('required_um'),required_deg=q.get('required_deg'),
                         refs=[list(v) for v in q.get('refs',())],connection_failure=bool(q.get('connection_failure'))))
    return rows


def place(details,position):
    if not details.get('drc_issues'):return details
    xy=np.asarray(position,float)
    return dict(details,drc_issues=[dict(q,top_cell_bounds_um=(np.asarray(q['package_relative_bounds_um'])+np.tile(xy,2)).tolist()) for q in details['drc_issues']])


def write(path,task_id,details):
    from .core import clean_json
    details=clean_json(details)
    rows=details.get('drc_issues')
    if rows is None:return details
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('pragma journal_mode=WAL')
        db.execute('create table if not exists issues(task_id text, ordinal integer, payload text, primary key(task_id,ordinal))')
        db.execute('create table if not exists catalogs(task_id text primary key, total integer, metadata text)')
        db.execute('delete from issues where task_id=?',(task_id,))
        db.executemany('insert into issues values(?,?,?)',((task_id,i,json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))) for i,row in enumerate(rows)))
        db.execute('insert or replace into catalogs values(?,?,?)',(task_id,len(rows),json.dumps({k:v for k,v in details.items() if k!='drc_issues'},ensure_ascii=False,allow_nan=False)))
    return dict(details,drc_issues=rows[:PAGE_SIZE],issues_complete=len(rows)<=PAGE_SIZE,
                issue_catalog=dict(schema=1,path=str(path.resolve()),task_id=task_id,total=len(rows),page_size=PAGE_SIZE,command='task.issues'))


def page(catalog,offset=0,limit=128):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=512:raise ValueError('offset须为非负整数，limit须为1～512')
    path=Path(catalog['path']).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30) as db:
        meta=db.execute('select total from catalogs where task_id=?',(catalog['task_id'],)).fetchone()
        if meta is None:raise ValueError('问题目录不存在')
        rows=[json.loads(row[0]) for row in db.execute('select payload from issues where task_id=? and ordinal>=? order by ordinal limit ?',(catalog['task_id'],offset,limit))]
    return dict(total=meta[0],offset=offset,items=rows,next_offset=offset+len(rows) if offset+len(rows)<meta[0] else None,complete=offset+len(rows)>=meta[0])
