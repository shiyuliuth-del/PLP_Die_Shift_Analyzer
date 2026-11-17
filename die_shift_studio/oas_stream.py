"""Bounded-memory OASIS writer for polygon cells and placements only.

Uses explicit names, absolute coordinates, general point lists and raw-deflate
CBLOCKs. No modal geometry, external references, arc recovery or whole-layout
reload. Records are independently read back by gdstk/KLayout in regressions.
"""
import hashlib,math,os,sqlite3,struct,tempfile,zlib
from pathlib import Path
import numpy as np
import shapely
from .resources import cancel_check


def uint(value):
    n=int(value)
    if n<0:raise ValueError('Negative unsigned OAS integer')
    out=bytearray()
    while n>=128:out.append((n&127)|128);n>>=7
    out.append(n);return bytes(out)

def sint(n):return uint((abs(int(n))<<1)|(n<0))
def string(s):
    data=s.encode('ascii');return uint(len(data))+data
def real(v):return b'\x07'+struct.pack('<d',float(v))


def verify_crc(path):
    """Verify our CRC32 OAS with an explicitly closed descriptor on every OS.

    Some gdstk oas_validate builds leave their FILE open; on Windows that prevents
    rename/delete. Independent reader tests remain separate from atomic publish.
    """
    path=Path(path);size=path.stat().st_size
    if size<280:return False
    with path.open('rb') as stream:
        if stream.read(13)!=b'%SEMI-OASIS\r\n':return False
        stream.seek(-256,2);end=stream.read(256)
        if end[0]!=2 or end[-5]!=1:return False
        stream.seek(0);left=size-4;crc=0
        while left:
            cancel_check();data=stream.read(min(left,1024**2))
            if not data:return False
            left-=len(data);crc=zlib.crc32(data,crc)
    return crc==struct.unpack('<I',end[-4:])[0]


def placement(name,origin=(0.,0.),angle=0.,dbu=.001):
    xy=np.rint(np.asarray(origin)/dbu)
    if not np.isfinite(xy).all() or np.max(abs(xy),initial=0)>2**52:raise ValueError('Placement coordinates outside safe range')
    return (b'\x12\xb2'+string(name)+real(angle) if angle else b'\x11\xb0'+string(name))+sint(int(xy[0]))+sint(int(xy[1]))


def polygons(layers,dbu,origin=(0.,0.),*,stats=None):
    from .gds_merge import winding_outline
    out=bytearray()
    for key,values in sorted(layers.items()):
        layer,datatype=map(int,key.split('/'))
        if min(layer,datatype)<0:raise ValueError('Invalid layer')
        for blob in values:
            cancel_check();geometry=shapely.from_wkb(blob)
            if not geometry.is_valid:raise ValueError('Cannot output invalid copper')
            for g in shapely.get_parts(geometry):
                if g.is_empty:continue
                if g.geom_type!='Polygon':raise ValueError('Output copper must be polygonal')
                # Quantize shared global nodes FIRST. Rounding local .5 values
                # after subtracting different integer origins can otherwise
                # disagree by one DBU (ties-to-even is not translation invariant).
                xy=np.rint(winding_outline(g)/dbu)-np.rint(np.asarray(origin)/dbu)
                if not np.isfinite(xy).all() or np.max(abs(xy),initial=0)>2**52:raise ValueError('Polygon coordinates outside safe range')
                xy=xy.astype(np.int64);xy=xy[np.any(xy!=np.roll(xy,1,axis=0),axis=1)]
                # User-authorized removal of fragments with no area on the
                # actual output grid. Python integers avoid shoelace overflow
                # and cancellation at large absolute layout coordinates.
                area2=sum(int(a[0])*int(b[1])-int(a[1])*int(b[0]) for a,b in zip(xy,np.roll(xy,-1,axis=0))) if len(xy)>=3 else 0
                if not area2:
                    if stats is not None:stats['discarded_subgrid_fragments']=stats.get('discarded_subgrid_fragments',0)+1
                    continue
                out.extend(b'\x15\x3b'+uint(layer)+uint(datatype)+b'\x04'+uint(len(xy)-1))
                for x,y in np.diff(xy,axis=0):out.extend(uint((abs(int(x))<<2)|(3 if x<0 else 1))+sint(int(y)))
                out.extend(sint(int(xy[0,0]))+sint(int(xy[0,1])))
    return bytes(out)


