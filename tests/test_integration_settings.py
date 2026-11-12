"""Branch integration keeps existing settings and validates manual geometry."""
import json
import unittest
from dataclasses import asdict, replace
from die_shift_studio.settings import ViewSettings


class IntegrationSettingsTests(unittest.TestCase):
    def test_legacy_defaults_retained(self):
        q=ViewSettings.from_dict({})
        self.assertEqual(q.width_yield_method,'grid')
        self.assertTrue(q.width_show_probability)
        self.assertTrue(q.show_annulus_range and q.show_spacing_range)
        self.assertIn('禁布区',q.width_display)
        self.assertEqual(q.reconstruction_regions,())
        self.assertIn('nominal-physical-throat-v36',q.width_key())

    def test_regions_and_all_cut_kinds_round_trip(self):
        plane=('3/0',2,(('1/0',7),('2/0',7)),1.,0.,12.,14.)
        q=replace(ViewSettings(),reconstruction_regions=(('3/0',(1.,2.,4.,5.),'move'),),
                  reconstruction_cuts=(plane,),reconstruction_extra_cuts=(plane,),
                  reconstruction_second_cuts=(plane,),reconstruction_dropped_cuts=(plane[:3],))
        restored=ViewSettings.from_dict(json.loads(json.dumps(asdict(q))))
        self.assertEqual(restored,q)
        self.assertEqual(restored.reconstruction_key(),q.reconstruction_key())

    def test_regions_reject_invalid_and_order_bounds(self):
        rows=[('3/0',(4,5,1,2),'stay'),('3/0',(0,0,float('nan'),2),'move'),
              ('3/0',(0,0,float('inf'),2),'move'),('3/0',(0,0,0,2),'move'),
              ('3/0',(0,0,1,2),'invalid'),None,('3/0',None,'move')]
        self.assertEqual(ViewSettings.from_dict({'reconstruction_regions':rows}).reconstruction_regions,
                         (('3/0',(1.,2.,4.,5.),'stay'),))
        for malformed in (None,42,'bad',{}):
            self.assertEqual(ViewSettings.from_dict({'reconstruction_regions':malformed}).reconstruction_regions,())

    def test_numeric_controls_validate_and_invalidate_cache(self):
        base=ViewSettings()
        for key in ('reconstruction_corner_guard_um',):
            with self.subTest(key=key):
                changed=ViewSettings.from_dict({key:getattr(base,key)+1})
                self.assertNotEqual(changed.reconstruction_key(),base.reconstruction_key())
                for bad in (-1,float('nan'),float('inf')):
                    with self.assertRaises(ValueError):ViewSettings.from_dict({key:bad})
        changed=replace(base,reconstruction_regions=(('3/0',(0.,0.,1.,1.),'move'),))
        self.assertNotEqual(changed.reconstruction_key(),base.reconstruction_key())

    def test_legacy_dropped_identity_migrates(self):
        identity=('3/0',2,(7,8))
        q=ViewSettings.from_dict({'reconstruction_dropped_cuts':[identity,(*identity,1,0,0,0)]})
        self.assertEqual(q.reconstruction_dropped_cuts,(identity,))

if __name__=='__main__':unittest.main()
