"""Mixed valid/problem package receipts continue only under explicit partial policy."""
import tempfile,threading,unittest,json
from pathlib import Path
from contextlib import closing
from RealDataAnalyse.excel import generate_example,import_files
from RealDataAnalyse.runner import Runner
from RealDataAnalyse.storage import Ledger
from test_v44 import FakeService

class MixedService(FakeService):
 def request(self,command,args=None,**kwargs):
  if command=='batch.end':
   from die_shift_studio.oas_stream import Store
   from shapely.geometry import box
   store=Store(self.output,.001,error_layer=(65535,0),failure_layer=(65534,0))
   try:
    for i,(cell,row) in enumerate(self.rows.items()):
     issue=cell.endswith('002')
     store.add(cell,{'1/0':[box(0,0,1,1).wkb]},row['position_um'],(0,0),errors=[dict(bounds_um=[5,5,6,6],die_ids=[1])] if issue else [],die_count=2,failure_outline=box(0,0,10,10).wkb if issue else None)
    receipt=store.write(self.output/self.filename)
   finally:store.close()
   self.batch_id=None;self.tasks['end']=receipt
   return dict(status='accepted',task_id='end')
  response=super().request(command,args,**kwargs)
  if command=='batch.submit' and args['cell'].endswith('002'):
   self.tasks[response['task_id']].update(connection_failure_count=1,drc_count=2,partial_output=True,omitted_region_count=1,error_rectangles=1,failed_packages=1,failure_layer=dict(name='FAIL',layer=65534,datatype=0))
  return response

