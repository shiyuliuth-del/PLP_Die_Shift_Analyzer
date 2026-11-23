from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import gdstk
import numpy as np
import shapely
from shapely.geometry import Point, LineString, box

from die_shift_studio.core import Parameters, read_gds
from die_shift_studio.evaluation_scope import focused_catalog, conditional_weights
from die_shift_studio.manual_regions import usable_partition
from die_shift_studio.project import normalize_project
from die_shift_studio.settings import ViewSettings
from die_shift_studio.short_edges import contract, has_short
from test_v40 import make_engine


class V52Tests(unittest.TestCase):
    def test_risk_scope_keeps_closing_pose_and_conditions_weights(self):
        dies=[box(-10,-20,10,20),box(90,-20,110,20),box(190,-20,210,20)]
        case={'dies':dies,'infos':[dict(cx=i*100,cy=0) for i in range(3)]}
        via=Point(0,0).buffer(1)
        obstacle=box(14,-30,16,30)
        record=dict(owner=0, poly=via, center=(0,0), target_layers=('5/0',), foreign=[0])
        model=SimpleNamespace(case=case,bound=SimpleNamespace(centers=np.array([[0,0],[100,0],[200,0]],float)),
                              records=[record],fixed_trees={'5':([0],shapely.STRtree([obstacle]))},
                              node_geometry=np.array([obstacle],object),interdie_networks=[])
        p=Parameters(multi_bound_x_um=12,multi_bound_y_um=10,bound_angle_deg=1)
        grid,indices=focused_catalog(model,p,scope='risk')
        _,focus=focused_catalog(model,p)
        self.assertLess(len(indices),len(focus))
        rows=[grid[i]['states'] for i in indices if i>=0]
        self.assertTrue(any(z[0,0]>0 and not z[1:].any() for z in rows))
        self.assertTrue(any(z[0,0]>0>z[1,0] for z in rows))
        self.assertTrue(any(z[0,0]<0<z[1,0] for z in rows))
        weights,_,_=conditional_weights(grid,p,indices)
        self.assertAlmostEqual(weights.sum(),1.)

    def test_short_edges_apply_to_import_holes_and_quantized_output(self):
        coords=[(0,0),(4,0),(4,.004),(4,4),(0,4)]
        g=shapely.Polygon(coords,holes=[[(1,1),(1.002,1),(2,1),(2,2),(1,2)]])
        clean=contract(g,.01)
        self.assertEqual(len(clean.interiors),1);self.assertFalse(has_short(clean,.01))
        self.assertTrue(clean.is_valid);self.assertAlmostEqual(clean.area,g.area,delta=.01)
        from die_shift_studio.gds_merge import restore_project_geometry
        from die_shift_studio.fast_preview import GeometryCache
        from die_shift_studio.dxf_export import snapshot
        from die_shift_studio.layout_export import write
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.gds'
            lib=gdstk.Library(unit=1e-6,precision=1e-9)
            lib.new_cell('TOP').add(gdstk.Polygon(coords,layer=5))
            lib.write_gds(source)
            data,_=restore_project_geometry(read_gds(source),{'parameters':{'minimum_area_um2':0}})
            self.assertFalse(has_short(shapely.Polygon(data.layers['5/0'][0]),.01))
            self.assertEqual(data.minimum_edge_um,.01)
            frozen=snapshot(data,GeometryCache(data))
            for ext in ('gds','oas'):
                output=Path(folder)/f'preview.{ext}'
                write(frozen,output,.001)
                loaded=read_gds(output)
                self.assertEqual(len(loaded.layers['5/0']),1)
                self.assertFalse(has_short(shapely.Polygon(loaded.layers['5/0'][0]),.01))

    def test_filtered_node_edit_can_save_reopen_and_restore(self):
        from die_shift_studio.area_filter import apply, canonical_project
        from die_shift_studio.core import GDSData
        from die_shift_studio.gds_shapes import edited_snapshot, shape_nodes, node_fingerprint
        tiny=np.asarray(box(-2,-2,-1.99,-1.99).exterior.coords)
        source=np.array([(0,0),(4,0),(4,.004),(4,4),(0,4)],float)
        raw=GDSData('fixture.gds','digest','TOP',['TOP'],.001,{'5/0':[tiny,source]})
        filtered,project=apply(raw,{'parameters':dict(minimum_area_um2=.5,minimum_edge_um=.01)})
        kind,nodes,_=shape_nodes(filtered,'5/0',0)
        edit=dict(layer='5/0',index=0,nodes=(nodes+[2.,0.]).tolist(),original=node_fingerprint(kind,nodes))
        saved=canonical_project(filtered,dict(project,node_edits=[edit]))
        self.assertEqual(saved['node_edits'][0]['index'],1)
        changed,local=apply(edited_snapshot(raw,saved['node_edits']),saved)
        self.assertEqual(len(changed.node_edits),1)
        self.assertEqual(changed.node_edits[0]['index'],0)
        self.assertEqual(canonical_project(changed,local)['node_edits'],saved['node_edits'])
        self.assertEqual(canonical_project(changed,dict(local,node_edits=[]))['node_edits'],[])
        _,before,_=shape_nodes(changed._source_snapshot,'5/0',0)
        _,after,_=shape_nodes(changed,'5/0',0)
        np.testing.assert_allclose(before,nodes);np.testing.assert_allclose(after,nodes+[2.,0.])

    def test_internal_manual_box_grows_transversally_without_touching_pads(self):
        plan=SimpleNamespace(source=box(-3,-5,103,5),tolerance=.001,width=10.,
                             route=LineString([(0,0),(100,0)]),length=100.,
                             land=box(-2,-2,2,2),anchors=[box(98,-2,102,2)],tangent=lambda _: (1.,0.),
                             allows_pad_point=False)
        box_inside=box(45,-1,52,1)
        result=usable_partition(plan,box_inside)
        self.assertTrue(result['A'].covers(box_inside))
        self.assertFalse(result['box_invalid'])
        self.assertEqual(result['near'].bounds,(45.,-5.,45.,5.))
        self.assertEqual(result['far'].bounds,(52.,-5.,52.,5.))

    def test_die_modes_change_only_contained_fixed_copper(self):
        via=Point(0,0).buffer(2)
        shapes={'127/0':[box(-200,-200,200,200)],'128/0':[box(-50,-50,50,50)],
                '3/0':[via.buffer(2)],'4/0':[via],
                '5/0':[box(-15,-15,15,15),box(28,-2,34,2),box(45,20,70,30)],
                '6/0':[]}
        p=Parameters(single_bound_x_um=8,line_space_um=2)
        for mode in ('follow','stationary','auto'):
            e,_,_=make_engine(shapes,p,ViewSettings(reconstruction_die_mode=mode))
            try:
                r=e.reconstructor
                result=r.frame(np.array([[8.,0.,0.]]),diagnostics=False)
                copper={row['indices'][0]:row['geometry'] for row in result['replacements']['5/0']}
                self.assertNotIn(('5/0',2),r.die_internal)
                if mode=='auto':
                    self.assertFalse(r.die_internal)
                    continue
                self.assertEqual(set(r.die_internal),{('5/0',0),('5/0',1)})
                self.assertAlmostEqual(copper[0].centroid.x,8 if mode=='follow' else 0)
                self.assertAlmostEqual(copper[1].centroid.x,39 if mode=='follow' else 31)
                self.assertFalse(result['hard_failures'])
            finally:e.close()

    def test_saved_page_offset_migrates_and_default_scope_is_risk(self):
        empty=normalize_project({'schema_version':5})
        self.assertEqual(empty['extreme_view']['mode'],'risk')
        self.assertEqual(empty['extreme_view']['page_size'],499)
        old={'schema_version':5,'extreme_view':dict(mode='focus',page=3,page_size=256),
             'settings':dict(table_page_limit=101)}
        migrated=normalize_project(old)
        self.assertEqual(migrated['extreme_view']['page'],6)
        self.assertEqual(migrated['extreme_view']['mode'],'focus')
        self.assertEqual(normalize_project(migrated),migrated)
