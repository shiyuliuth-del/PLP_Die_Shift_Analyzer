"""Real SH22 ownership and independently read CAD outline regressions."""
from collections import defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import tempfile
import unittest

import ezdxf
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box

from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.dxf_export import Snapshot
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.region_export import write_regions
from test_v38 import expanded_outlines


class V61Tests(unittest.TestCase):
    def test_sh22_vias_follow_their_chip_even_through_cross_die_fixed_rdl(self):
        root = Path(__file__).resolve().parents[1] / 'Jobs' / 'SH22'
        project = json.loads((root / 'SH22.dshift.json').read_text(encoding='utf-8'))
        data, project = restore_project_geometry(read_gds(root / 'sh22.gds', 'TOP'), project)
        roles = project['roles']
        case = build_case(data, roles, layer_order=project['layer_order'])
        cache = GeometryCache(data)
        params = Parameters.from_dict(project['parameters'])
        geometry = cache.layers['1/0']['geometry']
        indices, owners = [], []
        for xy, owner in [((-240910, 246311), 1), ((-243387, 245147), 0), ((-242996, 245508), 0)]:
            hits = [i for i, g in enumerate(geometry) if g.covers(Point(xy))]
            self.assertEqual(len(hits), 1, xy)
            indices.append(hits[0])
            owners.append(owner)
            self.assertTrue(case['dies'][owner].covers(geometry[hits[0]]))
        states = np.array([[15., -15., math.radians(1.)], [-15., 15., 0.]])
        with closing(PreviewEngine(cache, case, roles, {}, params, None, case['connections'])) as engine:
            frame = engine.frame(states)
            records = {r['index']: r['owner'] for r in engine.contact.records if r['key'] == '1/0'}
            for index, owner in zip(indices, owners):
                self.assertEqual(records[index], owner)
                initial = np.asarray(data.layers['1/0'][index])
                pivot = np.asarray(case['dies'][owner].centroid.coords[0])
                c, s = math.cos(states[owner, 2]), math.sin(states[owner, 2])
                rotation = np.array([[c, -s], [s, c]])
                expected = (initial - pivot) @ rotation.T + pivot + states[owner, :2]
                np.testing.assert_allclose(frame['layers']['1/0']['polygons'][index], expected, atol=1e-9, rtol=0.)
            fixed = frame['layers']['3/0']['polygons']
            for actual, original in zip(fixed, data.layers['3/0']):
                np.testing.assert_array_equal(actual, original)
            actual_dies = defaultdict(set)
            for node in engine.contact.nodes:
                if node['owner'] >= 0:
                    actual_dies[node['net']].add(node['owner'] + 1)
            self.assertEqual({r['network']: set(r['dies']) for r in engine.contact.interdie_networks},
                             {net: dies for net, dies in actual_dies.items() if len(dies) > 1})

    def test_partial_coincidence_is_emitted_once_and_blocks_keep_source_identity(self):
        source = box(0, 0, 100, 30).difference(box(40, 10, 60, 20))
        other = box(20, 30, 40, 50)  # shares part of the long edge, with different endpoints
        separate = box(150, 10, 170, 30)
        board, die = box(-20, -20, 200, 100), box(12, 52, 22, 62)
        extra_vertex = Polygon([(-20, -20), (0, -20), (200, -20), (200, 100), (-20, 100)])
        snap = Snapshot({'5/0': (source.wkb, source.wkb, separate.wkb), '6/0': (other.wkb,),
                         '7/0': (other.wkb,), '127/0': (board.wkb, extra_vertex.wkb),
                         '128/0': (die.wkb,)}, 'TOP', None, 0)
        roles = {'5/0': '固定RDL', '6/0': '固定RDL', '7/0': '固定RDL', '127/0': '封装边框', '128/0': '芯片边框'}
        areas = dict(A=box(30, -1, 70, 31), B=box(70, -1, 110, 31), C=box(-1, -1, 30, 31))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'blocks.dxf'
            write_regions(snap, {'pieces': [dict(layer='5/0', regions=areas)]}, roles, path)
            drawing = ezdxf.readfile(path)
            self.assertFalse(drawing.audit().errors)
            model = drawing.modelspace()
            inserts = list(model.query('INSERT'))
            self.assertEqual(len(model.query('LWPOLYLINE')), 2)
            self.assertEqual(sum(q.dxf.layer == 'L5_D0_B' for q in inserts), 2)
            self.assertEqual(sum(q.dxf.layer == 'L5_D0_C' for q in inserts), 1)
            self.assertFalse(any(q.dxf.layer.startswith('L7_D0') for q in inserts))
            transition = next(q for q in inserts if q.dxf.layer == 'L5_D0_C')
            self.assertGreater(len(list(transition.virtual_entities())), 2)  # outer segments and hole in one block
            self.assertFalse(list(model.query('HATCH')))
            lines = []
            for layer, xy, closed in expanded_outlines(model):
                if closed:
                    self.assertIn(layer, ('RKG_REF','DIE_REF'))
                    expected = board if layer == 'RKG_REF' else die
                    self.assertTrue(shapely.LinearRing(xy).equals(expected.exterior))
                else:
                    for p, q in zip(xy[:-1], xy[1:]):
                        lines.append(LineString((p, q)))
            merged = shapely.union_all(lines)
            self.assertTrue(merged.equals(shapely.union_all([g.boundary for g in (source, other, separate)])))
            self.assertAlmostEqual(sum(line.length for line in lines), merged.length)

    def test_conflicting_reference_edges_keep_previous_output(self):
        snap = Snapshot({'127/0': (box(0, 0, 100, 100).wkb,),
                         '128/0': (box(0, 20, 20, 40).wkb,)}, 'TOP', None, 0)
        roles = {'127/0': '封装边框', '128/0': '芯片边框'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'existing.dxf'
            path.write_bytes(b'old drawing')
            with self.assertRaisesRegex(ValueError, '参考边框之间有重叠'):
                write_regions(snap, {}, roles, path)
            self.assertEqual(path.read_bytes(), b'old drawing')
