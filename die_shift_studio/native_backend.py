"""Native KLayout API, or a persistent installed-desktop batch process. Offline."""
import atexit,importlib,json,logging,math,os,secrets,shutil,socket,struct,subprocess,sys,tempfile,threading
from pathlib import Path
from functools import lru_cache
import numpy as np
import shapely
from shapely.geometry import Polygon
from . import native_ops

_log=logging.getLogger(__name__)

def executable_candidates():
    """PATH, explicit installation variables, App Paths and usual install roots."""
    out=[]
    def add(value):
        if not value:return
        p=Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
        if p.is_dir():
            for name in ('klayout_app.exe','klayout.exe','klayout'):add(p/name)
        elif p.is_file() and str(p) not in out:out.append(str(p))
    for key in ('DIE_SHIFT_KLAYOUT','KLAYOUT_EXE','KLAYOUT_BIN','KLAYOUT_INSTALL_DIR'):add(os.environ.get(key))
    for name in ('klayout_app.exe','klayout.exe','klayout'):add(shutil.which(name))
    # Some configurations put the install directory in KLAYOUT_PATH/HOME.
    for key in ('KLAYOUT_PATH','KLAYOUT_HOME'):
        for value in os.environ.get(key,'').split(os.pathsep):add(value)
    if sys.platform=='win32':
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for name in ('klayout_app.exe','klayout.exe'):
                    for flags in (winreg.KEY_READ,winreg.KEY_READ|winreg.KEY_WOW64_32KEY):
                        try:
                            with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\App Paths'+'\\'+name,0,flags) as key:add(winreg.QueryValue(key,None))
                        except OSError:pass
        except ImportError:pass
        for key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA','APPDATA'):
            base=os.environ.get(key)
            if base:
                for folder in ('KLayout','Programs/KLayout'):add(Path(base)/folder)
        add(Path.home()/'KLayout')
    return out

class DesktopBackend:
    def __init__(self,executable):
        self.lock=threading.RLock();self.sock=None;self.process=None
        self.directory=tempfile.TemporaryDirectory(prefix='die-shift-klayout-');self.log_path=Path(self.directory.name)/'batch.log'
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(30)
        token=secrets.token_hex(32);config=Path(self.directory.name)/'connection.json'
        config.write_text(json.dumps(dict(port=listener.getsockname()[1],token=token)),encoding='utf-8')
        macro=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'die_shift_studio/klayout_worker.py'
        env=os.environ.copy()
        # A frozen Qt/Python installation must not override KLayout's runtime.
        for name in ('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH'):env.pop(name,None)
        if getattr(sys,'frozen',False) and 'LD_LIBRARY_PATH_ORIG' in env:env['LD_LIBRARY_PATH']=env['LD_LIBRARY_PATH_ORIG']
        self.log_file=self.log_path.open('wb')
        kwargs=dict(stdin=subprocess.DEVNULL,stdout=self.log_file,stderr=subprocess.STDOUT,env=env)
        if sys.platform=='win32':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        try:
            restore_dll=None
            if sys.platform=='win32' and getattr(sys,'frozen',False):
                import ctypes
                restore_dll=ctypes.windll.kernel32.SetDllDirectoryW
                restore_dll(None)
            try:self.process=subprocess.Popen([executable,'-b','-r',str(macro),'-rd','dsa_connection='+str(config)],**kwargs)
            finally:
                if restore_dll is not None:restore_dll(str(sys._MEIPASS))
            self.sock,_=listener.accept();self.sock.settimeout(120)
            hello=self._receive()
            if hello.pop('token',None)!=token:raise RuntimeError('KLayout IPC authentication failed')
            self.info=dict(hello,backend='installed desktop',executable=executable)
            _log.info('KLayout 离线桌面后端：%s',executable)
        except Exception as exc:
            details=self.log_path.read_text(encoding='utf-8',errors='replace')[-6000:]
            self.close();raise RuntimeError(f'KLayout 批处理启动失败：{executable}\n{details}\n{exc}') from exc
        finally:listener.close()
    def _read(self,n):
        data=[]
        while n:
            value=self.sock.recv(n)
            if not value:raise RuntimeError('KLayout 批处理连接已断开')
            data.append(value);n-=len(value)
        return b''.join(data)
    def _receive(self):
        n=struct.unpack('!I',self._read(4))[0]
        if n>256*1024*1024:raise RuntimeError('KLayout response too large')
        return json.loads(self._read(n))
    def execute(self,request):
        with self.lock:
            data=json.dumps(request,separators=(',',':')).encode('utf-8')
            self.sock.sendall(struct.pack('!I',len(data))+data);response=self._receive()
            if not response['ok']:raise RuntimeError('KLayout 后端计算失败：\n'+response['error'])
            return response['result']
    def close(self):
        if self.sock:
            try:self.sock.close()
            except OSError:pass
            self.sock=None
        if self.process:
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=3)
        if hasattr(self,'log_file'):self.log_file.close()
        self.directory.cleanup()

