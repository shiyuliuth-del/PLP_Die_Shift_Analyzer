"""Independent OAS readers prove ERROR name, DIE hierarchy and unchanged copper."""
import tempfile,unittest
from pathlib import Path
import shapely,gdstk
from die_shift_studio.oas_stream import Store,verify_crc
from die_shift_studio.oas_errors import choose_error_layer,rectangles

class ErrorOutputTests(unittest.TestCase):
 def test_flat_checkpoint_matches_expanded_hierarchy_without_package_cells(self):
  import klayout.db as k
  from shapely.affinity import rotate
  from die_shift_studio.oas_stream import polygons
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   board=shapely.box(10.0005,20,30.0005,40)
   hole=shapely.box(15,25,18,30)
   copper=board.difference(hole)
   layers={'1/0':[copper.wkb],'128/0':[board.wkb]}
   witness=rotate(shapely.box(15,22,17,24),33)
   issues=[dict(bounds_um=[13,22,14,23],die_ids=[1]),
           dict(bounds_um=witness.bounds,marker_corners_um=list(witness.exterior.coords)[:4],
                die_ids=[1],severity='serious')]
   template=(polygons({'7/3':[shapely.box(0,0,1,1).wkb]},.001),{'7/3':'Template'})
   outputs={}
   for hierarchical in (True,False):
    final=root/f'{hierarchical}.oas'
    checkpoint=root/f'{hierarchical}.partial.oas'
    store=Store(root,.001,error_layer=(65535,0),error2_layer=(65534,0),failure_layer=(65533,0),
                checkpoint_destination=checkpoint,template=template,create_cells=hierarchical)
    try:
     store.add('Measured',layers,(-100.0005,130),board.bounds[:2],errors=issues,die_count=1,
               failure_outline=board.wkb)
     store.add_nominal('NominalA',(400,600),board.bounds[:2],lambda:layers)
     store.add_nominal('NominalB',(700,-930),board.bounds[:2],lambda:layers)
     receipt=store.write(final)
    finally:store.close()
    self.assertIs(receipt['create_cells'],hierarchical)
    self.assertEqual((receipt['cells'],receipt['nominal_packages'],receipt['failed_packages']),(3,2,1))
    self.assertEqual((receipt['error_rectangles'],receipt['error2_rectangles']),(2,1))
    outputs[hierarchical]=[]
    for filename in (final,checkpoint):
     self.assertTrue(verify_crc(filename))
     layout=k.Layout();layout.read(str(filename));top=layout.top_cell()
     names={cell.name for cell in layout.each_cell()}
     if not hierarchical:
      self.assertEqual(names,{'TopCell','G_TEMPLATE'})
      self.assertEqual([instance.cell.name for instance in top.each_inst()],['G_TEMPLATE'])
     outputs[hierarchical].append({(info.layer,info.datatype):k.Region(top.begin_shapes_rec(layer)).flatten()
                                  for layer in layout.layer_indexes() for info in [layout.get_info(layer)]})
   for baseline,flat in zip(outputs[True],outputs[False]):
    self.assertEqual(baseline.keys(),flat.keys())
    for key in baseline:
     self.assertTrue((baseline[key]^flat[key]).is_empty(),key)

 def test_named_layer_die_rectangles_and_package_placement(self):
  import klayout.db as k
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'out.oas';checkpoint=Path(tmp)/'partial.oas'
   layer=choose_error_layer(['1/0','65535/0']);self.assertEqual(layer,(65534,0))
   s=Store(tmp,.001,checkpoint_destination=checkpoint,error_layer=layer)
   try:
    copper=shapely.box(0,0,20,10)
    issues=[dict(bounds_um=[1,2,3,4],die_ids=[1]),dict(bounds_um=[-4,0,-4,2],die_ids=[2,3]),dict(bounds_um=[7,8,9,10],die_ids=[0])]
    pose=s.add('P',{'1/0':[copper.wkb]},(1000,2000),(10,20),errors=issues,die_count=3)
    self.assertEqual(pose['error_rectangles'],4)
    s.flush();self.assertTrue(verify_crc(checkpoint));result=s.write(target)
   finally:s.close()
   self.assertEqual(result['error_rectangles'],4)
   for path in (target,checkpoint):
    layout=k.Layout();layout.read(str(path));info=[i for i in layout.layer_infos() if i.name=='ERROR']
    self.assertEqual(len(info),1);self.assertEqual((info[0].layer,info[0].datatype),layer)
    lib=gdstk.read_oas(path);top=lib.top_level()[0];p=next(c for c in lib.cells if c.name=='P')
    self.assertEqual(tuple(top.references[0].origin),(990,1980))
    self.assertEqual(sum(q.area() for q in p.get_polygons(layer=1,datatype=0)),copper.area)
    dies=[c for c in lib.cells if c.name.startswith('G_ERROR_')]
    self.assertEqual(len(dies),4)
    for c in dies:self.assertEqual(len(c.polygons),1)
    die1=next(c for c in dies if c.name.endswith('_DIE_1'))
    self.assertEqual(tuple(map(tuple,die1.polygons[0].bounding_box())),((1,2),(3,4)))
    self.assertEqual(len(top.get_polygons(layer=layer[0],datatype=0)),4)
 def test_unknown_ownership_and_bad_bounds_are_rejected(self):
  for row in [dict(bounds_um=[0,0,1,1]),dict(bounds_um=[0,0,float('nan'),1],die_ids=[1]),dict(bounds_um=[2,0,1,1],die_ids=[1])]:
   with self.assertRaises(ValueError):rectangles([row],3,.001)
 def test_duplicate_rectangles_are_not_duplicated_per_die(self):
  row=dict(bounds_um=[0,0,1,1],die_ids=[1,1,2]);self.assertEqual({k:len(v) for k,v in rectangles([row,row],2,.001).items()},{1:1,2:1})

 def test_subgrid_point_and_collinear_slivers_drop_without_changing_good_copper(self):
  from shapely.geometry import Polygon,box
  with tempfile.TemporaryDirectory() as temp:
   target=Path(temp)/'out.oas';s=Store(temp,.001,error_layer=(65535,0))
   try:
    good=box(10,10,20,20)
    tiny=box(.0001,.0001,.0002,.0002)
    collinear=Polygon([(2,0),(3,.0002),(4,0)])
    result=s.add('P',{'1/0':[good.wkb,tiny.wkb,collinear.wkb]},(0,0),(0,0),errors=[],die_count=1)
    self.assertEqual(result['discarded_subgrid_fragments'],2)
    receipt=s.write(target);self.assertEqual(receipt['discarded_subgrid_fragments'],2)
   finally:s.close()
   lib=gdstk.read_oas(target);polys=lib.top_level()[0].get_polygons(layer=1,datatype=0)
   self.assertEqual(len(polys),1);self.assertEqual(polys[0].area(),100)
   self.assertEqual(tuple(map(tuple,polys[0].bounding_box())),((10,10),(20,20)))
   self.assertTrue(verify_crc(target))
 def test_invalid_nondegenerate_copper_remains_rejected(self):
  from shapely.geometry import Polygon
  from die_shift_studio.oas_stream import polygons
  with self.assertRaisesRegex(ValueError,'invalid copper'):
   polygons({'1/0':[Polygon([(0,0),(2,2),(0,2),(2,0)]).wkb]},.001)

if __name__=='__main__':unittest.main()
