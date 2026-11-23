"""Shared inputs, via menu targets and nominal DXF segmentation."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import tempfile

import ezdxf
import shapely
from shapely.geometry import box, LineString

from die_shift_studio.dxf_export import Snapshot
from die_shift_studio.project import normalize_project
from die_shift_studio.region_export import write_regions
from die_shift_studio.settings import ViewSettings
from die_shift_studio.shape_menu import show_shape_menu


class V62Tests(TestCase):
    def test_saved_contact_panel_and_explicit_free_boundaries_migrate(self):
        p = normalize_project(dict(schema_version=5, expanded_settings=['接触标记', 'DRC'],
                                   settings={'reconstruction_box_mode':'free','contact_marker_size':3.25}))
        self.assertEqual(p['expanded_settings'], ['DRC'])
        self.assertEqual(p['settings']['contact_marker_size'], 3.25)
        self.assertEqual(p['settings']['reconstruction_box_mode'], 'free')
        self.assertEqual(ViewSettings.from_dict().reconstruction_box_mode, 'aligned')

    def test_fixed_via_menu_hides_add_and_moving_via_targets_its_real_copper(self):
        roles={'1/0':'运动过孔','2/0':'固定RDL','3/0':'固定过孔'}
        contacts=Mock()
        contacts.fixed_interfaces.return_value=[('2/0',1)]
        frame=SimpleNamespace(model=SimpleNamespace(connections=contacts), vias_by_target={('2/0',1):[]})
        zone={'layer':'2/0','index':0,'route_plan':None}
        selected=SimpleNamespace(selected=[('3/0',0)],hits=Mock(return_value=[('2/0',0)]))
        window=SimpleNamespace(data=object(),cache_group=object(),selection=selected,canvas=Mock(),
                               reconstruction_toggle=Mock(),width_toggle=Mock(),cut_drag=Mock(),
                               reconstruction_overlay=Mock(),engine=SimpleNamespace(reconstructor=frame),
                               roles=lambda:roles,current_frame={},view_settings=ViewSettings())
        window.cut_drag._tolerance.return_value=1.
        window.canvas.devicePixelRatioF.return_value=1.
        window.canvas.height.return_value=80
        window.width_toggle.isChecked.return_value=False
        window.reconstruction_overlay.pick.return_value=(zone,None,'body')
        window.reconstruction_overlay.data.return_value={}
        with (patch('die_shift_studio.shape_menu.W.QMenu') as menu_cls,
              patch('die_shift_studio.shape_menu.shape_nodes',return_value=('polygon',[],None)),
              patch('die_shift_studio.reconstruction_edits.change_zone') as edit):
            menu=menu_cls.return_value
            show_shape_menu(window,SimpleNamespace(x=10,y=10),(2.,4.))
            self.assertNotIn('增加动区',[call.args[0] for call in menu.addAction.call_args_list])
            menu.reset_mock()
            selected.selected=[('1/0',0)]
            selected.hits.return_value=[('2/0',0),('1/0',0)]
            show_shape_menu(window,SimpleNamespace(x=10,y=10),(2.,4.))
            add,= (call.args[1] for call in menu.addAction.call_args_list if call.args[0]=='增加动区')
            add()
            contacts.fixed_interfaces.assert_called_once_with('1/0',0)
            edit.assert_called_once_with(window,'add',ref=('2/0',1),point=(2.,4.))

    def test_abc_block_preserves_open_v61_segments_and_reference_rings(self):
        copper=box(0,0,100,60).difference(box(25,20,75,40))
        board=box(-20,-20,130,90)
        die=box(5,70,15,85)
        snapshot=Snapshot({'2/0':(copper.wkb,),'127/0':(board.wkb,),'128/0':(die.wkb,)},'TOP',None,0)
        roles={'2/0':'固定RDL','127/0':'封装边框','128/0':'芯片边框'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'out.dxf'
            from die_shift_studio.dxf_settings import DxfSettings
            write_regions(snapshot,{},roles,path,settings=DxfSettings(region_auto_join=False))
            drawing=ezdxf.readfile(path);model=drawing.modelspace()
            self.assertFalse(drawing.audit().errors)
            outlines=list(model.query('LWPOLYLINE'))
            self.assertEqual({q.dxf.layer for q in outlines},{'PKG_REF','DIE_REF'})
            self.assertTrue(all(q.closed for q in outlines))
            block,=model.query('INSERT')
            self.assertEqual(block.dxf.layer,'L2_D0_B')
            paths=list(block.virtual_entities())
            self.assertEqual(len(paths),4)
            self.assertTrue(all(not q.closed for q in paths))
            actual=shapely.union_all([LineString(q.get_points('xy')) for q in paths])
            self.assertTrue(actual.equals(copper.boundary))
