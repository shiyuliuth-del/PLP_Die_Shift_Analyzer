"""Focused acceptance for layout-stationary copper between moving zones."""
from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import shapely

from die_shift_studio.between_motion import STATIONARY, graph_poses, solve_serial_weights
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_import import read
from die_shift_studio.settings import ViewSettings
from test_v59 import two_ended
from test_v72 import vertices


class StationaryBetweenTests(unittest.TestCase):
    def test_stationary_anchor_in_serial_and_branched_motion(self):
        self.assertEqual(solve_serial_weights(['average',STATIONARY,'max']),[1.,.5,0.,0.,0.])
        edges={(0,1):1.,(1,2):1.,(2,3):1.,(1,4):1.}
        first=np.array([12.,5.,.012]);last=np.array([-3.,-8.,-.018])
        poses=graph_poses(5,{0:first,3:last,4:last},edges,{1:'average',2:STATIONARY},np.zeros(2),(0.,0.))
        np.testing.assert_array_equal(poses[2],np.zeros(3))
        np.testing.assert_allclose(poses[1],(first+last)/3)

    def test_stationary_copper_with_two_moving_dies_and_dxf(self):
        engine,defaults,_=two_ended()
        try:
            model=engine.reconstructor.model
            cfg=replace(defaults,reconstruction_between_motion=STATIONARY,
                        reconstruction_angle_boxes=tuple(('2/0',vertices(x,-40,x+18,40)) for x in (90,160,280,360)),
                        reconstruction_box_order=('aligned',)*4,reconstruction_ignored=(('2/0',0),))
            solver=Reconstructor(model,engine.p,cfg)
            try:
                pose=np.array([[9.,13.,.013],[-5.,-12.,-.012]])
                nominal=solver.frame(np.zeros_like(pose),diagnostics=False,_raw=True)
                shifted=solver.frame(pose,diagnostics=False)
                self.assertFalse(shifted['hard_failures'])
                self.assertEqual(len(shifted['between_blocks']),3)
                copper=shifted['replacements']['2/0'][0]['geometry'].buffer(model.data.dbu_um)
                for block in shifted['between_blocks']:
                    self.assertEqual(block['mode'],STATIONARY)
                    self.assertFalse(block['moves'])
                    self.assertTrue(block['geometry'].equals_exact(block['nominal'],0))
                    self.assertTrue(copper.covers(block['nominal']))
            finally:
                solver.close()

            # Pin a single internal interval while the shared core still
            # follows a rotating DIE. Inverse reference motion must cancel.
            target=min(nominal['between_blocks'],key=lambda b:b['nominal'].centroid.x)
            override=replace(cfg,reconstruction_between_motion='max',
                             reconstruction_between_overrides=((*target['id'],STATIONARY),))
            restored=ViewSettings.from_dict(json.loads(json.dumps(asdict(override))))
            self.assertEqual(restored.reconstruction_between_overrides,override.reconstruction_between_overrides)
            self.assertEqual(ViewSettings.from_dict().reconstruction_between_motion,'max')
            solver=Reconstructor(model,engine.p,restored)
            try:
                shifted=solver.frame(pose,diagnostics=False)
                self.assertFalse(shifted['hard_failures'])
                fixed,=[b for b in shifted['between_blocks'] if b['mode']==STATIONARY]
                core=max(shifted['between_blocks'],key=lambda b:b['nominal'].area)
                self.assertGreater(core['nominal'].centroid.distance(core['geometry'].centroid),10.)
                self.assertTrue(fixed['geometry'].equals_exact(fixed['nominal'],0))
                self.assertTrue(shifted['replacements']['2/0'][0]['geometry'].buffer(model.data.dbu_um).covers(fixed['nominal']))
                nominal=solver.frame(np.zeros_like(pose),diagnostics=False,_raw=True)
                self.assertTrue(any(b['moves'] for b in nominal['between_blocks']))
                with TemporaryDirectory() as tmp:
                    path=Path(tmp)/'regions.dxf'
                    write_regions(snapshot(model.data,model.cache),nominal,model.roles,path)
                    outlines=read(path)['2/0']
                    x=fixed['nominal'].centroid.x
                    for y in (fixed['nominal'].bounds[1],fixed['nominal'].bounds[3]):
                        probe=shapely.Point(x,y)
                        self.assertLess(min(g.distance(probe) for g in outlines['B']),1e-7)
                        self.assertGreater(min(g.distance(probe) for g in outlines['A']),1e-3)
            finally:
                solver.close()
        finally:
            engine.close()


if __name__=='__main__':
    unittest.main()
