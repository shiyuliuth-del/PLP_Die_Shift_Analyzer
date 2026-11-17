"""V43 contracts: source area filtering, local IPC, exact hierarchical output."""
import unittest,tempfile,json,threading
from pathlib import Path
from dataclasses import replace
import numpy as np,shapely,gdstk
from shapely.geometry import box,Point
from test_v38 import fixture
from die_shift_studio.core import read_gds,Parameters
from die_shift_studio.settings import ViewSettings


class V43Tests(unittest.TestCase):
 def test_oas_crc_corruption_and_empty_batch_keep_old_destination(self):
  from die_shift_studio.oas_stream import Store,verify_crc
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'existing.oas';target.write_bytes(b'old')
   store=Store(tmp,.001)
   try:
    with self.assertRaises(ValueError):store.write(target)
    self.assertEqual(target.read_bytes(),b'old')
    store.add('P',{'1/0':[box(0,0,1,1).wkb]},(0,0),(0,0));store.write(target)
    self.assertTrue(verify_crc(target));changed=bytearray(target.read_bytes());changed[40]^=1;target.write_bytes(changed)
    self.assertFalse(verify_crc(target))
    # Actual rename/delete, not just a checksum success assertion: Windows
    # rejects this when a reader has leaked its open file descriptor.
    renamed=target.with_name('renamed.oas');target.rename(renamed);renamed.unlink()
   finally:store.close()
 def test_uniform_candidates_precede_long_taper_when_both_feasible(self):
  from die_shift_studio.reconstruction_route import RoutePlan
  # Longer horizontal taper plus a shorter constant-width vertical run.
  route=shapely.LineString([(0,0),(500,0),(500,300)])
  taper=shapely.Polygon([(-20,-15),(500,-40),(520,0),(500,40),(-20,15)])
  source=shapely.union_all([taper,box(480,0,520,300),Point(0,0).buffer(20),Point(500,300).buffer(25)])
  plan=RoutePlan('5/0',0,0,(('4/0',0),),source,route,Point(0,0).buffer(10),(Point(500,300).buffer(10),),.001,3.)
  part=plan.choose(0)
  self.assertIsNotNone(part)
  self.assertGreater(part['a'],500)
  self.assertLess(plan.width_variation(part['a'],part['b']),.05)
 def test_oas_stream_holes_reuse_origins_and_integer_endpoints(self):
  from die_shift_studio.oas_stream import Store
  import klayout.db as db
  with tempfile.TemporaryDirectory() as tmp:
   p=box(-5,-5,5,5).difference(box(-1,-1,1,1));s=Store(tmp,.001,1024**2)
   for i in range(70):s.add('P'+str(i),{'5/0':[p.wkb]},(i*20,100),(-5,-5))
   target=Path(tmp)/'out.oas';result=s.write(target);s.close()
   self.assertEqual(result['unique_geometry_cells'],1)
   lib=gdstk.read_oas(target);top=lib.top_level()[0];self.assertEqual(len(top.references),70)
   self.assertAlmostEqual(top.area(),96*70,places=7)
   layout=db.Layout();layout.read(str(target));r=db.Region(layout.top_cell().begin_shapes_rec(layout.layer(5,0)))
   self.assertAlmostEqual(r.area()*layout.dbu**2,96*70,places=7)
   expected=db.Region()
   for i in range(70):
    shell=db.DPolygon([db.DPoint(x+i*20+5,y+105) for x,y in p.exterior.coords[:-1]])
    for ring in p.interiors:shell.insert_hole([db.DPoint(x+i*20+5,y+105) for x,y in ring.coords[:-1]])
    expected.insert(shell.to_itype(layout.dbu))
   self.assertTrue((r^expected).is_empty())
   np.testing.assert_allclose(top.bounding_box(),[[0,100],[1390,110]])
 def test_area_filter_stable_project_indices_and_hole_threshold(self):
  from die_shift_studio.gds_merge import restore_project_geometry,winding_outline
  from die_shift_studio.area_filter import canonical_project
  with tempfile.TemporaryDirectory() as tmp:
   lib=gdstk.Library();c=lib.new_cell('TOP');g=box(10,0,30,20).difference(box(15,5,16,6))
   c.add(gdstk.rectangle((0,0),(1,1),layer=5),gdstk.Polygon(winding_outline(g),layer=5))
   path=Path(tmp)/'input.oas';lib.write_oas(path)
   d=read_gds(path);job=dict(parameters={'minimum_area_um2':2},geometry_indexing='merged-v39',settings={'reconstruction_ignored':[['5/0',1]]})
   f,p=restore_project_geometry(d,job)
   self.assertEqual(len(f.layers['5/0']),1);self.assertEqual(p['settings']['reconstruction_ignored'],[('5/0',0)])
   self.assertAlmostEqual(shapely.Polygon(f.layers['5/0'][0]).area,400)
   saved=canonical_project(f,p);self.assertEqual(saved['settings']['reconstruction_ignored'],[('5/0',1)])
   again,_=restore_project_geometry(read_gds(path),saved);np.testing.assert_array_equal(f.layers['5/0'][0],again.layers['5/0'][0])
   saved['parameters']['minimum_area_um2']=0;unfiltered,_=restore_project_geometry(read_gds(path),saved)
   self.assertEqual(len(unfiltered.layers['5/0']),2)
 def test_ignored_defaults_static_and_explicit_whole_follow(self):
  from die_shift_studio.reconstruction import transform
  e,_,_=fixture()
  try:
   z=np.array([[9.,3.,.002]]);source=e.contact.cache.layers['5/0']['geometry'][0]
   cfg=ViewSettings(reconstruction_ignored=(('5/0',0),))
   e.configure_reconstruction(True,cfg);f=e.frame(z)['reconstruction'];g=f['replacements']['5/0'][0]['geometry']
   self.assertLess(g.symmetric_difference(source).area,.001)
   self.assertFalse(f['pieces'][0]['connection_failed'])
   e.configure_reconstruction(True,replace(cfg,reconstruction_following=(('5/0',0),)))
   f=e.frame(z)['reconstruction'];g=f['replacements']['5/0'][0]['geometry']
   self.assertLess(g.symmetric_difference(transform(source,e.bound.centers[0],z[0])).area,.001)
  finally:e.close()
 def test_pose_validation_and_absolute_pivot(self):
  from die_shift_studio.measured_core import pose_parameters
  from die_shift_studio.pivots import rotation_centers
  case={'dies':[box(-10,-20,10,20)],'infos':[dict(cx=0,cy=0,width=20,height=40,angle=0)]}
  z,p=pose_parameters(case,Parameters(),[dict(die=1,dx_um=2,dy_um=3,angle_deg=90,rotation_center_um=[5,7])])
  np.testing.assert_allclose(rotation_centers(case,p),[[5,7]])
  self.assertAlmostEqual(z[0,2],np.pi/2)
  with self.assertRaises(ValueError):pose_parameters(case,p,[dict(die=1,dx_um=0,dy_um=0,angle_deg=float('nan'))])
 def test_ipc_json_request_response_and_shutdown(self):
  from die_shift_studio.local_ipc import Server,call
  with tempfile.TemporaryDirectory() as tmp:
   endpoint=Path(tmp)/'endpoint.json'
   def dispatch(request,event,reply):reply.update(status='completed',result={'command':request['command']});event.set()
   server=Server(endpoint,dispatch)
   try:self.assertEqual(call(endpoint,'info')['result']['command'],'info')
   finally:server.close();server.thread.join(3)
   self.assertFalse(endpoint.exists());self.assertFalse(server.thread.is_alive())
   endpoint.write_bytes(b'existing drawing or other user file')
   with self.assertRaises(ValueError):Server(endpoint,dispatch)
   self.assertEqual(endpoint.read_bytes(),b'existing drawing or other user file')
 def test_measured_snapshot_includes_moved_die_border_and_real_copper(self):
  from die_shift_studio.measured_core import Session,write_gds
  e,d,c=fixture()
  try:
   s=Session(d,c,e.roles,{},e.p,replace(e.reconstructor.settings,acceleration='cpu'),e.contact.cache)
   s.prepare(lambda *_:None)
   self.assertEqual(s.model(s.p)[0].bound.acceleration,'cpu')
   from unittest.mock import patch
   from die_shift_studio.reconstruction_route import RoutePlan
   with patch.object(RoutePlan,'choose',side_effect=AssertionError('Prepared equivalent pivot rebuilt routes')):
    f,out,details=s.run([dict(die=1,dx_um=5,dy_um=2,angle_deg=0,rotation_center_um=list(c['dies'][0].centroid.coords[0]))],preview=False)
   before=shapely.Polygon(d.layers['128/0'][0]);after=shapely.from_wkb(out.layers['128/0'][0])
   self.assertAlmostEqual(after.centroid.x-before.centroid.x,5)
   self.assertTrue(details['fixed_rdl_drc_checked'])
   with tempfile.TemporaryDirectory() as tmp:
    result=write_gds(out,Path(tmp)/'m.gds',d.dbu_um);self.assertTrue(Path(result['path']).is_file())
   s.close()
  finally:e.close()
 def test_whole_follow_mixed_die_dependencies_are_not_lost(self):
  from test_v40 import make_engine
  from die_shift_studio.statistics import ExtremeCatalog
  from die_shift_studio.reconstruction_events import scan,current_failures
  from die_shift_studio.extreme_filter import ProblemIndex
  shapes={'127/0':[box(-300,-200,500,200)],'128/0':[box(-150,-70,-50,70),box(50,-70,150,70)],
          '3/0':[Point(x,0).buffer(15) for x in (-100,100)],'4/0':[Point(x,0).buffer(8) for x in (-100,100)],
          '5/0':[shapely.LineString([(-100,0),(300,0)]).buffer(30)],'6/0':[Point(300,0).buffer(8)]}
  p=Parameters(multi_bound_x_um=5,multi_bound_y_um=0,bound_angle_deg=0,annulus_um=5)
  cfg=ViewSettings(reconstruction_ignored=(('5/0',0),),reconstruction_following=(('5/0',0),))
  e,_,_=make_engine(shapes,p,cfg)
  try:
   r=e.reconstructor;grid=ExtremeCatalog(e.contact.case,p);bank=scan(r,grid);index=ProblemIndex(grid,bank['red_factors'])
   for i in range(grid.total):
    f=r.frame(grid[i]['states']);self.assertEqual(index.matches(grid.choices(i)),bool(current_failures(f)),i)
   f=r.frame(np.array([[5.,0,0],[0.,0,0]]));self.assertTrue(f['pieces'][0]['connection_failed'])
   source=e.contact.cache.layers['5/0']['geometry'][0];self.assertTrue(f['replacements']['5/0'][0]['geometry'].equals(source))
  finally:e.close()
 def test_new_client_limits_and_shared_filter_metadata_roundtrip(self):
  from die_shift_studio.project import normalize_project
  p=normalize_project({'schema_version':5,'parameters':{},'settings':{}})
  self.assertEqual(p['parameters']['minimum_area_um2'],.5)
  self.assertEqual(p['settings']['reconstruction_following'],())
  self.assertEqual(p['measured_output']['max_threads'],0)
  self.assertEqual(normalize_project({'schema_version':5,'measured_output':{'max_memory_mb':0}})['measured_output']['max_memory_mb'],0)
 def test_gds_overflow_rejected_before_replacing_old_file(self):
  from die_shift_studio.measured_core import write_gds
  from die_shift_studio.dxf_export import Snapshot
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'old.gds';path.write_bytes(b'keep-old')
   snapshot=Snapshot({'1/0':(box(3e6,0,3e6+10,10).wkb,)},'TOP',None,0)
   with self.assertRaisesRegex(ValueError,'32'):write_gds(snapshot,path,.001)
   self.assertEqual(path.read_bytes(),b'keep-old')
   snapshot=Snapshot({'65536/0':(box(0,0,10,10).wkb,)},'TOP',None,0)
   with self.assertRaisesRegex(ValueError,'16'):write_gds(snapshot,path,.001)
   self.assertEqual(path.read_bytes(),b'keep-old')

if __name__=='__main__':unittest.main()
