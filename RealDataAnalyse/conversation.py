"""Bounded IPC conversation queue; full, redacted messages go to rotating logs."""
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time


def redacted(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if str(k).lower() in ('token', 'authkey', 'password', 'secret') else redacted(v)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redacted(v) for v in value]
    return value


class Conversation:
    def __init__(self, capacity=1000, message_limit=16000, *, display=True):
        self.lock = threading.RLock()
        self.events = deque(maxlen=capacity)
        self.message_limit = message_limit
        self.dropped = 0
        self.handler = None
        self.root = None
        self.display = display

    def set_display(self, enabled):
        with self.lock:
            self.display = enabled
            if not enabled:
                self.events.clear()
                self.dropped = 0

    def set_root(self, root):
        root = Path(root).resolve()
        with self.lock:
            if root == self.root:
                return
            (root/'logs').mkdir(parents=True, exist_ok=True)
            if self.handler:
                self.handler.close()
            self.handler = RotatingFileHandler(root/'logs'/'IPC-conversation.log', maxBytes=16*1024**2, backupCount=4, encoding='utf8')
            self.handler.setFormatter(logging.Formatter('%(message)s'))
            self.root = root

    def record(self, direction, endpoint, value):
        value = redacted(value)
        now = time.time()
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), default=str)
        label = {'send': 'RealDataAnalyse → Die Shift Analyze', 'receive': 'Die Shift Analyze → RealDataAnalyse',
                 'error': 'RealDataAnalyse · 通信异常', 'lifecycle': 'RealDataAnalyse · 实例管理'}[direction]
        line = f'{time.strftime("%H:%M:%S", time.localtime(now))}.{int(now%1*1000):03d}  {label}  [{Path(endpoint).name}]  {payload}'
        with self.lock:
            if self.handler:
                self.handler.emit(logging.LogRecord('IPC', logging.INFO, '', 0, line, (), None))
            if not self.display:
                return
            if len(self.events) == self.events.maxlen:
                self.dropped += 1
            visible = line[:self.message_limit]
            if len(line) > self.message_limit:
                visible += ' … [界面截断，完整内容见项目 logs/IPC-conversation.log]'
            self.events.append(visible)

    def drain(self, limit=100):
        with self.lock:
            result = []
            if self.dropped:
                result.append(f'[界面队列达到上限，省略 {self.dropped} 条旧信息；完整交互按日志轮转保留]')
                self.dropped = 0
            for _ in range(min(limit, len(self.events))):
                result.append(self.events.popleft())
            return result

    def close(self):
        with self.lock:
            if self.handler:
                self.handler.close(); self.handler = None
