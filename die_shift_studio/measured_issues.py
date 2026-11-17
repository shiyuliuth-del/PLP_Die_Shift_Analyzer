"""Complete, durable per-pose DRC catalogues with bounded IPC pages."""
import json,sqlite3,threading,time,os,weakref
from contextlib import closing
from pathlib import Path
import numpy as np

PAGE_SIZE=128
_LOCK_WAIT_SECONDS=30.
_gate_registry=weakref.WeakValueDictionary()
_gate_guard=threading.Lock()

class _Gate:
    def __init__(self):self.lock=threading.RLock()

def _gate(path):
    key=os.path.normcase(str(path.resolve()))
    with _gate_guard:
        gate=_gate_registry.get(key)
        if gate is None:gate=_Gate();_gate_registry[key]=gate
        return gate

def _retry_locked(operation,deadline):
    from .resources import cancel_check
    while True:
        cancel_check();left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('等待问题目录数据库锁超时；未提交本次事务，请稍后重试')
        try:return operation(min(.25,left))
        except sqlite3.OperationalError as exc:
            code=getattr(exc,'sqlite_errorcode',None)
            locked=(int(code)&255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) if code is not None else str(exc).lower() in ('database is locked','database table is locked','database schema is locked')
            if not locked:raise
            cancel_check()
            if time.monotonic()>=deadline:raise TimeoutError('等待问题目录数据库锁超时；未提交本次事务，请稍后重试') from exc
            time.sleep(min(.02,max(0.,deadline-time.monotonic())))



def catalogue(frame,case,model=None):
    from .current_drc import locations
    owners_by_net={}
    if model is not None:
        for node in model.nodes:
            if node['owner']>=0:owners_by_net.setdefault(node['net'],set()).add(int(node['owner'])+1)
    import shapely,math
    from shapely.affinity import rotate
    rows=[];origin=np.asarray(case['board'].bounds[:2])
    for i,q in enumerate(frame['drc_locations'] if 'drc_locations' in frame else locations(frame.get('drc',()),measure=frame.get('measure_drc',True))):
        from .resources import cancel_check
        cancel_check()
        bounds=list(map(float,q['geometry'].bounds));point=list(map(float,q['point']))
        dies={int(o)+1 for o in q.get('dependencies',()) if o>=0}
        if q.get('owner',-1)>=0:dies.add(int(q['owner'])+1)
        if model is not None:
            for ref in q.get('refs',()):
                if tuple(ref) in model.ids:dies.update(owners_by_net.get(model.nodes[model.ids[tuple(ref)]]['net'],()))
        # Construct around the actual witness. Single-owner markers align with
        # its current DIE orientation; shared-net witnesses use their own MRR.
        poses=frame.get('states')
        angle=float(poses[next(iter(dies))-1,2]) if len(dies)==1 and poses is not None and next(iter(dies))<=len(poses) else 0.
        witness=q['geometry'];margin=max(float(getattr(model.data,'dbu_um',.001)) if model is not None else .001,1e-6)
        if len(dies)==1:
            local=rotate(witness,-angle,origin=(0,0),use_radians=True)
            lo=np.asarray(local.bounds[:2])-margin;hi=np.asarray(local.bounds[2:])+margin
            rectangle=rotate(shapely.box(*lo,*hi),angle,origin=(0,0),use_radians=True)
        else:
            rectangle=witness.minimum_rotated_rectangle
            if rectangle.geom_type!='Polygon':rectangle=witness.buffer(margin).minimum_rotated_rectangle
            edge=np.diff(np.asarray(rectangle.exterior.coords),axis=0)[0];angle=math.atan2(edge[1],edge[0])
        corners=np.asarray(rectangle.exterior.coords)[:4].tolist()
        rows.append(dict(die_ids=sorted(dies) or [0],number=i+1,kind=q['kind'],layers=list(q.get('layers',(q.get('layer',''),))),
                         bounds_um=bounds,point_um=point,marker_corners_um=corners,marker_angle_deg=math.degrees(angle),package_relative_bounds_um=(np.asarray(bounds)-np.tile(origin,2)).tolist(),
                         required_um=q.get('required_um'),required_deg=q.get('required_deg'),actual_um=q.get('actual_um'),actual_deg=q.get('actual_deg'),
                         refs=[list(v) for v in q.get('refs',())],connection_failure=bool(q.get('connection_failure'))))
    return rows


