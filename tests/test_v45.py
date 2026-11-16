import unittest
from dataclasses import replace,asdict
import numpy as np
import shapely
from shapely.geometry import Point,box
from die_shift_studio.core import Parameters
from die_shift_studio.settings import ViewSettings
from die_shift_studio.drc import GlobalDRC
from test_v40 import make_engine


class CurrentRulesTests(unittest.TestCase):
    def test_defaults_and_explicit_old_values(self):
        self.assertEqual(Parameters().minimum_area_um2,.5)
        self.assertEqual(ViewSettings().reconstruction_style,'linear')
        self.assertEqual(ViewSettings().drc_tolerance_um,.1)
        old=ViewSettings.from_dict({'reconstruction_style':'c2','drc_tolerance_um':0})
        self.assertEqual(old.reconstruction_style,'c2');self.assertEqual(old.drc_tolerance_um,0)
        self.assertEqual(Parameters.from_dict({'minimum_area_um2':0}).minimum_area_um2,0)
        from die_shift_studio.measured_core import Options
        cfg=Options.from_dict({});self.assertEqual((cfg.max_memory_mb,cfg.max_threads),(0,0))
        self.assertEqual(cfg.memory_budget_mb({'available_memory_mb':10000}),3500)
        self.assertEqual(Options(max_memory_mb=512).memory_budget_mb({'available_memory_mb':10000}),512)

    def fixture(self):
        via=Point(0,0).buffer(10)
        landing=via.buffer(15/np.cos(np.pi/128),quad_segs=32).buffer(.005)
        shapes={'127/0':[box(-500,-500,500,500)],'128/0':[box(-50,-50,50,50)],
                '3/0':[landing],'4/0':[via],'5/0':[box(-120,-120,120,120)],'6/0':[Point(80,0).buffer(10)]}
        return make_engine(shapes,Parameters(annulus_um=15,moving_annulus_um=15,package_clearance_um=10),ViewSettings())

    def test_common_rigid_motion_does_not_create_enclosure_faults(self):
        engine,_,_=self.fixture()
        try:
            check=GlobalDRC(engine.contact,engine.p);cfg=ViewSettings(drc_tolerance_um=0)
            zero=check.frame(np.zeros((1,3)),settings=cfg)
            self.assertFalse(zero,[(r['kind'],r['geometry'].area) for r in zero])
            for angle in (.11,.39,.71,1.2):
                rows=check.frame(np.array([[12.,-7.,angle]]),settings=cfg)
                self.assertFalse(rows,[(r['kind'],r['geometry'].area) for r in rows])
            self.assertGreater(check.hits,0)
        finally:engine.close()

    def test_current_fixed_copper_and_moving_linewidth_are_both_checked(self):
        engine,_,_=self.fixture()
        try:
            cfg=ViewSettings();check=GlobalDRC(engine.contact,replace(engine.p,wafer_line_width_um=70))
            rows=check.frame(np.zeros((1,3)),settings=cfg)
            self.assertTrue(any(r['layer']=='3/0' and r['kind']=='线宽不足' for r in rows))
            changed={'5/0':[dict(indices=(0,),geometry=box(-120,-4,120,4),net=engine.contact.nodes[engine.contact.ids['5/0',0]]['net'])]}
            rows=check.frame(np.zeros((1,3)),changed,cfg)
            self.assertTrue(any(r['layer']=='5/0' and r['kind']=='线宽不足' for r in rows))
            self.assertTrue(any(r['kind']=='孔盘包围不足' for r in rows))
        finally:engine.close()

    def test_proven_rigid_override_reuses_nominal_rules(self):
        engine,_,_=self.fixture()
        try:
            cfg=ViewSettings(drc_tolerance_um=0);check=GlobalDRC(engine.contact,engine.p)
            z=np.array([[12.,-7.,.39]])
            baseline=check.frame(z,settings=cfg);misses=check.misses
            node=engine.contact.nodes[engine.contact.ids['3/0',0]]
            replacements={'3/0':[dict(indices=(0,),geometry=engine.contact.moved(node['poly'],0,z))]}
            actual=check.frame(z,replacements,cfg)
            self.assertEqual(check.misses,misses)
            self.assertEqual([(q['kind'],q['geometry'].wkb) for q in actual],[(q['kind'],q['geometry'].wkb) for q in baseline])
            replacements['3/0'][0]['geometry']=box(-100,-1,100,1)
            self.assertTrue(any(q['kind']=='线宽不足' for q in check.frame(z,replacements,cfg)))
        finally:engine.close()

    def test_location_rows_are_connected_and_deduplicated(self):
        from die_shift_studio.current_drc import locations
        a,b=box(0,0,1,1),box(4,0,5,1);g=a.union(b)
        rows=locations([dict(layer='5/0',kind='线宽不足',geometry=g),dict(layer='5/0',kind='线宽不足',geometry=g)])
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['geometry'].covers(Point(r['point'])) for r in rows))

if __name__=='__main__':unittest.main()