class Store:
    """Incremental OAS stream and disk-backed cell index, under caller lock.

    Completed packets flush in blocks to the same temporary OAS. SQLite holds
    only the index/placements, never all copper. Finalization appends TopCell.
    """
    def __init__(self,directory,dbu,memory_bytes=64*1024**2,checkpoint_destination=None,*,error_layer=None):
        if not math.isfinite(dbu) or dbu<=0:raise ValueError('Output DBU must be finite and positive')
        self.error_layer=error_layer;self.error_rectangles=0;self.error_packages=0;self.discarded_subgrid_fragments=0
        self.checkpoint_count=0;self.checkpoint_destination=Path(checkpoint_destination) if checkpoint_destination else None
        self.temp=tempfile.TemporaryDirectory(prefix='.dsa-batch-',dir=directory)
        self.db=sqlite3.connect(str(Path(self.temp.name)/'cells.sqlite'),check_same_thread=False)
        self.db.execute('pragma journal_mode=OFF');self.db.execute('pragma temp_store=FILE');self.db.execute('pragma cache_size=-4096')
        self.db.execute('create table cells(name text primary key)')
        self.db.execute('create table packages(name text primary key, x real, y real)')
        self.dbu=float(dbu);self.limit=max(1024**2,int(memory_bytes));self.pending=[];self.pending_names=set();self.bytes=0;self.count=0
        self.stage=Path(self.temp.name)/'stage.oas';self.stream=self.stage.open('wb');self.crc=0;self.finished=False
        self.emit(b'%SEMI-OASIS\r\n\x01'+string('1.0')+real(1/self.dbu)+b'\0'+b'\0'*12)
        if error_layer is not None:
            layer,datatype=error_layer
            if any(type(n) is not int or not 0<=n<=65535 for n in (layer,datatype)):
                self.close();raise ValueError('ERROR层编号无效')
            # OASIS LAYERNAME geometry record: exact layer/datatype intervals.
            self.emit(b'\x0b'+string('ERROR')+b'\x03'+uint(layer)+b'\x03'+uint(datatype))
    def add(self,name,layers,position,board_origin,*,errors=None,die_count=0):
        if not name or len(name)>120 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):raise ValueError('Cell name must be 1–120 ASCII letters/digits/_/-')
        if name=='TopCell' or name.startswith('G_'):raise ValueError('Reserved cell name')
        if name in self.pending_names or self.db.execute('select 1 from packages where name=?',(name,)).fetchone():raise ValueError('Duplicate package Cell: '+name)
        children=[];cells=[];marker_count=0;stats={}
        # Reuse canonical source features, not just whole layers. Changing one
        # trace must not duplicate all unchanged traces in the same fixed layer.
        for key,blobs in sorted(layers.items()):
            for blob in blobs:
                g=shapely.from_wkb(blob)
                if g.is_empty:continue
                origin=np.rint(np.asarray(g.bounds[:2])/self.dbu)*self.dbu
                body=polygons({key:[blob]},self.dbu,origin,stats=stats)
                if not body:continue
                child='G_'+hashlib.sha256(body).hexdigest();cells.append((child,body))
                children.append(placement(child,origin,dbu=self.dbu))
        if errors is not None:
            if self.error_layer is None:raise ValueError('批次没有配置ERROR图层')
            if f'{self.error_layer[0]}/{self.error_layer[1]}' in layers:raise ValueError('ERROR层与金属源层冲突')
            from .oas_errors import rectangles
            markers=rectangles(errors,die_count,self.dbu)
            for die in range(0,die_count+1):
                blobs=markers.get(die,())
                if not blobs and die==0:continue
                # Package cell names are reserved separately from generated G_
                # geometry names; include a hash to avoid user-name collisions.
                child='G_ERROR_'+name+f'_DIE_{die}'
                cells.append((child,polygons({f'{self.error_layer[0]}/{self.error_layer[1]}':blobs},self.dbu)))
                children.append(placement(child,dbu=self.dbu))
            marker_count=sum(map(len,markers.values()))
        body=b''.join(children);shift=np.asarray(position,dtype=float)-np.asarray(board_origin)
        if shift.shape!=(2,) or not np.isfinite(shift).all():raise ValueError('Package origin must have two finite coordinates')
        self.pending.append((name,cells,body,tuple(shift)));self.pending_names.add(name);self.bytes+=sum(len(b) for _,b in cells)+len(body)
        self.count+=1;self.discarded_subgrid_fragments+=stats.get('discarded_subgrid_fragments',0);self.error_rectangles+=marker_count;self.error_packages+=bool(marker_count)
        if self.bytes>=self.limit:self.flush()
        return dict(error_rectangles=marker_count,discarded_subgrid_fragments=stats.get('discarded_subgrid_fragments',0),error_layer=(dict(layer=self.error_layer[0],datatype=self.error_layer[1],name='ERROR') if self.error_layer else None))
    def emit(self,data):
        self.stream.write(data);self.crc=zlib.crc32(data,self.crc)
    def cell(self,name,body):
        self.emit(b'\x0e'+string(name))
        compressor=zlib.compressobj(3,zlib.DEFLATED,-15);compressed=compressor.compress(body)+compressor.flush()
        self.emit(b'\x22\0'+uint(len(body))+uint(len(compressed))+compressed)
    def flush(self):
        for name,cells,body,(x,y) in self.pending:
            cancel_check()
            for child,content in cells:
                cursor=self.db.execute('insert or ignore into cells values (?)',(child,))
                if cursor.rowcount:self.cell(child,content)
            self.db.execute('insert into cells values (?)',(name,));self.cell(name,body)
            self.db.execute('insert into packages values (?,?,?)',(name,x,y))
        self.db.commit();self.stream.flush();self.pending.clear();self.pending_names.clear();self.bytes=0
        os.fsync(self.stream.fileno())
        if self.checkpoint_destination and self.count:self.checkpoint()
    def checkpoint(self):
        """Publish a complete readable stage; retain it after cancellation/error."""
        import shutil
        if self.checkpoint_count==self.count:return
        target=self.checkpoint_destination
        fd,name=tempfile.mkstemp(prefix='.checkpoint-',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as out,self.stage.open('rb') as src:
                shutil.copyfileobj(src,out,1024*1024)
                crc=self.crc
                def emit(data):
                    nonlocal crc
                    out.write(data);crc=zlib.crc32(data,crc)
                emit(b'\x0e'+string('TopCell'))
                for cell,x,y in self.db.execute('select name,x,y from packages order by name'):
                    emit(placement(cell,(x,y),dbu=self.dbu))
                emit(b'\x02'+uint(248)+b'\0'*248+b'\x01');out.write(struct.pack('<I',crc));out.flush();os.fsync(out.fileno())
            if not verify_crc(name):raise ValueError('阶段OAS校验失败')
            os.replace(name,target);self.checkpoint_count=self.count
        finally:
            if os.path.exists(name):os.unlink(name)

    def write(self,destination,progress=lambda *_:None):
        if self.finished:raise RuntimeError('Batch OAS already finalized')
        if not self.count:raise ValueError('批次没有成功的封装Cell，不替换已有文件')
        self.flush();destination=Path(destination);self.emit(b'\x0e'+string('TopCell'))
        total=self.db.execute('select count(*) from cells').fetchone()[0]
        for i,(name,x,y) in enumerate(self.db.execute('select name,x,y from packages order by name')):
            cancel_check();self.emit(placement(name,(x,y),dbu=self.dbu))
            if i%256==0:progress(round(90*i/max(self.count,1)),'输出TopCell封装引用')
        self.emit(b'\x02'+uint(248)+b'\0'*248+b'\x01');self.stream.write(struct.pack('<I',self.crc))
        self.stream.flush();os.fsync(self.stream.fileno());self.stream.close();self.finished=True
        if not verify_crc(self.stage):raise ValueError('OAS checksum validation failed')
        cancel_check();os.replace(self.stage,destination)
        return dict(path=str(destination.resolve()),cells=self.count,unique_geometry_cells=total-self.count,bytes=destination.stat().st_size,dbu_um=self.dbu,top_cell='TopCell',error_layer=(dict(layer=self.error_layer[0],datatype=self.error_layer[1],name='ERROR') if self.error_layer else None),error_rectangles=self.error_rectangles,error_packages=self.error_packages,discarded_subgrid_fragments=self.discarded_subgrid_fragments)
    def close(self):
        if not self.stream.closed:self.stream.close()
        self.db.close();self.temp.cleanup()
