"""Versioned, data-only JSON/Zlib result sidecar. Never execute a pickle payload."""
import base64,hashlib,json,os,tempfile,zlib
from pathlib import Path
from collections import OrderedDict
import numpy as np
import shapely
from . import __version__
from .session_cache import logical_fingerprint


def data_types():
    from types import SimpleNamespace
    from .extreme_filter import PoseClause
    from .width_grid import WidthGrid
    from .statistics import ExtremeCatalog
    return dict(clause=PoseClause,width_grid=WidthGrid,extreme_grid=ExtremeCatalog,index=SimpleNamespace)


def encode(x):
    if x is None or isinstance(x,(str,bool,int,float)):return x
    if isinstance(x,bytes):return ['bytes',base64.b64encode(x).decode('ascii')]
    if type(x) in data_types().values():return ['record',next(k for k,v in data_types().items() if type(x) is v),encode(x.__dict__)]
    if isinstance(x,np.generic):return encode(x.item())
    if isinstance(x,np.ndarray):
        if x.dtype.hasobject:return ['object_array',encode(x.tolist())]
        return ['array',x.dtype.str,list(x.shape),base64.b64encode(x.tobytes()).decode('ascii')]
    if isinstance(x,shapely.Geometry):return ['geometry',x.wkb_hex]
    if isinstance(x,dict):return ['dict',[[encode(k),encode(v)] for k,v in x.items()]]
    if isinstance(x,(tuple,list,set,frozenset)):return [type(x).__name__,[encode(v) for v in x]]
    from .reconstruction_route import RoutePlan
    if isinstance(x,RoutePlan):return ['plane_route' if getattr(x,'plane_domain',False) else 'route',encode(x.__getstate__())]
    raise TypeError('不支持的结果类型：'+type(x).__name__)


def decode(x):
    if not isinstance(x,list):return x
    kind=x[0]
    if kind=='bytes':return base64.b64decode(x[1],validate=True)
    if kind=='record':
        cls=data_types().get(x[1])
        if cls is None:raise ValueError('未知结果记录类型')
        obj=cls.__new__(cls);obj.__dict__.update(decode(x[2]));return obj
    if kind=='geometry':return shapely.from_wkb(bytes.fromhex(x[1]))
    if kind=='array':
        dtype=np.dtype(x[1])
        if dtype.hasobject:raise ValueError('结果数组不能包含对象指针')
        return np.frombuffer(base64.b64decode(x[3],validate=True),dtype=dtype).copy().reshape(x[2])
    if kind=='object_array':return np.asarray(decode(x[1]),dtype=object)
    if kind=='dict':return {decode(k):decode(v) for k,v in x[1]}
    if kind in ('tuple','list','set','frozenset'):return {'tuple':tuple,'list':list,'set':set,'frozenset':frozenset}[kind](decode(v) for v in x[1])
    if kind in ('route','plane_route'):
        from .reconstruction_route import RoutePlan
        if kind=='plane_route':
            from .plane_route import PlaneRoute
            RoutePlan=PlaneRoute
        obj=object.__new__(RoutePlan);obj.__dict__.update(decode(x[1]));obj.cache={};obj.pose_memo=OrderedDict();obj.memo_bytes=0;return obj
    raise ValueError('未知的结果数据类型')


def identity(w):
    from dataclasses import asdict
    return logical_fingerprint((__version__,w.data.geometry_id,w.data.top_name,w.roles(),w.ordered_layers(),w.motions(),asdict(w.params()),asdict(w.view_settings)))


