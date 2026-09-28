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
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


class SessionCache(MutableMapping):
    def __init__(self, budget_bytes=32 * 1024**2):
        self.budget=max(0,budget_bytes);self.memory=OrderedDict();self.sizes={};self.used=0
        self.directory=tempfile.TemporaryDirectory(prefix='die_shift_cache_')
        self.root=Path(self.directory.name);self.lock=threading.RLock()
        self.hits=0;self.misses=0;self.closed=False

    def __getitem__(self,key):
        with self.lock:
            if key not in self.sizes:self.misses+=1;raise KeyError(key)
            self.hits+=1
            if key in self.memory:
                self.memory.move_to_end(key);return self.memory[key]
            value=pickle.loads((self.root/fingerprint(key)).read_bytes())
            self._remember(key,value);return value

    def _remember(self,key,value):
        if self.sizes[key]>self.budget:return
        self.memory[key]=value;self.used+=self.sizes[key]
        while self.used>self.budget:
            old,_=self.memory.popitem(last=False);self.used-=self.sizes[old]

    def __setitem__(self,key,value):
        with self.lock:
            if self.closed:return
            blob=pickle.dumps(value,protocol=5)
            if key in self.memory:self.memory.pop(key);self.used-=self.sizes[key]
            (self.root/fingerprint(key)).write_bytes(blob)
            self.sizes[key]=len(blob);self._remember(key,value)

    def __contains__(self,key):return key in self.sizes
    def __iter__(self):return iter(self.sizes)
    def __len__(self):return len(self.sizes)
    def __delitem__(self,key):
        with self.lock:
            size=self.sizes.pop(key)
            if key in self.memory:self.memory.pop(key);self.used-=size
            (self.root/fingerprint(key)).unlink(missing_ok=True)
    def move_to_end(self,key):
        if key in self.memory:self.memory.move_to_end(key)
    def clear(self):
        with self.lock:
            for key in list(self.sizes):del self[key]
    def close(self):
        with self.lock:
            self.memory.clear();self.sizes.clear();self.used=0;self.closed=True;self.directory.cleanup()
    def get_or_compute(self,key,fn):
        try:return self[key]
        except KeyError:
            value=fn();self[key]=value;return value

    def __del__(self):
        if hasattr(self,'directory') and not getattr(self,'closed',True):self.close()
