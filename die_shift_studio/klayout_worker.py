"""Trusted batch macro for the installed KLayout executable (offline IPC)."""
import sys,os,json,socket,struct,traceback
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import native_ops
import pya as kdb

def receive(sock,n):
    parts=[]
    while n:
        data=sock.recv(n)
        if not data:raise EOFError()
        parts.append(data);n-=len(data)
    return b''.join(parts)

def send(sock,value):
    data=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode('utf-8')
    sock.sendall(struct.pack('!I',len(data))+data)

with open(dsa_connection,encoding='utf-8') as stream:config=json.load(stream)
sock=socket.create_connection(('127.0.0.1',config['port']),timeout=30);sock.settimeout(None)
send(sock,dict(token=config['token'],**native_ops.execute(kdb,{'op':'info'})))
try:
    while True:
        size=struct.unpack('!I',receive(sock,4))[0]
        if size>256*1024*1024:raise ValueError('IPC request too large')
        request=json.loads(receive(sock,size))
        try:send(sock,dict(ok=True,result=native_ops.execute(kdb,request)))
        except Exception:send(sock,dict(ok=False,error=traceback.format_exc()))
except EOFError:pass
finally:sock.close()
