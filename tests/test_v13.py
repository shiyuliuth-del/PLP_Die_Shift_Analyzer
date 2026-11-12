"""V13: failed initialization, session caching, final-layout DRC and rollback."""
import importlib
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import box
from test_v12 import fixture
from die_shift_studio.fast_preview import PreviewEngine
from die_shift_studio.drc import GlobalDRC
from die_shift_studio.session_cache import SessionCache
from die_shift_studio.settings import ViewSettings


class V13Tests(unittest.TestCase):
    def test_windows_missing_klayout_actionable_before_worker(self):
        from die_shift_studio.dependencies import verify
        original=importlib.import_module
        def missing(name):
            if name=='klayout.db':raise ModuleNotFoundError("No module named 'klayout'")
            return original(name)
        from die_shift_studio import native_backend
        native_backend.close()
        with patch('importlib.import_module',side_effect=missing),patch.object(native_backend,'executable_candidates',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'DIE_SHIFT_KLAYOUT') as error:verify()
        self.assertNotIn('pip install',str(error.exception))
        verify()



    def test_disk_spill_keeps_every_entry_and_clear_removes_everything(self):
        cache=SessionCache(budget_bytes=1);path=cache.root
        for i in range(10):cache[i]={'geometry':box(i,0,i+1,2),'array':np.arange(100)}
        self.assertFalse(cache.memory)
        for i in range(10):self.assertTrue(cache[i]['geometry'].equals(box(i,0,i+1,2)))
        cache.clear();self.assertEqual(len(list(path.iterdir())),0);self.assertEqual(len(cache),0)
        cache['x']=1;cache.close();self.assertFalse(path.exists())

    def test_drc_checks_all_fixed_net_pairs_not_only_cross_owner(self):
        shapes=[box(0,0,100,20),box(0,25,100,45),box(0,50,100,70),box(0,75,100,95)]
        nodes=[dict(net=i,owner=-1) for i in range(5)]
        m=SimpleNamespace(data=SimpleNamespace(dbu_um=.001),roles={'5/0':'固定RDL','3/0':'运动RDL'},
            cache=SimpleNamespace(layers={'5/0':dict(geometry=shapes),'3/0':dict(geometry=[box(0,0,100,1)])}),
            case={'board':box(-100,-100,250,250)},nodes=nodes,ids={**{('5/0',i):i for i in range(4)},('3/0',0):4},records=[])
        *_,p,_=fixture();p=replace(p,line_width_um=10,line_space_um=10)
        drc=GlobalDRC(m,p);out=drc.frame(np.zeros((2,3)))
        self.assertEqual(sum(q['kind']=='异网短接 / 线距不足' for q in out),3)
        self.assertFalse(any(q['layer']=='3/0' for q in out))
        # Move the second conductor over its neighbour; check replacement, not source.
        replacements={'5/0':[dict(indices=(1,),net=1,geometry=box(20,10,120,30))]}
        new=drc.frame(np.zeros((2,3)),replacements)
        bad=shapely.union_all([q['geometry'] for q in new if q['kind']=='异网短接 / 线距不足'])
        self.assertTrue(bad.covers(box(21,11,99,19)))



if __name__=='__main__':unittest.main()
