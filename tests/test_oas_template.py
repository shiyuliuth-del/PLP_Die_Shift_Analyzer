"""Static template import, exact placement, layer names, and input identity."""
from pathlib import Path
import tempfile
import unittest

import gdstk
import klayout.db as k
from die_shift_studio.oas_errors import choose_error_layer
from die_shift_studio.oas_stream import Store, verify_crc
from die_shift_studio.oas_template import digest, read_template


class TemplateOutput(unittest.TestCase):
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
