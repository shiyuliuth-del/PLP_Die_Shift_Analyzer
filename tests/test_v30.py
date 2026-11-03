"""Runtime location, single entry, icon, independent range visibility and cache bounds."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
from matplotlib.collections import LineCollection
from PIL import Image
from shapely.geometry import box,Polygon,MultiPolygon

from die_shift_studio.app_icon import write_ico
from die_shift_studio.contact_ranges import ContactRanges
from die_shift_studio.frozen_support import prepare_runtime
from die_shift_studio.settings import ViewSettings

class V30Tests(unittest.TestCase):
    def test_directory_initialization_and_user_data_survive_relaunch(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'带 空格';root.mkdir();exe=root/'Die_Shift_Analyze.exe';exe.touch()
            with patch('sys.frozen',True,create=True),patch('sys.executable',str(exe)),patch('sys._MEIPASS',d,create=True):
                prepare_runtime();marker=root/'Jobs'/'old.json';marker.write_text('keep')
                (root/'logs').rmdir();prepare_runtime();self.assertEqual(marker.read_text(),'keep')
                self.assertEqual({p.name for p in root.iterdir()},{exe.name,'Jobs','logs'})

    def test_one_launcher_unpinned_requirements_and_icon_sizes(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(sorted(p.name for p in root.glob('*.bat')),['RUN.bat'])
        self.assertFalse((root/'RUN.vbs').exists());self.assertFalse((root/'main.pyw').exists())
        for requirement in (root/'requirements.txt').read_text().splitlines():self.assertRegex(requirement,r'^[a-zA-Z0-9_-]+$')
        with TemporaryDirectory() as d:
            ico=Path(d)/'icon.ico';write_ico(ico)
            with Image.open(ico) as im:self.assertEqual(im.ico.sizes(),{(s,s) for s in (16,24,32,48,64,128,256)})

    def test_independent_overlays_keep_holes_and_do_not_recompute_unchanged_paths(self):
        hole=Polygon(box(0,0,10,10).exterior.coords,[box(2,2,8,8).exterior.coords])
        multi=MultiPolygon([hole,box(20,20,30,30)]);other=box(41,41,45,46)
        regions=[dict(key=k,index=i,bounds=shape.bounds,annulus=shape,spacing=shape) for i,(k,shape) in enumerate([('4/0',multi),('8/0',other)])]
        settings=ViewSettings.from_dict({});artists=[LineCollection([]),LineCollection([])];model=ContactRanges(regions)
        view=(-5,-5,32,32);source=model.regions
        model.draw(artists,settings,{'4/0','8/0'},view)
        self.assertEqual([len(a.get_segments()) for a in artists],[3,3])
        with patch('die_shift_studio.contact_ranges.outlines',side_effect=AssertionError('coordinates recalculated')):
            for flags in [(False,True),(False,False),(True,False),(True,True)]:
                cfg=replace(settings,show_annulus_range=flags[0],show_spacing_range=flags[1])
                model.draw(artists,cfg,{'4/0','8/0'},(-4,-4,33,33))
                self.assertEqual(tuple(a.get_visible() for a in artists),flags)
                for key in ('numeric_key','deformation_key','width_key','reconstruction_key'):
                    self.assertEqual(getattr(cfg,key)(),getattr(settings,key)())
        model.draw(artists,settings,{'8/0'},(35,35,50,50));self.assertEqual([len(a.get_segments()) for a in artists],[1,1])
        np.testing.assert_allclose(artists[1].get_segments()[0],other.exterior.coords)
        self.assertIs(source,regions)
        with self.assertRaises(ValueError):ViewSettings.from_dict({'show_spacing_range':'False'})