class ErrorContracts(unittest.TestCase):
 def test_valid_failure_valid_sequence_publishes_all_and_records_warning(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();paths=generate_example(root,count=3,die_count=2);db=root/'run.sqlite'
   import_files(paths,db,2,2,threading.Event());service=MixedService(root)
   result=Runner(db,root/'ipc.json',root/'case.json',root/'Output/final.oas',transport=service).run()
   self.assertEqual(result['issue_packages'],1);self.assertEqual(result['error_rectangles'],1);self.assertEqual(result['failed_packages'],1)
   self.assertEqual(service.begin_count,1)
   with closing(Ledger(db)) as ledger:
    self.assertEqual(ledger.counts(),{'committed':2,'committed_with_issues':1})
   import gdstk
   lib=gdstk.read_oas(result['output']);self.assertEqual(len(lib.top_level()[0].references),3)
   self.assertEqual(len(lib.top_level()[0].get_polygons(layer=65535,datatype=0)),1)
   self.assertEqual(list((root/'Output').iterdir()),[root/'Output/final.oas'])
 def test_unconfirmed_failure_or_missing_markers_never_masquerades_as_success(self):
  base=dict(cell='P',fixed_rdl_drc_checked=True,connection_failure_count=1,drc_count=1,partial_output=True,omitted_region_count=1,error_rectangles=1,failed_packages=1,failure_layer=dict(name='FAIL',layer=65534,datatype=0))
  Runner.validate_pose(base,'P')
  for key,value in [('partial_output',False),('omitted_region_count',0),('error_rectangles',0),('fixed_rdl_drc_checked',False)]:
   with self.assertRaises(ValueError):Runner.validate_pose(dict(base,**{key:value}),'P')
 def test_drc_only_keeps_copper_without_requiring_partial_deletion(self):
  Runner.validate_pose(dict(cell='P',fixed_rdl_drc_checked=True,drc_count=3,error_rectangles=2),'P')

 def test_catalogue_timeout_replays_all_packages_without_duplicate_output(self):
  from RealDataAnalyse.transport import RemoteError
  class LockOnce(FakeService):
   def request(self,command,args=None,**kwargs):
    response=super().request(command,args,**kwargs)
    if command=='batch.submit' and self.begin_count==1 and args['cell'].endswith('001'):
     self.tasks[response['task_id']]=RemoteError(dict(status='error',error=dict(type='TimeoutError',message='diagnostic catalogue remained busy')))
    return response
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();files=generate_example(root,count=3,die_count=2);db=root/'run.sqlite'
   import_files(files,db,2,2,threading.Event());service=LockOnce(root)
   out=root/'Output'/'final.oas';out.parent.mkdir();out.write_bytes(b'previous verified output')
   result=Runner(db,root/'ipc.json',root/'case.json',out,retries=1,transport=service).run()
   self.assertEqual(result['state'],'completed');self.assertEqual(service.begin_count,2);self.assertEqual(service.launched,0)
   with closing(Ledger(db)) as ledger:
    self.assertEqual(ledger.get('attempt'),2);self.assertEqual(ledger.counts(),{'committed':3})
    self.assertTrue(ledger.db.execute("select 1 from events where message like '%等待后补算整批%'").fetchone())
   import gdstk
   self.assertEqual(len(gdstk.read_oas(out).top_level()[0].references),3)
   self.assertEqual(list(out.parent.iterdir()),[out])
   self.assertTrue(any(p.is_file() and p.read_bytes()==b'previous verified output' for p in (root/'Cache'/'History').rglob('*')))

 def test_memory_retry_reduces_actual_lanes_not_requested_limit(self):
  from RealDataAnalyse.transport import RemoteError
  class LimitedService(FakeService):
   limits=[]
   def request(self,command,args=None,**kwargs):
    response=super().request(command,args,**kwargs)
    if command=='output.configure':
     self.limits.append(args['max_threads'])
    if command=='batch.submit' and self.begin_count==1 and args['cell'].endswith('001'):
     self.tasks[response['task_id']]=RemoteError(dict(status='error',error=dict(type='MemoryError',message='shared budget')))
    return response
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();files=generate_example(root,count=3,die_count=2);db=root/'run.sqlite'
   import_files(files,db,2,2,threading.Event());service=LimitedService(root)
   result=Runner(db,root/'ipc.json',root/'case.json',root/'final.oas',workers=12,retries=1,transport=service).run()
   self.assertEqual(result['state'],'completed')
   self.assertEqual(service.limits,[12,1])

 def test_non_lock_catalogue_failure_preserves_old_output_without_retry(self):
  from RealDataAnalyse.transport import RemoteError
  class DiskFailure(FakeService):
   def request(self,command,args=None,**kwargs):
    response=super().request(command,args,**kwargs)
    if command=='batch.submit':
     self.tasks[response['task_id']]=RemoteError(dict(status='error',error=dict(type='OperationalError',message='disk I/O error')))
    return response
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();files=generate_example(root,count=3,die_count=2);db=root/'run.sqlite'
   import_files(files,db,2,2,threading.Event());service=DiskFailure(root);out=root/'final.oas';out.write_bytes(b'old output')
   with self.assertRaises(RemoteError):Runner(db,root/'ipc.json',root/'case.json',out,retries=2,transport=service).run()
   self.assertEqual(out.read_bytes(),b'old output');self.assertEqual(service.begin_count,1)
   with closing(Ledger(db)) as ledger:
    self.assertEqual(ledger.get('state'),'blocked')
    self.assertTrue(ledger.db.execute("select 1 from events where level='ERROR'").fetchone())

 def test_plan_warning_message_preserves_numeric_progress(self):
  from unittest.mock import Mock,patch
  from RealDataAnalyse.plans import Plans,PlanRunner,capture
  from RealDataAnalyse.excel import DEFAULT_PROFILE
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();files=generate_example(root,count=1,die_count=2)
   (root/'source.gds').write_bytes(b'fixture');(root/'case.json').write_text(json.dumps(dict(gds_path='source.gds')))
   config=dict(folder=str(root),project=str(root/'case.json'),files=[dict(path=p,enabled=True) for p in files],profile=DEFAULT_PROFILE,controls=dict(dies=2,workers=1,memory=512,retries=0,timeout=0))
   plans=Plans(root/'plans.sqlite');pid=plans.add(capture(config,threading.Event()));values=[]
   transport=Mock();transport.owns_descriptor.return_value=False
   class ReportingRunner:
    def __init__(self,*args,**kwargs):self.report=args[9]
    def run(self):
     self.report(dict(text='批次1 · 完成5/10',current=5,total=10))
     self.report('封装含问题，正常部分已保留')
     return dict(state='completed')
   try:
    with patch('RealDataAnalyse.plans.connect',return_value=(transport,{},False)),patch('RealDataAnalyse.plans.Runner',ReportingRunner):
     PlanRunner(plans,progress=values.append).run_one(plans.get(pid))
    self.assertEqual(values[-1]['current'],values[-2]['current'])
    self.assertEqual(values[-1]['current'],55)
   finally:
    from RealDataAnalyse.storage import configure_logging
    configure_logging(Path(__file__).resolve().parents[1])

if __name__=='__main__':unittest.main()
