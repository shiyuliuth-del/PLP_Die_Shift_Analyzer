"""Authenticated, JSON-only local IPC: Unix socket / Windows named pipe.

No TCP listener, pickle deserialization, shell execution or dynamic imports.
The Qt controller owns commands; accept/transport threads never touch Qt state.
"""
import json,os,secrets,threading,time,uuid
from pathlib import Path
from multiprocessing.connection import Listener,Client
from concurrent.futures import ThreadPoolExecutor

MAX_MESSAGE=2*1024*1024


def decode(data):
    value=json.loads(data,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Non-finite JSON: '+v)))
    if not isinstance(value,dict):raise ValueError('Request must be a JSON object')
    return value


def call(endpoint,command,args=None,timeout=60):
    info=json.loads(Path(endpoint).read_text(encoding='utf8'))
    request=dict(id=uuid.uuid4().hex,command=command,args=args or {})
    payload=json.dumps(request,allow_nan=False,separators=(',',':')).encode()
    if len(payload)>MAX_MESSAGE:raise ValueError('IPC request too large')
    with Client(info['address'],family=info['family'],authkey=bytes.fromhex(info['token'])) as connection:
        connection.send_bytes(payload)
        if not connection.poll(timeout):raise TimeoutError('IPC reply timed out')
        response=decode(connection.recv_bytes(MAX_MESSAGE))
    if response.get('id')!=request['id']:raise RuntimeError('IPC response identity mismatch')
    return response


class Server:
    def __init__(self,endpoint,dispatch):
        self.endpoint=Path(endpoint).resolve();self.endpoint.parent.mkdir(parents=True,exist_ok=True)
        # Cross-process lifetime lock prevents two simultaneous starts from
        # publishing different authenticated sockets under one descriptor.
        self.lock_file=open(str(self.endpoint)+'.lock','a+b');self.lock_file.seek(0);self.lock_file.write(b'0');self.lock_file.flush();self.lock_file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close();raise RuntimeError('此IPC入口正在使用')
        if self.endpoint.exists():
            try:active=call(self.endpoint,'ping',timeout=1).get('status')=='completed'
            except (OSError,EOFError,TimeoutError,ValueError):active=False
            if active:self.lock_file.close();raise RuntimeError('此IPC入口已被另一实例占用')
        self.family='AF_PIPE' if os.name=='nt' else 'AF_UNIX'
        self.address=(r'\\.\pipe\DieShift-'+uuid.uuid4().hex if os.name=='nt' else str(self.endpoint.parent/('s-'+uuid.uuid4().hex[:12])))
        if self.family=='AF_UNIX' and len(self.address.encode())>100:
            import tempfile
            self.socket_directory=__import__('tempfile').TemporaryDirectory(prefix='dsa-ipc-')
            self.address=str(Path(self.socket_directory.name)/'socket')
        self.token=secrets.token_bytes(32);self.dispatch=dispatch;self.closed=threading.Event()
        self.listener=Listener(self.address,family=self.family,backlog=64,authkey=self.token)
        if os.name!='nt':os.chmod(self.address,0o600)
        self.pool=ThreadPoolExecutor(max_workers=16,thread_name_prefix='dsa-ipc');self.slots=threading.BoundedSemaphore(16)
        content=dict(protocol=1,pid=os.getpid(),family=self.family,address=self.address,token=self.token.hex())
        fd=os.open(str(self.endpoint),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf8') as stream:json.dump(content,stream)
        self.thread=threading.Thread(target=self._accept,name='dsa-ipc-listener',daemon=True);self.thread.start()
    def _accept(self):
        while not self.closed.is_set():
            try:connection=self.listener.accept()
            except (OSError,EOFError):break
            except Exception:
                if self.closed.is_set():break
                continue
            if self.closed.is_set():connection.close();break
            if not self.slots.acquire(blocking=False):
                try:
                    if connection.poll(.5):
                        request=decode(connection.recv_bytes(MAX_MESSAGE))
                        connection.send_bytes(json.dumps(dict(id=request.get('id'),status='error',error=dict(type='Busy',message='IPC transport busy',retry_after_ms=200))).encode())
                except (OSError,EOFError,ValueError):pass
                finally:connection.close()
                continue
            try:self.pool.submit(self._handle,connection)
            except RuntimeError:connection.close();self.slots.release();break
        self.listener.close()
        self.lock_file.close()
        if hasattr(self,'socket_directory'):self.socket_directory.cleanup()
    def _handle(self,connection):
        request={}
        try:
            if not connection.poll(10):return
            request=decode(connection.recv_bytes(MAX_MESSAGE))
            if not isinstance(request.get('id'),str) or not isinstance(request.get('command'),str) or not isinstance(request.get('args',{}),dict):raise ValueError('Invalid command envelope')
            event=threading.Event();reply={}
            self.dispatch(request,event,reply)
            if not event.wait(30):raise TimeoutError('GUI command dispatch timed out')
            response=dict(reply,id=request['id'])
        except Exception as exc:response=dict(id=request.get('id'),status='error',error=dict(type=type(exc).__name__,message=str(exc)))
        try:connection.send_bytes(json.dumps(response,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
        except (OSError,EOFError):pass
        finally:connection.close();self.slots.release()
    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        # Wake a blocked accept without killing a geometry or transport thread.
        def wake():
            try:
                with Client(self.address,family=self.family,authkey=self.token):pass
            except (OSError,EOFError):pass
        threading.Thread(target=wake,daemon=True).start()
        self.pool.shutdown(wait=False,cancel_futures=True)
        self.thread.join(.5)
        try:
            info=json.loads(self.endpoint.read_text())
            if info.get('token')==self.token.hex():self.endpoint.unlink()
        except (OSError,ValueError):pass
