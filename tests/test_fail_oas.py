"""FAIL contains only real package outlines for irreparable connection failures."""
import tempfile,unittest
from pathlib import Path
import shapely
import gdstk
from die_shift_studio.oas_stream import Store,verify_crc
from die_shift_studio.oas_errors import choose_error_layer
from RealDataAnalyse.runner import Runner

class FailLayer(unittest.TestCase):
 def test_partial_and_final_fail_layer_contains_only_broken_package_outline(self):
  import klayout.db as k
  board=shapely.Polygon([(10,20),(30,20),(30,30),(20,30),(20,40),(10,40)])
  with tempfile.TemporaryDirectory() as name:
   out=Path(name)/'final.oas';stage=Path(name)/'stage.oas'
   error=choose_error_layer(['65535/0']);failure=choose_error_layer(['65535/0'],reserved=[error])
   self.assertEqual((error,failure),((65534,0),(65533,0)))
   store=Store(name,.001,error_layer=error,failure_layer=failure,checkpoint_destination=stage)
   marker=dict(bounds_um=[12,22,14,24],die_ids=[1])
   try:
    good=store.add('Good',{'1/0':[shapely.box(11,21,12,22).wkb]},(100,200),(10,20),errors=[],die_count=1)
    drc=store.add('DRC',{},(300,400),(10,20),errors=[marker],die_count=1)
    broken=store.add('Broken',{},(500,600),(10,20),errors=[marker],die_count=1,failure_outline=board.wkb)
    self.assertEqual((good['failed_packages'],drc['failed_packages'],broken['failed_packages']),(0,0,1))
    store.flush();result=store.write(out);self.assertEqual(result['failed_packages'],1)
   finally:store.close()
   for path in (stage,out):
    self.assertTrue(verify_crc(path));layout=k.Layout();layout.read(str(path))
    fail=next(i for i in layout.layer_indexes() if layout.get_info(i).name=='FAIL')
    self.assertEqual((layout.get_info(fail).layer,layout.get_info(fail).datatype),failure)
    expected=k.Region(k.Polygon([k.Point((x+490)*1000,(y+580)*1000) for x,y in list(board.exterior.coords)[:-1]]))
    self.assertTrue((k.Region(layout.top_cell().begin_shapes_rec(fail))^expected).is_empty())
    lib=gdstk.read_oas(path);top=lib.top_level()[0]
    polys=top.get_polygons(layer=failure[0],datatype=failure[1]);self.assertEqual(len(polys),1)
    self.assertEqual(polys[0].area(),board.area)
    self.assertLess(polys[0].area(),shapely.Polygon(polys[0].points).envelope.area)
    self.assertFalse(any(c.name in ('G_FAIL_Good','G_FAIL_DRC') for c in lib.cells))
    self.assertEqual(len(top.get_polygons(layer=error[0],datatype=error[1])),2)

 def test_no_failed_package_keeps_fail_empty(self):
  import klayout.db as k
  with tempfile.TemporaryDirectory() as name:
   store=Store(name,.001,error_layer=(65535,0),failure_layer=(65534,0))
   try:
    store.add('Normal',{},(0,0),(0,0),errors=[],die_count=1);receipt=store.write(Path(name)/'out.oas')
   finally:store.close()
   layout=k.Layout();layout.read(receipt['path']);fail=next(i for i in layout.layer_indexes() if layout.get_info(i).name=='FAIL')
   self.assertTrue(k.Region(layout.top_cell().begin_shapes_rec(fail)).is_empty());self.assertEqual(receipt['failed_packages'],0)

 def test_invalid_or_colliding_fail_geometry_is_rejected(self):
  with tempfile.TemporaryDirectory() as name:
   with self.assertRaises(ValueError):Store(name,.001,error_layer=(3,0),failure_layer=(3,0))
   store=Store(name,.001,error_layer=(65535,0),failure_layer=(65534,0))
   try:
    with self.assertRaises(ValueError):store.add('Bad',{'65534/0':[shapely.box(0,0,1,1).wkb]},(0,0),(0,0))
    with self.assertRaises(ValueError):store.add('Empty',{},(0,0),(0,0),failure_outline=shapely.Polygon().wkb)
    self.assertEqual(store.count,0)
   finally:store.close()

 def test_client_requires_fail_for_disconnected_packages_but_not_dimension_drc(self):
  base=dict(cell='P',fixed_rdl_drc_checked=True,drc_count=1,error_rectangles=1,connection_failure_count=1,
            partial_output=True,omitted_region_count=1,failed_packages=1,failure_layer=dict(name='FAIL',layer=65534,datatype=0))
  Runner.validate_pose(base,'P')
  for changes in (dict(failed_packages=0),dict(failed_packages=2),dict(failure_layer=None),dict(failure_layer=dict(name='ERROR'))):
   with self.assertRaises(ValueError):Runner.validate_pose(dict(base,**changes),'P')
  with self.assertRaises(ValueError):Runner.validate_pose(dict(base,connection_failure_count=0),'P')
  Runner.validate_pose(dict(base,connection_failure_count=0,failed_packages=0,failure_layer=None),'P')

 def test_legacy_disconnected_pending_commit_cannot_bypass_fail_validation(self):
  import json,threading
  from contextlib import closing
  from RealDataAnalyse.excel import generate_example,import_files
  from RealDataAnalyse.storage import Ledger,sha256
  with tempfile.TemporaryDirectory() as name:
   root=Path(name);db=root/'run.sqlite';files=generate_example(root,count=1,die_count=1)
   import_files(files,db,1,1,threading.Event());stage=root/'pending.oas';stage.write_bytes(b'legacy partial output')
   out=root/'final.oas';out.write_bytes(b'previous good output')
   with closing(Ledger(db)) as ledger:
    with ledger.db:ledger.db.execute('update packages set result=?',(json.dumps(dict(connection_failure_count=1)),))
    ledger.set('publish',dict(stage=str(stage),sha256=sha256(stage),receipt=dict(cells=1)))
    runner=Runner(db,root/'ipc.json',root/'project.json',out);runner.ledger=ledger
    with self.assertRaisesRegex(ValueError,'FAIL'):runner.publish()
   self.assertEqual(stage.read_bytes(),b'legacy partial output');self.assertEqual(out.read_bytes(),b'previous good output')

if __name__=='__main__':unittest.main()
