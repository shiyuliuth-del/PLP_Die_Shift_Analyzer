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
        self.caches={};self.recent=OrderedDict();self.pinned=set();self.bytes=0;self.enabled=True
        self.values=None;self.motions=None;self.replays=OrderedDict();self.engines={};self.frame_stores={};self.last_assessment=None;self.nominal={}
    def register(self,cache):
        with self.lock:self.caches[id(cache)]=cache
    def entries(self,cache):
        return self.replays if cache is self.motions else self.recent
    def touch(self,cache,key):
        records=self.entries(cache);identity=(id(cache),key)
        if identity in records:records.move_to_end(identity)
    def stored(self,cache,key,delta):
        records=self.entries(cache);identity=(id(cache),key);new=identity not in records
        self.bytes+=delta;records[identity]=None;records.move_to_end(identity)
        if key in cache.pinned:self.pinned.add(identity)
        if new:self.trim(records)
    def removed(self,cache,key,size):
        identity=(id(cache),key);self.bytes-=size;self.entries(cache).pop(identity,None);self.pinned.discard(identity)
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
            self.nominal.clear();self.last_assessment=None
    def close(self):
        self.enabled=False
        for engine in list(self.engines.values()):engine.close()
        for cache in list(self.caches.values()):cache.close()
        self.engines.clear();self.nominal.clear();self.last_assessment=None


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
    def __init__(self,budget=32*1024**2):self.budget=budget;self.memory=OrderedDict();self.used=0
    def get(self,key):
        entry=self.memory.get(key)
        if entry is None:return None
        self.memory.move_to_end(key);return entry[0]
    def put(self,key,value):
        size=2*len(pickle.dumps(value,protocol=5))
        old=self.memory.pop(key,None)
        if old is not None:self.used-=old[1]
        if size>self.budget:return
        self.memory[key]=(value,size);self.used+=size
        while self.used>self.budget or len(self.memory)>64:
            _,(_,amount)=self.memory.popitem(last=False);self.used-=amount
    def clear(self):self.memory.clear();self.used=0


class SessionCache(MutableMapping):
    def __init__(self, budget_bytes=32 * 1024**2, group=None):
        self.budget=max(0,budget_bytes);self.memory=OrderedDict();self.sizes={};self.paths={};self.used=0
        self.directory=tempfile.TemporaryDirectory(prefix='die_shift_cache_')
        self.root=Path(self.directory.name);self.group=group;self.lock=group.lock if group is not None else threading.RLock();self.pinned=set()
        self.hits=0;self.misses=0;self.closed=False;self.inflight={}
        if group is not None:group.register(self)

    def __getitem__(self,key):
        with self.lock:
            if key not in self.sizes:self.misses+=1;raise KeyError(key)
            self.hits+=1
            if self.group is not None:self.group.touch(self,key)
            if key in self.memory:
                self.memory.move_to_end(key);return self.memory[key]
            value=pickle.loads((self.root/self.paths[key]).read_bytes())
            self._remember(key,value);return value

    def _remember(self,key,value):
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
            if self.group is not None:self.group.stored(self,key,len(blob)-previous)

    def pin(self,key):
        with self.lock:
            self.pinned.add(key)
            if self.group is not None and key in self.sizes:self.group.pinned.add((id(self),key))

    def __contains__(self,key):return key in self.sizes
    def __iter__(self):return iter(self.sizes)
    def __len__(self):return len(self.sizes)
    def __delitem__(self,key):
        with self.lock:
            size=self.sizes.pop(key)
            if key in self.memory:self.memory.pop(key);self.used-=size
            (self.root/self.paths.pop(key)).unlink(missing_ok=True)
            self.pinned.discard(key)
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