def save(w,path):
    if not w.view_settings.save_project_results:return None
    if w._tasks or w.preview_pending or w.motion_pending:
        w.log_bus.emit('计算尚未完成，本次仅保存设置，不保存不完整结果。','WARNING');return None
    folder=Path(w.view_settings.result_cache_directory).expanduser() if w.view_settings.result_cache_directory else path.parent
    folder.mkdir(parents=True,exist_ok=True)
    payload=dict(frame=w.current_frame,result=w.result,shown=w.shown_states,probability=w.probability_data,space=w.space_data,
                 footprints=w.footprint_data,width_summary=w._width_grid_summary,width_bank=w.width_yield.bank,
                 width_token=w.width_yield.key[1] if w.width_yield.key else None,reconstruction_bank=w.reconstruction_yield.bank,
                 reconstruction_evidence=w.reconstruction_yield.evidence,reconstruction_token=w.reconstruction_yield.key[1] if w.reconstruction_yield.key else None,plans=getattr(getattr(w.engine,'reconstructor',None),'plan_pool',{}))
    raw=zlib.compress(json.dumps(dict(version=__version__,identity=identity(w),payload=encode(payload)),ensure_ascii=False,allow_nan=True).encode(),6)
    digest=hashlib.sha256(raw).hexdigest();target=folder/(path.stem+'.'+digest[:20]+'.dsresults')
    fd,name=tempfile.mkstemp(prefix='.results-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
    return dict(path=os.path.relpath(target,path.parent),sha256=digest,identity=identity(w))


def restore(w,project,path):
    row=project.get('saved_results')
    if not row or path is None:return False
    applied=False;e=None;signature=None
    try:
        if row['identity']!=identity(w):raise ValueError('源码、图纸或参数已变化')
        file=Path(path).parent/row['path']
        if file.stat().st_size>128*1024**2:raise ValueError('结果文件超过128MiB读取上限')
        raw=file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('校验值不匹配')
        stream=zlib.decompressobj();body=stream.decompress(raw,512*1024**2)
        if not stream.eof:raise ValueError('结果文件过大或不完整')
        item=json.loads(body)
        if item['version']!=__version__ or item['identity']!=identity(w):raise ValueError('结果版本不一致')
        value=decode(item['payload'])
        from .fast_preview import GeometryCache,PreviewEngine
        cache=w.geometry_cache or GeometryCache(w.data);con=w.case['connections'];p=w.params()
        signature=w.preview_identity(w.data,w.roles(),w.ordered_layers(),w.motions(),p,value['result'])
        e=PreviewEngine(cache,w.case,w.roles(),w.motions(),p,value['result'],con,cache_group=w.cache_group,cache_identity=signature)
        e.configure_deformation(False,w.view_settings)
        e.signature=signature;e.reconstruction_plan_pool=value['plans'];e.configure_reconstruction(w.reconstruction_toggle.isChecked(),w.view_settings)
        e.configure_width(w.width_toggle.isChecked(),w.view_settings);e.configure_drc(w.drc_toggle.isChecked(),w.view_settings)
        applied=True
        w.engine=e;w.geometry_cache=cache;w.connections=con;w.session_engines[(signature,id(e))]=e
        if value['result'] is not None:
            original=w.request_preview;original_refresh=w.reconstruction_yield.refresh;original_width=w.width_yield.refresh
            w.request_preview=lambda *a,**kw:None;w.reconstruction_yield.refresh=lambda *a,**kw:None;w.width_yield.refresh=lambda *a,**kw:None
            try:w.display_result(value['result'])
            finally:w.request_preview=original;w.reconstruction_yield.refresh=original_refresh;w.width_yield.refresh=original_width
        w.engine=e;w.result=value['result'];w.current_frame=value['frame'];w.shown_states=value['shown'];w.manual_states=value['shown']
        w.probability_data=value['probability'];w.space_data=value['space'];w.footprint_data=value['footprints']
        if w.current_frame is not None:e.frames[(e.cache_key(),w.current_frame['states'].tobytes(),False)]=w.current_frame
        w._width_grid_summary=value.get('width_summary')
        for name in ('width','reconstruction'):
            controller=getattr(w,name+'_yield');controller.bank=value.get(name+'_bank')
            token=value.get(name+'_token');controller.key=(id(w.cache_group),token) if token else None
        w.reconstruction_yield.evidence=value.get('reconstruction_evidence')
        w.width_yield.render();w.reconstruction_yield.render()
        if w.result is not None:w.cache_group.last_assessment=(w.assessment_identity(w.data,w.roles(),w.ordered_layers(),p,w.view_settings),w.result)
        w.dirty=False;w.plot_layout(preserve=True);w.update_histograms()
        if w.shown_states is not None:w.update_state_table(w.shown_states)
        w.log_bus.emit('已恢复保存时的完整结果画面；其他工况按当前配置复用或继续计算。');return True
    except Exception as exc:
        if applied:
            w.result=None;w.width_yield.reset();w.reconstruction_yield.reset();w.drop_result()
            w.session_engines.pop((signature,id(e)),None)
            e.frames.clear();e.close();w.engine=None
        w.log_bus.emit('未恢复结果缓存，工程设置已正常读取：'+str(exc),'WARNING');return False
