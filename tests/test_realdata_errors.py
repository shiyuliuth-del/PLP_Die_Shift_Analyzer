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
   store=Store(self.output,.001,error_layer=(65535,0))
   try:
    for i,(cell,row) in enumerate(self.rows.items()):
     issue=cell.endswith('002')
     store.add(cell,{'1/0':[box(0,0,1,1).wkb]},row['position_um'],(0,0),errors=[dict(bounds_um=[5,5,6,6],die_ids=[1])] if issue else [],die_count=2)
    receipt=store.write(self.output/self.filename)
   finally:store.close()
   self.batch_id=None;self.tasks['end']=receipt
   return dict(status='accepted',task_id='end')
  response=super().request(command,args,**kwargs)
  if command=='batch.submit' and args['cell'].endswith('002'):
   self.tasks[response['task_id']].update(connection_failure_count=1,drc_count=2,partial_output=True,omitted_region_count=1,error_rectangles=1)
  return response

class ErrorContracts(unittest.TestCase):
 def test_valid_failure_valid_sequence_publishes_all_and_records_warning(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();paths=generate_example(root,count=3,die_count=2);db=root/'run.sqlite'
   import_files(paths,db,2,2,threading.Event());service=MixedService(root)
   result=Runner(db,root/'ipc.json',root/'case.json',root/'Output/final.oas',transport=service).run()
   self.assertEqual(result['issue_packages'],1);self.assertEqual(result['error_rectangles'],1)
   self.assertEqual(service.begin_count,1)
   with closing(Ledger(db)) as ledger:
    self.assertEqual(ledger.counts(),{'committed':2,'committed_with_issues':1})
   import gdstk
   lib=gdstk.read_oas(result['output']);self.assertEqual(len(lib.top_level()[0].references),3)
   self.assertEqual(len(lib.top_level()[0].get_polygons(layer=65535,datatype=0)),1)
   self.assertEqual(list((root/'Output').iterdir()),[root/'Output/final.oas'])
 def test_unconfirmed_failure_or_missing_markers_never_masquerades_as_success(self):
  base=dict(cell='P',fixed_rdl_drc_checked=True,connection_failure_count=1,drc_count=1,partial_output=True,omitted_region_count=1,error_rectangles=1)
  Runner.validate_pose(base,'P')
  for key,value in [('partial_output',False),('omitted_region_count',0),('error_rectangles',0),('fixed_rdl_drc_checked',False)]:
   with self.assertRaises(ValueError):Runner.validate_pose(dict(base,**{key:value}),'P')
 def test_drc_only_keeps_copper_without_requiring_partial_deletion(self):
  Runner.validate_pose(dict(cell='P',fixed_rdl_drc_checked=True,drc_count=3,error_rectangles=2),'P')

if __name__=='__main__':unittest.main()
