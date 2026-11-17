"""Durable per-project catalogues: first-create concurrency and real lock paths."""
import json,os,sqlite3,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch
from die_shift_studio.measured_issues import write,page
from die_shift_studio.resources import cancellation_scope

class IssueConcurrencyTests(unittest.TestCase):
 def test_concurrent_first_writers_and_readers_keep_all_task_rows(self):
  with tempfile.TemporaryDirectory() as tmp:
   for cycle in range(8):
    path=Path(tmp)/f'{cycle}.sqlite';barrier=threading.Barrier(8)
    def worker(i):
     barrier.wait();rows=[{'number':j,'owner':i} for j in range(257)]
     reply=write(path,str(i),dict(drc_issues=rows,drc_count=len(rows)))
     items=page(reply['issue_catalog'],0,512)['items']
     self.assertEqual(items,rows);return reply['issue_catalog']
    with ThreadPoolExecutor(8) as pool:catalogs=list(pool.map(worker,range(8)))
    with closing(sqlite3.connect(path)) as db:
     self.assertEqual(db.execute('select count(*) from catalogs').fetchone()[0],8)
     self.assertEqual(db.execute('select count(*) from issues').fetchone()[0],8*257)
    self.assertEqual(page(catalogs[-1],256,1)['items'],[{'number':256,'owner':7}])
 def test_separate_processes_initialize_one_catalogue_without_losing_tasks(self):
  script="""import sys,time
from pathlib import Path
from die_shift_studio.measured_issues import write,page
path,owner,signal=map(Path,sys.argv[1:]);Path(str(signal)+'.'+owner.name).write_text('ready')
while not signal.exists():time.sleep(.01)
r=write(path,owner.name,{'drc_issues':[{'owner':owner.name,'n':i} for i in range(100)]})
assert page(r['issue_catalog'])['total']==100
"""
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);path=folder/'shared.sqlite';signal=folder/'go';processes=[]
   try:
    for i in range(4):processes.append(subprocess.Popen([sys.executable,'-c',script,str(path),str(i),str(signal)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True))
    deadline=time.monotonic()+30
    while not all(Path(str(signal)+'.'+str(i)).exists() for i in range(4)):
     if time.monotonic()>deadline or any(p.poll() is not None for p in processes):self.fail('child failed to reach catalogue barrier')
     time.sleep(.01)
    signal.write_text('start')
    for process in processes:
     output=process.communicate(timeout=40)[0];self.assertEqual(process.returncode,0,output)
    with closing(sqlite3.connect(path)) as db:
     self.assertEqual(db.execute('select count(*) from catalogs').fetchone()[0],4)
     self.assertEqual(db.execute('select count(*) from issues').fetchone()[0],400)
   finally:
    for process in processes:
     if process.poll() is None:process.kill()
     process.communicate(timeout=5)
 def test_external_writer_wait_then_complete_and_expired_wait_is_explicit(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'issues.sqlite';write(path,'base',{'drc_issues':[]})
   with closing(sqlite3.connect(path)) as blocker,ThreadPoolExecutor(1) as pool:
    blocker.execute('BEGIN IMMEDIATE')
    future=pool.submit(write,path,'waiting',{'drc_issues':[{'n':1}]})
    time.sleep(.35);self.assertFalse(future.done());blocker.rollback()
    reply=future.result(timeout=5);self.assertEqual(page(reply['issue_catalog'])['items'],[{'n':1}])
    blocker.execute('BEGIN IMMEDIATE')
    with patch('die_shift_studio.measured_issues._LOCK_WAIT_SECONDS',.15):
     with self.assertRaises(TimeoutError):write(path,'expired',{'drc_issues':[{'n':2}]})
    blocker.rollback()
   with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute("select count(*) from catalogs where task_id='expired'").fetchone()[0],0)
 def test_export_reuses_full_catalogue_instead_of_rewriting_inline_page(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'issues.sqlite';rows=[{'n':i} for i in range(600)]
   preview=write(path,'preview',{'drc_issues':rows,'drc_count':len(rows)})
   self.assertFalse(preview['issues_complete']);self.assertEqual(len(preview['drc_issues']),128)
   exported=write(path,'export',dict(preview,path='out.gds'))
   self.assertEqual(exported['issue_catalog']['total'],600);self.assertFalse(exported['issues_complete'])
   self.assertEqual(page(exported['issue_catalog'],512,100)['items'],rows[512:])
   with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('select count(*) from issues').fetchone()[0],600)
   with self.assertRaises(ValueError):write(path,'bad',{'drc_issues':rows[:128],'issues_complete':False})
 def test_non_lock_database_errors_are_not_retried(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'issues.sqlite'
   with patch('die_shift_studio.measured_issues.sqlite3.connect',side_effect=sqlite3.OperationalError('disk I/O error')) as connect:
    with self.assertRaisesRegex(sqlite3.OperationalError,'disk I/O error'):write(path,'bad',{'drc_issues':[]})
    self.assertEqual(connect.call_count,1)
   self.assertFalse(path.exists())
   result=write(path,'good',{'drc_issues':[]});self.assertEqual(page(result['issue_catalog'])['total'],0)
 def test_cancelled_wait_and_failed_transaction_do_not_replace_prior_rows(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'issues.sqlite';prior=write(path,'same',{'drc_issues':[{'n':'old'}]})
   stop=threading.Event()
   def waiting():
    with cancellation_scope(stop):return write(path,'same',{'drc_issues':[{'n':'new'}]})
   with closing(sqlite3.connect(path)) as blocker,ThreadPoolExecutor(1) as pool:
    blocker.execute('BEGIN IMMEDIATE');future=pool.submit(waiting);time.sleep(.1);stop.set()
    with self.assertRaises(InterruptedError):future.result(timeout=2)
    blocker.rollback()
   self.assertEqual(page(prior['issue_catalog'])['items'],[{'n':'old'}])
   with closing(sqlite3.connect(path)) as db,db:
    db.execute("CREATE TRIGGER reject_catalog BEFORE INSERT ON catalogs BEGIN SELECT RAISE(ABORT,'injected disk-contract failure'); END")
   with self.assertRaises(sqlite3.IntegrityError):write(path,'same',{'drc_issues':[{'n':'bad'}]})
   self.assertEqual(page(prior['issue_catalog'])['items'],[{'n':'old'}])
   path.unlink();self.assertFalse(path.exists())
if __name__=='__main__':unittest.main()
