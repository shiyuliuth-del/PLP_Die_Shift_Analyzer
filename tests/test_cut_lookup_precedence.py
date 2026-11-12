"""Exact manual cuts must beat older merged-piece pins after integration."""
import unittest

from die_shift_studio.reconstruction import Reconstructor


class CutLookupPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.r=Reconstructor.__new__(Reconstructor)
        self.r.vias_by_target={('5/0',0):[
            dict(key='4/0',index=0),dict(key='4/0',index=1)]}
        self.one=(('4/0',0),)
        self.both=(('4/0',0),('4/0',1))

    def lookup(self, entries):
        rows={('5/0',0,refs):value for refs,value in entries}
        return self.r._cover_lookup(rows,None,'5/0',0,self.one)

    def second(self, entries):
        rows=[('5/0',0,refs,1.,0.,x,0.) for refs,x in entries]
        return self.r._pinned_second(rows,'5/0',0,self.one)

    def test_new_exact_edit_beats_older_merged_piece_for_both_cuts(self):
        entries=[(self.both,10.),(self.one,25.)]
        self.assertEqual(self.lookup(entries),25.)
        self.assertEqual(self.second(entries),(25.,0.,1.,0.))
        self.assertEqual(self.lookup(list(reversed(entries))),25.)
        self.assertEqual(self.second(list(reversed(entries))),(25.,0.,1.,0.))

    def test_second_cut_exact_edit_beats_cover_independently(self):
        self.assertEqual(self.second([(self.both,10.),(self.one,25.)]),
                         (25.,0.,1.,0.))

    def test_qualified_cover_precedes_legacy_exact_but_exact_wins_over_both(self):
        entries=[((0,),5.),(self.both,10.)]
        self.assertEqual(self.lookup(entries),10.)
        self.assertEqual(self.second(entries),(10.,0.,1.,0.))
        entries.append((self.one,25.))
        self.assertEqual(self.lookup(entries),25.)
        self.assertEqual(self.second(entries),(25.,0.,1.,0.))

    def test_unambiguous_legacy_exact_precedes_legacy_cover(self):
        entries=[((0,1),10.),((0,),25.)]
        self.assertEqual(self.lookup(entries),25.)
        self.assertEqual(self.second(entries),(25.,0.,1.,0.))
        self.assertEqual(self.lookup(entries[:1]),10.)

    def test_ambiguous_legacy_and_other_layer_refs_cannot_override(self):
        self.r.vias_by_target[('5/0',0)].append(dict(key='6/0',index=0))
        entries=[((0,),5.),((('6/0',0),),15.)]
        self.assertIsNone(self.lookup(entries))
        self.assertIsNone(self.second(entries))
        entries.append((self.one,25.))
        self.assertEqual(self.lookup(entries),25.)
        self.assertEqual(self.second(entries),(25.,0.,1.,0.))

    def test_dropped_cut_covers_and_unrelated_features_remain_isolated(self):
        dropped={('5/0',0,self.both)}
        self.assertTrue(self.r._cover_lookup(dropped,None,'5/0',0,self.one))
        self.assertIsNone(self.r._cover_lookup(dropped,None,'5/0',1,self.one))
        self.assertIsNone(self.r._cover_lookup(dropped,None,'7/0',0,self.one))


if __name__=='__main__':unittest.main()
