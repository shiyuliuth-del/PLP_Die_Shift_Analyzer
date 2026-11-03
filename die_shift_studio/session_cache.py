"""Session-only memoization: bounded RAM, private temporary disk spill.

Only this process's own values are serialized. Never load project/user pickles.
Entries survive LRU eviction on disk until explicit evaluation reset or close.
"""
from collections import OrderedDict
from collections.abc import MutableMapping
import hashlib
import pickle
import tempfile
import threading
import json
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


def logical_fingerprint(value):
    """Content identity must not depend on pickle's object-alias memo table."""
    def convert(v):
        if isinstance(v,dict):return {str(k):convert(x) for k,x in sorted(v.items(),key=lambda q:str(q[0]))}
        if isinstance(v,(tuple,list)):return [convert(x) for x in v]
        if hasattr(v,'tolist'):return convert(v.tolist())
        if isinstance(v,float) and v==0:return 0.
        return v
    return hashlib.sha256(json.dumps(convert(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=True).encode()).hexdigest()


class ProjectCacheGroup:
    def __init__(self,key,max_entries=2000):
        self.key=key;self.max_entries=max_entries;self.lock=threading.RLock()
        self.caches={};self.recent=OrderedDict();self.pinned=set();self.bytes=0;self.enabled=True;self.revision=0
        self.values=None;self.motions=None;self.replays=OrderedDict();self.engines={};self.frame_stores={};self.last_assessment=None;self.nominal={};self.frame_ram=None
    def shared_frame_ram(self):
        with self.lock:
            if self.frame_ram is None:
                from .resources import resource_profile
                mib=max(8,min(256,resource_profile()['available_memory_mb']//32))
                self.frame_ram=EphemeralCache(mib*1024**2,max_entries=256)
            return self.frame_ram
    def park_frames(self):
        with self.lock:
            if self.frame_ram is not None:self.frame_ram.clear()
    def register(self,cache):
        with self.lock:self.caches[id(cache)]=cache
    def entries(self,cache):
        return self.replays if cache is self.motions else self.recent
    def touch(self,cache,key):
        records=self.entries(cache);identity=(id(cache),key)
        if identity in records:records.move_to_end(identity)
    def stored(self,cache,key,delta):
        records=self.entries(cache);identity=(id(cache),key);new=identity not in records
        self.revision+=1;self.bytes+=delta;records[identity]=None;records.move_to_end(identity)
        if key in cache.pinned:self.pinned.add(identity)
        if new:self.trim(records)
    def removed(self,cache,key,size):
        identity=(id(cache),key);self.revision+=1;self.bytes-=size;self.entries(cache).pop(identity,None);self.pinned.discard(identity)
    def pose_inventory(self,signature,zero,previous=-1,config=None):
        """Read key metadata only. Inspecting a row must never load or touch an LRU entry."""
        if self.revision==previous or not self.lock.acquire(blocking=False):return None
        try:
            by_pose={}
            def add(state,kind,identity):
                by_pose.setdefault(state,set()).add((kind,identity))
            for cache in self.caches.values():
                tag=cache.pose_source
                if tag and tag[0]==signature:
                    _,kind,source_config=tag
                    for key in cache.sizes:
                        if kind=='预演帧':
                            if key[0]=='global':state=zero
                            elif key[2] is False:state=key[1]
                            else:continue
                        else:state=key if kind=='宽度' else key[0]
                        add(state,kind,(source_config,key))
            for key in self.motions.completed:
                if key[1]==signature:
                    for state in {key[3],key[4]}:add(state,'动画',key)
            for key in self.values.sizes:
                if isinstance(key,tuple) and len(key)>2 and key[0] in ('repair','package') and key[1]==signature:
                    add(key[2] if key[2] is not None else zero,'补块' if key[0]=='repair' else '整包间距图',key)
            result=(self.revision,{state:len(entries) for state,entries in by_pose.items()})
            if config is None:return result
            store=self.frame_stores.get(signature)
            statuses={} if store is None else {key[1]:v for key,v in store.row_status.items() if key[0]==config}
            return (*result,statuses)
        finally:self.lock.release()

    def trim(self,records):
        pinned=len(self.pinned) if records is self.recent else 0
        while len(records)-pinned>self.max_entries:
            identity=next(v for v in records if v not in self.pinned)
            uid,key=identity;del self.caches[uid][key]
    def limit(self,count):
        with self.lock:
            if self.max_entries!=count:
                self.max_entries=int(count);self.trim(self.recent);self.trim(self.replays)
    def clear(self):
        with self.lock:
            for cache in list(self.caches.values()):cache.clear()
            self.park_frames();self.nominal.clear();self.last_assessment=None
    def close(self):
        self.enabled=False
        for engine in list(self.engines.values()):engine.close()
        for cache in list(self.caches.values()):cache.close()
        self.park_frames();self.engines.clear();self.nominal.clear();self.last_assessment=None


class ProjectCacheRegistry:
    """All projects opened in this application run; exit still removes caches."""
    def __init__(self):self.groups={};self.retired=[];self._stats=(0,0,0,0)
    def retire(self,key):
        group=self.groups.pop(key,None)
        if group is not None:group.enabled=False;self.retired.append(group)
        return group
    def rekey(self,old,new):
        if old==new:return
        if new in self.groups:raise ValueError('Target project cache already exists')
        group=self.groups.pop(old);group.key=new;self.groups[new]=group
    def get(self,key,max_entries=2000):
        if key not in self.groups:
            group=ProjectCacheGroup(key,max_entries);group.values=SessionCache(group=group);group.motions=SessionCache(group=group);self.groups[key]=group
        group=self.groups[key];group.limit(max_entries);return group
    def statistics(self,current):
        # The UI timer never waits for a large pickle read/write in a worker.
        total=0;size=0;count=0;pinned=0
        for key,group in list(self.groups.items())+[(g.key,g) for g in self.retired]:
            if not group.lock.acquire(blocking=False):return self._stats
            try:
                total+=group.bytes
                if key==current:size+=group.bytes;count+=len(group.recent);pinned+=len(group.pinned)
            finally:group.lock.release()
        self._stats=(size,total,count,pinned);return self._stats
    def close(self):
        for group in list(self.groups.values())+self.retired:group.close()
        self.groups.clear();self.retired.clear()


class EphemeralCache:
    """Intermediate poses live in animation bundles; do not evict real results."""
    def __init__(self,budget=32*1024**2,max_entries=64):
        self.budget=budget;self.max_entries=max_entries;self.memory=OrderedDict();self.used=0
    def get(self,key):
        entry=self.memory.get(key)
        if entry is None:return None
        self.memory.move_to_end(key);return entry[0]
    def put(self,key,value,size=None):
        if size is None:size=2*len(pickle.dumps(value,protocol=5))
        old=self.memory.pop(key,None)
        if old is not None:self.used-=old[1]
        if size>self.budget:return
        self.memory[key]=(value,size);self.used+=size
        while self.used>self.budget or len(self.memory)>self.max_entries:
            _,(_,amount)=self.memory.popitem(last=False);self.used-=amount
    def discard(self,key):
        old=self.memory.pop(key,None)
        if old is not None:self.used-=old[1]
    def clear(self):self.memory.clear();self.used=0


class SessionCache(MutableMapping):
    def __init__(self, budget_bytes=32 * 1024**2, group=None, frame_ram=None):
        self.frame_ram=frame_ram
        self.budget=max(0,budget_bytes);self.memory=OrderedDict();self.sizes={};self.paths={};self.used=0
        self.directory=tempfile.TemporaryDirectory(prefix='die_shift_cache_')
        self.root=Path(self.directory.name);self.group=group;self.lock=group.lock if group is not None else threading.RLock();self.pinned=set()
        self.hits=0;self.misses=0;self.closed=False;self.inflight={};self.pose_source=None;self.completed=set();self.row_status={}
        if group is not None:group.register(self)

    def __getitem__(self,key):
        with self.lock:
            if key not in self.sizes:self.misses+=1;raise KeyError(key)
            self.hits+=1
            if self.group is not None:self.group.touch(self,key)
            if key in self.memory:
                self.memory.move_to_end(key);return self.memory[key]
            if self.frame_ram is not None:
                hot=self.frame_ram.get((id(self),key))
                if hot is not None:return hot
            value=pickle.loads((self.root/self.paths[key]).read_bytes())
            self._remember(key,value);return value

    def _remember(self,key,value):
        if self.frame_ram is not None and key not in self.pinned:
            self.frame_ram.put((id(self),key),value,size=2*self.sizes[key]+512)
            return
        if self.sizes[key]>self.budget and key not in self.pinned:return
        self.memory[key]=value;self.used+=self.sizes[key]
        while self.used>self.budget:
            old=next((k for k in self.memory if k not in self.pinned),None)
            if old is None:break
            self.memory.pop(old);self.used-=self.sizes[old]

    def __setitem__(self,key,value):
        with self.lock:
            if self.closed or (self.group is not None and not self.group.enabled):return
            blob=pickle.dumps(value,protocol=5)
            previous=self.sizes.get(key,0)
            if key in self.memory:self.memory.pop(key);self.used-=self.sizes[key]
            filename=self.paths.setdefault(key,fingerprint(key))
            (self.root/filename).write_bytes(blob)
            self.sizes[key]=len(blob);self._remember(key,value)
            if self.group is not None:
                if (self.pose_source and self.pose_source[1]=='预演帧' and key[0]!='global' and key[2] is False
                        and isinstance(value,dict) and 'checks' in value):
                    from .extreme_validation import frame_status
                    q=frame_status(value);self.row_status[key]=dict(safe=q['safe'],checked=q['checked'],width_pending=q.get('width_pending',False))
                if self is self.group.motions:
                    if isinstance(value,dict) and value.get('complete'):self.completed.add(key)
                    else:self.completed.discard(key)
                self.group.stored(self,key,len(blob)-previous)

    def pin(self,key):
        with self.lock:
            self.pinned.add(key)
            if self.frame_ram is not None:
                token=(id(self),key);value=self.frame_ram.get(token)
                if value is not None:self.frame_ram.discard(token);self._remember(key,value)
            if self.group is not None and key in self.sizes:self.group.pinned.add((id(self),key))

    def __contains__(self,key):return key in self.sizes
    def __iter__(self):return iter(self.sizes)
    def __len__(self):return len(self.sizes)
    def __delitem__(self,key):
        with self.lock:
            size=self.sizes.pop(key)
            if self.frame_ram is not None:self.frame_ram.discard((id(self),key))
            if key in self.memory:self.memory.pop(key);self.used-=size
            (self.root/self.paths.pop(key)).unlink(missing_ok=True)
            self.pinned.discard(key);self.completed.discard(key);self.row_status.pop(key,None)
            if self.group is not None:self.group.removed(self,key,size)
    def move_to_end(self,key):
        if key in self.memory:self.memory.move_to_end(key)
    def clear(self):
        with self.lock:
            for key in list(self.sizes):del self[key]
    def close(self):
        with self.lock:
            if self.closed:return
            self.clear()
            self.memory.clear();self.sizes.clear();self.used=0;self.closed=True;self.directory.cleanup()
            if self.group is not None:self.group.caches.pop(id(self),None)
    def get_or_compute(self,key,fn):
        while True:
            with self.lock:
                if key in self.sizes:return self[key]
                pending=self.inflight.get(key)
                if pending is None:
                    pending=threading.Event();self.inflight[key]=pending;owner=True
                else:owner=False
            if owner:
                try:
                    value=fn();self[key]=value;return value
                finally:
                    with self.lock:self.inflight.pop(key,None);pending.set()
            pending.wait()  # Releases the GIL; never holds the shared disk lock.

    def __del__(self):
        if hasattr(self,'directory') and not getattr(self,'closed',True):self.close()
