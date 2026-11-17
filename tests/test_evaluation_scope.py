import unittest,math
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import patch
import numpy as np
from scipy.special import ndtr
from shapely.geometry import box
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings
from die_shift_studio.evaluation_scope import focused_catalog,conditional_weights,conditional_event
from die_shift_studio.extreme_filter import PoseClause

class ScopeTests(unittest.TestCase):
 def model(self):
  return SimpleNamespace(case={'dies':[box(0,0,10,10),box(1000,0,1010,10)],'infos':[{'cx':5.,'cy':5.},{'cx':1005.,'cy':5.}]},interdie_networks=[])
 def parameters(self):
  return Parameters(multi_bound_x_um=1,multi_bound_y_um=0,bound_angle_deg=0,target_nsigma=1)
 def test_focus_weights_condition_on_only_selected_joint_cells(self):
  p=self.parameters();grid,indices=focused_catalog(self.model(),p)
  self.assertEqual(len(indices),7);self.assertEqual(grid.total+1,9);self.assertIn(-1,indices)
  weights,_,_=conditional_weights(grid,p,indices);self.assertAlmostEqual(weights.sum(),1.)
  tail=(ndtr(1)-ndtr(.5))/(ndtr(1)-ndtr(-1));mass=1-2*tail**2
  positive=int(np.flatnonzero(grid.locals[0][:,0]>0)[0]);clause=PoseClause(((0,(positive,)),))
  passed,failed=conditional_event(grid,indices,weights,[clause])
  self.assertAlmostEqual(failed,tail*(1-tail)/mass);self.assertAlmostEqual(passed+failed,1.)
 def test_settings_scope_is_saved_and_separates_caches(self):
  a=ViewSettings();b=ViewSettings.from_dict({'evaluation_scope':'focus'})
  self.assertEqual(a.evaluation_scope,'all');self.assertNotEqual(a.reconstruction_key(),b.reconstruction_key());self.assertNotEqual(a.width_key(),b.width_key())
  with self.assertRaises(ValueError):ViewSettings.from_dict({'evaluation_scope':'wrong'})
 def test_focused_drc_never_visits_unselected_poses(self):
  from die_shift_studio.extreme_validation import evaluate_states,status_for
  from die_shift_studio.representatives import RepresentativeExtremes
  p=self.parameters();model=self.model();grid,indices=focused_catalog(model,p);catalog=RepresentativeExtremes(model.case,p,grid)
  seen=[]
  def frame(z):
   seen.append(z.copy());return {'checks':{i:{'failures':[]} for i in range(3)},'contact':{'issues':[]},'drc':[]}
  engine=SimpleNamespace(roles={},configure_deformation=lambda *_:None,configure_drc=lambda *_:None,frame=frame)
  facts=evaluate_states(engine,catalog,ViewSettings())
  self.assertEqual(len(seen),len(indices));self.assertTrue(status_for(facts,[0,0])['safe'])
  self.assertFalse(status_for(facts,[1,2])['checked'])
  self.assertEqual(set(facts['pose_status']),set(map(str,indices)))
 def test_focused_reconstruction_does_not_prepare_full_joint_grid(self):
  from test_v38 import fixture
  from die_shift_studio.evaluation_scope import focused_reconstruction
  from die_shift_studio.statistics import ExtremeCatalog
  e,_,_=fixture()
  try:
   grid=ExtremeCatalog(e.contact.case,e.p)
   with patch('die_shift_studio.reconstruction_joint.prepare',side_effect=AssertionError('full grid built')):
    bank,value=focused_reconstruction(e.reconstructor,grid,(-1,),lambda *_:None)
   self.assertEqual(bank['package_states'],1);self.assertEqual(value['total'],1)
   self.assertEqual(value['method'],'focus');self.assertAlmostEqual(value['value']+value['failure'],1.)
  finally:e.close()
if __name__=='__main__':unittest.main()
