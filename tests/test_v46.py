import unittest,tempfile,json
from pathlib import Path
import shapely,gdstk
from die_shift_studio.jobs import job_path,list_jobs,local_source,atomic_save
from die_shift_studio.oas_stream import Store,verify_crc
class V46Tests(unittest.TestCase):
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
