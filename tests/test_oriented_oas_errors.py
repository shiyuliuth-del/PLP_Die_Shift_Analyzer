"""Independent readback of current-pose DIE outlines and oriented ERROR markers."""
import json,math,tempfile,threading,unittest
from contextlib import closing
from pathlib import Path
import numpy as np
import shapely
import gdstk
from die_shift_studio.oas_errors import rectangles
from die_shift_studio.oas_stream import Store,verify_crc


def posed_box(bounds,angle,pivot=(0.,0.),shift=(0.,0.)):
    x0,y0,x1,y1=bounds;c,s=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    return [[pivot[0]+c*(x-pivot[0])-s*(y-pivot[1])+shift[0],
             pivot[1]+s*(x-pivot[0])+c*(y-pivot[1])+shift[1]]
            for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]


def issue(corners,dies,angle):
    return dict(bounds_um=list(shapely.Polygon(corners).bounds),die_ids=dies,
                marker_corners_um=corners,marker_angle_deg=angle)


class OrientedMarkers(unittest.TestCase):
 def test_two_readers_preserve_pose_outlines_marker_direction_and_topcell_shift(self):
  import klayout.db as k
  dbu=.001;origin=(-20.,-30.);position=(1000.,2000.);shift=np.asarray(position)-origin
  corners=[posed_box((1,2,13,5),90,(4,-7),(17,-5)),posed_box((-8,10,4,13),-31,(100,200),(-11,19))]
  shared=posed_box((20,25,30,27),23)
  issues=[issue(corners[0],[1],90),issue(corners[1],[2],-31),issue(shared,[1,2],23)]
  with tempfile.TemporaryDirectory() as name:
   out=Path(name)/'final.oas';stage=Path(name)/'stage.oas';store=Store(name,dbu,error_layer=(65535,0),checkpoint_destination=stage)
   try:
    result=store.add('Package',{'128/0':[shapely.Polygon(c).wkb for c in corners]},position,origin,errors=issues,die_count=2)
    self.assertEqual(result['error_rectangles'],4);store.flush();store.write(out)
   finally:store.close()
   for path in (stage,out):
    self.assertTrue(verify_crc(path));lib=gdstk.read_oas(path);top=lib.top_level()[0]
    layout=k.Layout();layout.read(str(path));error=next(i for i in layout.layer_indexes() if layout.get_info(i).name=='ERROR')
    # Golden integer coordinates use the analytic pose above, not the writer.
    expected=k.Region()
    for c in corners:
     expected.insert(k.Polygon([k.Point(int(x),int(y)) for x,y in np.rint((np.asarray(c)+shift)/dbu)]))
    self.assertTrue((k.Region(layout.top_cell().begin_shapes_rec(layout.layer(128,0)))^expected).is_empty())
    for die,c,angle in ((1,corners[0],90),(2,corners[1],-31)):
     cell=next(c0 for c0 in lib.cells if c0.name==f'G_ERROR_Package_DIE_{die}')
     self.assertEqual(len(cell.polygons),2)
     raw=shapely.Polygon(c)
     p=next(shapely.Polygon(q.points) for q in cell.polygons if shapely.Polygon(q.points).covers(raw))
     self.assertTrue(raw.buffer(2*dbu).covers(p))
     # The long edge follows the requested direction, including a remote pivot.
     xy=np.asarray(p.exterior.coords)[:-1];edges=np.roll(xy,-1,axis=0)-xy;edge=edges[np.argmax(np.linalg.norm(edges,axis=1))]
     measured=math.degrees(math.atan2(edge[1],edge[0]));delta=(measured-angle+90)%180-90
     self.assertLess(abs(delta),.1)
     if angle%90:self.assertLess(p.area,p.envelope.area*.8)
     placed=shapely.Polygon(np.asarray(c)+shift)
     self.assertTrue(any(shapely.Polygon(q.points).covers(placed) for q in top.get_polygons(layer=65535,datatype=0)))
     self.assertEqual(k.Region(layout.cell(cell.name).shapes(error)).count(),2)
    # Shared-DIE witnesses are the identical physical quadrilateral in both cells.
    for die in (1,2):
     cell=next(c for c in lib.cells if c.name==f'G_ERROR_Package_DIE_{die}')
     self.assertTrue(any(shapely.Polygon(p.points).covers(shapely.Polygon(shared)) for p in cell.polygons))

 def test_subgrid_rotated_witness_survives_and_cyclic_duplicates_are_removed(self):
  points=posed_box((0,0,.00005,10),37,shift=(.0003,.0002))
  row=issue(points,[1,2],37);cycled=dict(row,marker_corners_um=points[2:]+points[:2])
  reversed_row=dict(row,marker_corners_um=points[::-1])
  groups=rectangles([row,cycled,reversed_row],2,.001)
  self.assertEqual({d:len(v) for d,v in groups.items()},{1:1,2:1})
  for blobs in groups.values():
   poly=shapely.from_wkb(blobs[0]);self.assertGreater(poly.area,0)
   self.assertTrue(poly.covers(shapely.Polygon(points)));self.assertLess(poly.area,poly.envelope.area*.01)

 def test_malformed_oriented_record_cannot_silently_fall_back_to_bounds(self):
  valid=issue(posed_box((0,0,4,1),30),[1],30)
  invalid=[None,[[0,0],[4,0],[4,1]],[[0,0],[4,0],[3,1],[0,1]],
           [[0,0],[4,1],[0,1],[4,0]],[[0,0]]*4,[[0,0],[4,0],[4,float('nan')],[0,1]]]
  for points in invalid:
   with self.subTest(points=points),self.assertRaises((ValueError,TypeError)):
    rectangles([dict(valid,marker_corners_um=points)],1,.001)
  with self.assertRaises(ValueError):rectangles([dict(valid,bounds_um=[-100,-100,100,100])],1,.001)
  with self.assertRaises(ValueError):rectangles([dict(valid,marker_angle_deg=float('nan'))],1,.001)

 def test_client_journal_preserves_corners_angle_and_pose_without_retransforming(self):
  from RealDataAnalyse.excel import generate_example,import_files
  from RealDataAnalyse.storage import Ledger
  from RealDataAnalyse.runner import Runner
  from test_v44 import FakeService
  marker=issue(posed_box((0,0,10,2),-21,(50,-80),(12,17)),[1,2],-21)
  class OrientedService(FakeService):
   def request(self,command,args=None,**kwargs):
    if command=='batch.end':
     store=Store(self.output,.001,error_layer=(65535,0))
     try:
      for cell,row in self.rows.items():store.add(cell,{},row['position_um'],(0,0),errors=[marker],die_count=2)
      receipt=store.write(self.output/self.filename)
     finally:store.close()
     self.batch_id=None;self.tasks['end']=receipt;return dict(status='accepted',task_id='end')
    reply=super().request(command,args,**kwargs)
    if command=='batch.submit':self.tasks[reply['task_id']].update(drc_count=1,error_rectangles=2,drc_issues=[marker])
    return reply
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();files=generate_example(root,count=2,die_count=2);database=root/'tasks.sqlite'
   import_files(files,database,2,2,threading.Event());service=OrientedService(root)
   result=Runner(database,root/'ipc.json',root/'case.json',root/'Output'/'final.oas',transport=service).run()
   self.assertEqual(result['error_rectangles'],4)
   with closing(Ledger(database)) as ledger:
    for row in ledger.db.execute('select result from packages'):
     self.assertEqual(json.loads(row[0])['drc_issues'][0],marker)
   self.assertEqual(list((root/'Output').iterdir()),[root/'Output'/'final.oas'])


class MarkerGridAngle(unittest.TestCase):
 def test_subgrid_angle_is_retained_by_oas_polygon_placement(self):
  corners=posed_box((0,0,.03,.015),.035,shift=(100,200))
  row=issue(corners,[1],.035)
  with tempfile.TemporaryDirectory() as name:
   out=Path(name)/'rotated.oas';store=Store(name,.001,error_layer=(65535,0))
   try:
    self.assertEqual(store.add('P',{},(10,20),(0,0),errors=[row],die_count=1)['error_rectangles'],1)
    store.write(out)
   finally:store.close()
   lib=gdstk.read_oas(out)
   die=next(c for c in lib.cells if c.name=='G_ERROR_P_DIE_1')
   shape=die.get_polygons()[0]
   points=np.asarray(shape.points);vector=points[1]-points[0]
   angle=math.degrees(math.atan2(vector[1],vector[0]))
   self.assertAlmostEqual(math.remainder(angle-.035,90),0.,places=5)
   self.assertTrue(shapely.Polygon(points).covers(shapely.Polygon(corners)))
   self.assertEqual(len(shape.points),4)

if __name__=='__main__':unittest.main()