def place(details,position):
    if not details.get('drc_issues'):return details
    xy=np.asarray(position,float)
    return dict(details,drc_issues=[dict(q,top_cell_marker_corners_um=(np.asarray(q['marker_corners_um'])-np.asarray(q['bounds_um'][:2])+np.asarray(q['package_relative_bounds_um'][:2])+xy).tolist() if 'marker_corners_um' in q else None,top_cell_bounds_um=(np.asarray(q['package_relative_bounds_um'])+np.tile(xy,2)).tolist()) for q in details['drc_issues']])


def write(path,task_id,details):
    from .core import clean_json
    details=clean_json(details)
    rows=details.get('drc_issues')
    if rows is None:return details
    existing=details.get('issue_catalog')
    if existing is not None:
        # Exporting a completed preview reuses its immutable full catalogue.
        # Its inline rows are only page one: never overwrite the catalogue
        # with those 128 rows and claim that they are the complete issue list.
        if not isinstance(existing,dict) or existing.get('schema')!=1 or type(existing.get('total')) is not int or existing['total']<len(rows):
            raise ValueError('已有问题目录描述无效，拒绝用截断问题页覆盖完整记录')
        return details
    if details.get('issues_complete') is False:raise ValueError('截断的问题页缺少完整目录，拒绝丢失后续问题')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Encode outside the single-writer section. SQLite already serializes
    # writers; geometry workers remain fully independent of this short commit.
    payloads=[json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':')) for row in rows]
    metadata=json.dumps({k:v for k,v in details.items() if k!='drc_issues'},ensure_ascii=False,allow_nan=False)
    deadline=time.monotonic()+_LOCK_WAIT_SECONDS;gate=_gate(path)
    from .resources import cancel_check
    while not gate.lock.acquire(timeout=.1):
        cancel_check()
        if time.monotonic()>=deadline:raise TimeoutError('等待问题目录写入队列超时；本次事务未提交')
    try:
        def transaction(timeout):
            with closing(sqlite3.connect(path,timeout=timeout)) as db, db:
                # Changing journal mode can fail immediately despite SQLite's
                # busy_timeout. Existing WAL databases need no mode transition.
                if db.execute('pragma journal_mode').fetchone()[0].lower()!='wal':
                    if db.execute('pragma journal_mode=WAL').fetchone()[0].lower()!='wal':
                        raise sqlite3.OperationalError('问题目录无法启用WAL日志模式')
                db.execute('BEGIN IMMEDIATE')
                db.execute('create table if not exists issues(task_id text, ordinal integer, payload text, primary key(task_id,ordinal))')
                db.execute('create table if not exists catalogs(task_id text primary key, total integer, metadata text)')
                db.execute('delete from issues where task_id=?',(task_id,))
                db.executemany('insert into issues values(?,?,?)',((task_id,i,payload) for i,payload in enumerate(payloads)))
                db.execute('insert or replace into catalogs values(?,?,?)',(task_id,len(rows),metadata))
                cancel_check()
        _retry_locked(transaction,deadline)
    finally:gate.lock.release()
    return dict(details,drc_issues=rows[:PAGE_SIZE],issues_complete=len(rows)<=PAGE_SIZE,
                issue_catalog=dict(schema=1,path=str(path.resolve()),task_id=task_id,total=len(rows),page_size=PAGE_SIZE,command='task.issues'))


def page(catalog,offset=0,limit=128):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=512:raise ValueError('offset须为非负整数，limit须为1～512')
    path=Path(catalog['path']).resolve()
    def read(timeout):
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=timeout)) as db:
            db.execute('BEGIN')  # total and page rows share one read snapshot
            meta=db.execute('select total from catalogs where task_id=?',(catalog['task_id'],)).fetchone()
            if meta is None:raise ValueError('问题目录不存在')
            rows=[json.loads(row[0]) for row in db.execute('select payload from issues where task_id=? and ordinal>=? order by ordinal limit ?',(catalog['task_id'],offset,limit))]
            return meta,rows
    meta,rows=_retry_locked(read,time.monotonic()+_LOCK_WAIT_SECONDS)
    return dict(total=meta[0],offset=offset,items=rows,next_offset=offset+len(rows) if offset+len(rows)<meta[0] else None,complete=offset+len(rows)>=meta[0])