class ModuleBackend:
    def __init__(self):
        self.kdb=importlib.import_module('klayout.db');self.lock=threading.RLock()
        self.info=dict(native_ops.execute(self.kdb,{'op':'info'}),backend='Python module')
    def execute(self,request):
        with self.lock:return native_ops.execute(self.kdb,request)
    def close(self):pass

_backend=None;_lock=threading.RLock()
def backend():
    global _backend
    with _lock:
        if _backend is not None:return _backend
        failures=[]
        if os.environ.get('DIE_SHIFT_NATIVE_BACKEND')!='desktop':
            try:_backend=ModuleBackend();return _backend
            except (ImportError,OSError,RuntimeError) as exc:failures.append(str(exc))
        for path in executable_candidates():
            try:_backend=DesktopBackend(path);return _backend
            except Exception as exc:failures.append(str(exc))
        raise RuntimeError('未能启动 KLayout 几何后端。请将已安装的 klayout_app.exe / klayout.exe 加入 PATH，或设置 DIE_SHIFT_KLAYOUT 为程序完整路径。无需联网，不会安装依赖。\n'+'\n'.join(failures))

def close():
    global _backend
    with _lock:
        if _backend is not None:_backend.close();_backend=None
    compute.cache_clear();any_violation.cache_clear();native_ops._REGIONS.clear()
atexit.register(close)

def _encode(geometry,dbu):
    out=[];pending=list(shapely.get_parts(geometry))
    while pending:
        p=pending.pop()
        if p.geom_type in ('MultiPolygon','GeometryCollection'):pending.extend(shapely.get_parts(p));continue
        if p.geom_type!='Polygon':continue
        rings=[]
        for ring in [p.exterior,*p.interiors]:
            xy=np.rint(np.asarray(ring.coords)[:-1]/dbu).astype(np.int64)
            if len(xy)>3:
                xy=xy[np.any(xy!=np.roll(xy,1,axis=0),axis=1)]
                a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
                if np.max(abs(np.r_[a,b]),initial=0)<2e9:
                    keep=(a[:,0]*b[:,1]-a[:,1]*b[:,0]!=0)|(np.sum(a*b,axis=1)<0)
                    if keep.sum()>=3:xy=xy[keep]
            rings.append(xy.tolist())
        out.append(rings)
    return out

@lru_cache(maxsize=192)
def compute(op,wkb,dbu,distance=0.,other=None):
    request=dict(op=op,a=_encode(shapely.from_wkb(wkb),dbu))
    if op.removesuffix('_any') in ('width','space','notch','separation'):request['distance']=max(1,math.ceil(distance/dbu-1e-9))
    if op=='round':request['radius']=round(distance/dbu)
    if other is not None:request['b']=_encode(shapely.from_wkb(other),dbu)
    result=backend().execute(request);polygons=[]
    for rings in result['polygons']:
        p=Polygon(np.asarray(rings[0])*dbu,[np.asarray(r)*dbu for r in rings[1:]])
        polygons.append(p if p.is_valid else shapely.make_valid(p))
    return shapely.union_all(polygons),result['area']*dbu*dbu

def geometry(op,poly,dbu,distance=0.,other=None):
    return compute(op,poly.wkb,float(dbu),float(distance),other.wkb if other is not None else None)[0]

@lru_cache(maxsize=2048)
def any_violation(op,wkb,dbu,distance,other=None):
    # Native early-exit preserves the exact edge-pair predicates while avoiding
    # marker polygon construction/union/serialization for probability decisions.
    return compute(op+'_any',wkb,float(dbu),float(distance),other)[1]>0
