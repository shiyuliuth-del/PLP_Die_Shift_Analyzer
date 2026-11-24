"""Durable, bounded-memory run ledger. No executable serialization."""
import hashlib
from contextlib import closing
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import time
import uuid


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def sha256(path, stop=None):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if stop is not None and stop.is_set():
                raise InterruptedError('已停止文件校验')
            h.update(chunk)
    return h.hexdigest()


def read_task_metadata(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return {key: json.loads(value) for key, value in db.execute('select key,value from meta')}


class Lease:
    """OS lifetime lock, automatically released after process death."""
    def __init__(self, path):
        self.file = open(str(path) + '.lock', 'a+b')
        try:
            # Windows byte locks also prohibit a competing write before locking.
            self.file.seek(0, 2)
            if self.file.tell() == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('其他 RealDataAnalyse 正在使用此任务或输出文件') from None

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Ledger:
    def __init__(self, path):
        self.path = Path(path).resolve()
        existing = self.path.exists()
        self.db = sqlite3.connect(self.path, timeout=30)
        if existing:
            try:
                schema = self.db.execute("select value from meta where key='schema'").fetchone()
                run_id = self.db.execute("select value from meta where key='run_id'").fetchone()
                if not schema or json.loads(schema[0]) != 1 or not run_id:
                    raise ValueError('Invalid ledger metadata')
                uuid.UUID(hex=json.loads(run_id[0]))
            except (sqlite3.Error, ValueError, TypeError, AttributeError):
                self.db.close()
                raise ValueError('不是受支持的RealDataAnalyse任务数据库；未修改所选文件') from None
        self.db.execute('pragma journal_mode=WAL')
        self.db.execute('pragma synchronous=FULL')
        self.db.executescript('''
            create table if not exists meta(key text primary key, value text not null);
            create table if not exists measurements(
                package text, die integer, x real, y real, payload text, source text, row_number integer,
                primary key(package,die));
            create table if not exists packages(
                seq integer primary key, package text unique, cell text unique,
                payload text, state text default 'pending', task text, result text);
            create table if not exists events(
                seq integer primary key, created real, level text, message text);
            create index if not exists package_state on packages(state);
        ''')
        if self.get('schema') is None:
            self.set('schema', 1)
            self.set('run_id', uuid.uuid4().hex)
        if self.get('schema') != 1:
            raise ValueError('不支持的任务数据库版本')

    def get(self, key, default=None):
        row = self.db.execute('select value from meta where key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('insert or replace into meta values(?,?)', (key, dumps(value)))

    def store_receipts(self, rows, counts, *, reset=False):
        with self.db:
            if reset:
                self.db.execute("update packages set state='pending',task=null,result=null")
            self.db.executemany('update packages set state=?,task=?,result=? where seq=?', rows)
            self.db.executemany('insert or replace into meta values(?,?)',
                                ((key, dumps(value)) for key, value in counts.items()))

    def event(self, message, level='INFO'):
        with self.db:
            self.db.execute('insert into events(created,level,message) values(?,?,?)', (time.time(), level, message))
        logging.getLogger('RealDataAnalyse').log(getattr(logging, level), message)

    def counts(self):
        return dict(self.db.execute('select state,count(*) from packages group by state'))

    def close(self):
        self.db.close()


def configure_logging(root):
    folder = Path(root) / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('RealDataAnalyse')
    logger.setLevel(logging.INFO)
    destination = (folder / 'RealDataAnalyse.log').resolve()
    for old in list(logger.handlers):
        if isinstance(old, RotatingFileHandler) and Path(old.baseFilename).resolve() != destination:
            logger.removeHandler(old)
            old.close()
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename).resolve() == destination for h in logger.handlers):
        handler = RotatingFileHandler(folder / 'RealDataAnalyse.log', maxBytes=8 * 1024**2, backupCount=8, encoding='utf8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s %(message)s'))
        logger.addHandler(handler)
    return logger
