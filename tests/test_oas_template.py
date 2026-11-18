"""Static template import, exact placement, layer names, and input identity."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

import gdstk
import klayout.db as k
from die_shift_studio.oas_errors import choose_error_layer
from die_shift_studio.oas_stream import Store, verify_crc
from die_shift_studio.oas_template import digest, read_template


class TemplateOutput(unittest.TestCase):
    def test_installed_desktop_reads_template_without_python_klayout(self):
        from die_shift_studio.native_backend import executable_candidates
        if not any(Path(candidate).is_file() for candidate in executable_candidates()):
            self.skipTest('Installed desktop KLayout unavailable')
        root = Path(__file__).resolve().parents[1]
        script = """
import importlib.abc
from pathlib import Path
import sys
import gdstk
from die_shift_studio.native_backend import backend
from die_shift_studio.oas_stream import Store, verify_crc
from die_shift_studio.oas_template import digest, read_template

class NoKLayout(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name.split('.')[0] == 'klayout':
            raise ModuleNotFoundError("No module named 'klayout'", name=name)

sys.meta_path.insert(0, NoKLayout())
assert not any(name == 'klayout' or name.startswith('klayout.') for name in sys.modules)
source, target = map(Path, sys.argv[1:])
sha = digest(source)
template = read_template(source, sha, .001)
assert backend().info['backend'] == 'installed desktop'
assert set(template[1]) == {'60000/0', '60001/0'}
store = Store(target.parent, .001, template=template)
try:
    store.add('Package', {}, (0, 0), (0, 0))
    store.write(target)
finally:
    store.close()
assert verify_crc(target) and digest(source) == sha
top, = gdstk.read_oas(target).top_level()
assert len(top.get_polygons(layer=60000, datatype=0)) == 14
assert len(top.get_polygons(layer=60001, datatype=0)) == 8
assert [label.text for label in top.get_labels()] == ['MARK'] * 4
"""
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
            env.pop('DIE_SHIFT_NATIVE_BACKEND', None)
            output = subprocess.run(
                [sys.executable, '-c', script, str(root/'Jobs/GD55/GD55_Template.gds'),
                 str(Path(temp)/'result.oas')],
                cwd=root, env=env, capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(output.returncode, 0, output.stdout + output.stderr)

    def test_hierarchical_template_copies_layers_and_text_once_to_final_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            base=k.Layout()
            base.dbu=.001
            geom=base.layer(k.LayerInfo(65535,0,'Cut'))
            mark=base.layer(k.LayerInfo(12,2,'Marks'))
            child=base.create_cell('MARK')
            child.shapes(geom).insert(k.Box(-50,-500,50,500))
            child.shapes(geom).insert(k.Path([k.Point(1,0),k.Point(100,120)],20))
            child.shapes(mark).insert(k.Text('FIDUCIAL',k.Trans(10,20)))
            top=base.create_cell('TEMPLATE')
            top.insert(k.CellInstArray(child.cell_index(),k.Trans(1,False,1234,-1234)))
            source=root/'source.oas'
            base.write(str(source))
            sha=digest(source)
            template=read_template(source,sha,.001)
            error=choose_error_layer(template[1])
            self.assertEqual(error,(65534,0))
            stage=root/'stage.oas'
            final=root/'final.oas'
            store=Store(root,.001,checkpoint_destination=stage,error_layer=error,template=template)
            try:
                for i in range(2):
                    store.add(f'P{i}',{},(i*1000,2000),(10,20),errors=[],die_count=1)
                store.write(final)
            finally:
                store.close()
            self.assertEqual(digest(source),sha)
            for output in (stage,final):
                self.assertTrue(verify_crc(output))
                read=k.Layout()
                read.read(str(output))
                cut=read.layer(65535,0)
                self.assertEqual(read.get_info(cut).name,'Cut')
                assert (k.Region(top.begin_shapes_rec(geom))^k.Region(read.top_cell().begin_shapes_rec(cut))).is_empty()
                gd=gdstk.read_oas(output)
                refs=gd.top_level()[0].references
                self.assertEqual([ref.cell_name for ref in refs].count('G_TEMPLATE'),1)
                label=gd.top_level()[0].get_labels()[0]
                self.assertEqual((label.text,label.layer,label.texttype),('FIDUCIAL',12,2))
                self.assertAlmostEqual(label.origin[0],1.214)
                self.assertAlmostEqual(label.origin[1],-1.224)
            with self.assertRaisesRegex(ValueError,'已改变'):
                read_template(source,'incorrect',.001)
