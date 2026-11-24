"""Timestamped thread-safe runtime output, mirrored to the original streams."""
import sys
import logging
import traceback
import warnings
from collections import deque
from datetime import datetime
from itertools import islice
from pathlib import Path
from threading import Lock
class LogBus:
    def __init__(self,path=None,*,enabled=True):
        self.path=Path(path) if path else None
        self.lock=Lock()
        self.enabled=enabled
        self.lines=deque(maxlen=10000)
        self.sequence=0
        if self.path:
            try:self.path.parent.mkdir(parents=True,exist_ok=True)
            except OSError:self.path=None
    def set_enabled(self,enabled):
        with self.lock:self.enabled=enabled

    def read_after(self,sequence):
        with self.lock:
            if sequence==self.sequence:return sequence,[]
            begin=max(0,sequence-self.sequence+len(self.lines))
            return self.sequence,list(islice(self.lines,begin,None))

    def text(self):
        return '\n'.join(self.read_after(0)[1])

    def emit(self,text,level='INFO'):
        if not self.enabled:return
        stamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines=[f'{stamp} [{level}] {line}' for line in str(text).rstrip().splitlines()]
        if not lines:return
        with self.lock:
            if not self.enabled:return
            self.lines.extend(lines)
            self.sequence+=len(lines)
            if self.path:
                try:
                    with self.path.open('a',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
                except OSError:pass

class Stream:
    def __init__(self,original,bus,level):self.original=original;self.bus=bus;self.level=level
    def write(self,text):
        if self.original:self.original.write(text)
        if str(text).strip():self.bus.emit(text,self.level)
        return len(text)
    def flush(self):
        if self.original:self.original.flush()
    def __getattr__(self,key):return getattr(self.original,key)

class Handler(logging.Handler):
    def __init__(self,bus):super().__init__();self.bus=bus
    def emit(self,record):self.bus.emit(self.format(record),record.levelname)

class RuntimeCapture:
    def __init__(self,bus):
        self.bus=bus;self.stdout=sys.stdout;self.stderr=sys.stderr;self.hook=sys.excepthook
        self.out=Stream(self.stdout,bus,'OUTPUT');self.err=Stream(self.stderr,bus,'ERROR')
        sys.stdout=self.out;sys.stderr=self.err
        self.handler=Handler(bus);logging.getLogger().addHandler(self.handler)
        self.warning_hook=warnings.showwarning
        def warning(message,category,filename,lineno,file=None,line=None):
            if file is not None:
                return self.warning_hook(message,category,filename,lineno,file,line)
            text=warnings.formatwarning(message,category,filename,lineno,line)
            bus.emit(text,'WARNING')
            if self.stderr is not None:self.stderr.write(text);self.stderr.flush()
        self.new_warning_hook=warning;warnings.showwarning=warning
        def hook(kind,value,tb):
            text='未处理异常：'+''.join(traceback.format_exception(kind,value,tb));bus.emit(text,'ERROR')
            if self.stderr is not None:self.stderr.write(text+'\n');self.stderr.flush()
            # Qt slot failures must be visible without terminating the UI.
        self.new_hook=hook;sys.excepthook=hook
    def close(self):
        if sys.stdout is self.out:sys.stdout=self.stdout
        if sys.stderr is self.err:sys.stderr=self.stderr
        if sys.excepthook is self.new_hook:sys.excepthook=self.hook
        if warnings.showwarning is self.new_warning_hook:warnings.showwarning=self.warning_hook
        logging.getLogger().removeHandler(self.handler)
