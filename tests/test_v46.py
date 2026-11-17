import unittest,tempfile,json
from pathlib import Path
import shapely,gdstk
from die_shift_studio.jobs import job_path,list_jobs,local_source,atomic_save
from die_shift_studio.oas_stream import Store,verify_crc
class V46Tests(unittest.TestCase):
 def test_branch_handle_excludes_exterior_sliver(self):
  from die_shift_studio.branch_regions import interior_interfaces
  source=shapely.box(0,0,100,30);domain=shapely.box(50,0,100,30)
  rigid=shapely.union_all([shapely.box(0,0,50,30),shapely.box(50,0,100,.00001)])
  seam=interior_interfaces(source,domain,[rigid],.001)
  self.assertLess(seam.length,30.01)
  self.assertLess(seam.hausdorff_distance(shapely.LineString([(50,0),(50,30)])),.005)
 def test_trace_does_not_prepare_shared_source(self):
  from die_shift_studio.reconstruction_route import trace
  source=shapely.box(0,0,100,30)
  self.assertIsNotNone(trace(source,(10,15),(90,15),10))
  self.assertFalse(shapely.is_prepared(source))
 def test_topology_queries_do_not_reuse_prepared_frame_geometry(self):
  import numpy as np
  from unittest.mock import patch
  from test_v40 import make_engine
  from die_shift_studio.core import Parameters
  from die_shift_studio.settings import ViewSettings
  from die_shift_studio.topology_routing import resolve
  shapes={'127/0':[shapely.box(-100,-100,300,100)],'128/0':[shapely.box(-15,-15,15,15)],
          '3/0':[shapely.box(-10,-10,10,10)],'4/0':[shapely.Point(0,0).buffer(3)],
          '5/0':[shapely.box(-10,-10,210,10)],'6/0':[shapely.Point(200,0).buffer(3)]}
  e,_,_=make_engine(shapes,Parameters(single_bound_x_um=5,single_bound_y_um=5,bound_angle_deg=0,annulus_um=3),ViewSettings())
  try:
   r=e.reconstructor;states=np.zeros((1,3));frame=r.frame(states,diagnostics=False,_raw=True)
   for node in r.model.nodes:shapely.prepare(node['poly'])
   for rows in frame['replacements'].values():
    for row in rows:shapely.prepare(row['geometry'])
   original=shapely.STRtree.query;seen=[]
   def query(tree,geometry,*args,**kwargs):
    if kwargs.get('predicate')=='intersects':
     seen.append(True);self.assertFalse(np.any(shapely.is_prepared(geometry)))
    return original(tree,geometry,*args,**kwargs)
   with patch.object(shapely.STRtree,'query',query):
    result=resolve(r,frame['replacements'],frame['pieces'],states)
   self.assertTrue(seen);self.assertFalse(result[-1])
  finally:e.close()
 def test_mixed_branch_point_selects_its_terminal_without_jump(self):
  from types import SimpleNamespace
  from die_shift_studio.branch_regions import editable_piece
  def member(y):
   plan=SimpleNamespace(route=shapely.LineString([(0,y),(100,y)]),length=100.,tolerance=.001)
   return dict(route_plan=plan,interval=(30.,80.),moved_cut=shapely.LineString([(30,y-10),(30,y+10)]),
               extra_cut=shapely.LineString([(80,y-10),(80,y+10)]),moving_terminals=shapely.Point(0,y).buffer(3))
  first,second=member(0),member(100)
  piece=dict(first,graph_members=(first,second),endpoint_weights=(1.,0.),
             moved_cut=shapely.GeometryCollection([shapely.Point(0,0),second['moved_cut']]))
  picked=editable_piece(piece,(.2,.1),'moved')
  self.assertIs(picked['route_plan'],first['route_plan'])
  self.assertEqual(picked['interval'],(0.,80.));self.assertEqual(first['interval'],(30.,80.))
  self.assertEqual(len(picked['drag_members']),1)
 def test_forcing_existing_source_reuses_its_paths(self):
  from test_v38 import fixture
  from dataclasses import replace
  from unittest.mock import patch
  import numpy as np
  e,_,_=fixture()
  try:
   r=e.reconstructor;r.frame(np.zeros((1,3)),diagnostics=False,_raw=True)
   plans=[q['plan'] for q in r._base_plans];self.assertTrue(plans)
   cfg=replace(r.settings,reconstruction_forced=(('5/0',0),))
   e.configure_reconstruction(True,cfg)
   with patch('die_shift_studio.reconstruction_route.trace',side_effect=AssertionError('unchanged source was retraced')):
    e.reconstructor._plans_for_all_poses(lambda *_:None)
   self.assertEqual(plans,[q['plan'] for q in e.reconstructor._base_plans])
  finally:e.close()
 def test_project_folder_and_legacy_discovery(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'input.gds';source.write_bytes(b'original')
   p=job_path(root/'Jobs','A');self.assertEqual(p.parent.name,'A')
   relative=local_source(p,source);atomic_save(p,dict(gds_path=relative,parameters={}))
   self.assertEqual((p.parent/relative).read_bytes(),source.read_bytes())
   atomic_save(root/'Jobs'/'legacy.json',dict(gds_path='input.gds',parameters={}))
   self.assertEqual(len(list_jobs(root/'Jobs')),2)
 def test_staged_oas_survives_close_without_end(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'stage.partial.oas';s=Store(t,.001,checkpoint_destination=path)
   s.add('P1',{'5/0':[shapely.box(0,0,20,20).wkb]},(100,200),(0,0));s.flush()
   self.assertTrue(verify_crc(path));self.assertEqual(len(gdstk.read_oas(path).top_level()[0].references),1)
   s.add('P2',{'5/0':[shapely.box(0,0,20,20).wkb]},(300,200),(0,0));s.flush();s.close()
   self.assertTrue(verify_crc(path));self.assertEqual(len(gdstk.read_oas(path).top_level()[0].references),2)
 def test_many_fixed_ports_also_count_as_complex(self):
  from test_v40 import make_engine
  from die_shift_studio.core import Parameters
  from die_shift_studio.settings import ViewSettings
  import numpy as np
  shapes={'127/0':[shapely.box(-500,-500,500,500)],'128/0':[shapely.box(60,-30,95,30)],
          '3/0':[shapely.box(65,-25,95,25)],'4/0':[shapely.Point(80,0).buffer(3)],
          '5/0':[shapely.box(-100,-50,100,50)],'6/0':[shapely.Point(x,0).buffer(3) for x in (-40,-30,-20)]}
  e,_,_=make_engine(shapes,Parameters(single_bound_x_um=35,single_bound_y_um=0,bound_angle_deg=0,annulus_um=5,moving_annulus_um=5),ViewSettings())
  try:
   e.reconstructor.frame(np.array([[35.,0,0]]));self.assertIn(('5/0',0),e.reconstructor.auto_following)
  finally:e.close()
 def test_complex_source_can_choose_rigid_following(self):
  import numpy as np
  from shapely.geometry import box,Point
  from test_v40 import make_engine
  from die_shift_studio.core import Parameters
  from die_shift_studio.settings import ViewSettings
  vias=[Point(80,y).buffer(3) for y in (-15,-5,5,15)]
  shapes={'127/0':[box(-500,-500,500,500)],'128/0':[box(60,-30,95,30)],'3/0':[box(65,-25,95,25)],'4/0':vias,'5/0':[box(-100,-50,100,50)],'6/0':[Point(-30,0).buffer(3)]}
  e,_,_=make_engine(shapes,Parameters(single_bound_x_um=35,single_bound_y_um=0,bound_angle_deg=0,annulus_um=5,moving_annulus_um=5),ViewSettings())
  try:
   frame=e.reconstructor.frame(np.array([[35.,0,0]]))
   self.assertIn(('5/0',0),e.reconstructor.auto_following)
   self.assertTrue(all(q['ignored_following'] for q in frame['pieces']))
  finally:e.close()

if __name__=='__main__':unittest.main()
