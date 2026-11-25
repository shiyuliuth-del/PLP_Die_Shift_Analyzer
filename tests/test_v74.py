"""SH22 contour-rail ownership and one-to-one nominal motion anchors."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import shapely

from die_shift_studio.between_motion import component_modes
from die_shift_studio.contact import ContactModel
from die_shift_studio.core import Parameters, build_case, read_gds
from die_shift_studio.dxf_export import snapshot
from die_shift_studio.dxf_settings import DxfSettings
from die_shift_studio.fast_preview import GeometryCache
from die_shift_studio.gds_merge import restore_project_geometry
from die_shift_studio.project import normalize_project
from die_shift_studio.reconstruction import Reconstructor
from die_shift_studio.reconstruction_zones import live_serial
from die_shift_studio.region_export import write_regions
from die_shift_studio.region_import import read
from die_shift_studio.settings import ViewSettings
from test_v72 import vertices

ROOT = Path(__file__).resolve().parents[1]


class V74Tests(unittest.TestCase):
    def test_close_branches_do_not_share_one_override(self):
        between = {
            0: shapely.box(0, 0, 20, 10),
            1: shapely.box(0, 10.001, 20, 20),
            2: shapely.box(20, 0, 40, 10),
        }
        ref = ('2/0', 0)
        settings = replace(ViewSettings(), reconstruction_between_overrides=(
            (*ref, 10., 9.9999, 'min'),
            (*ref, 29., 5., 'average'),
        ))
        self.assertEqual(component_modes(settings, ref, between), {
            0: ('min', True), 1: ('max', False), 2: ('average', True),
        })

    def test_sh22_readback_labels_both_rails_at_user_location(self):
        job = ROOT/'Jobs'/'SH22'/'SH22.dshift.json'
        project = normalize_project(json.loads(job.read_text(encoding='utf-8')))
        raw = read_gds(job.parent/project['gds_path'], project['top_cell'])
        data, project = restore_project_geometry(raw, project)
        roles = project['roles']
        params = Parameters.from_dict(project['parameters'])
        case = build_case(data, roles, layer_order=project['layer_order'])
        cache = GeometryCache(data)
        model = ContactModel(data, case, roles, case['connections'], cache, parameters=params)
        layer = '2/0'
        x, y = -241967., 245577.
        source_index, source = next((i, g) for i, g in enumerate(cache.geometries[layer])
                                    if g.covers(shapely.Point(x, y)))
        ref = (layer, source_index)
        settings = replace(ViewSettings.from_dict(project['settings']),
                           reconstruction_boxes=((layer, vertices(x-18, y-18, x+18, y+18)),),
                           reconstruction_box_order=('free',))
        solver = Reconstructor(model, params, settings)
        try:
            # Limit construction to the actual source net; its physical terminals
            # and design neighbours still come from the unmodified SH22 input.
            solver.vias_by_target = {ref: solver.vias_by_target[ref]}
            zero = np.zeros((len(case['dies']), 3))
            nominal = solver.frame(zero, targets={ref}, diagnostics=False, _raw=True)
            self.assertTrue(any(q.get('boxed') and not q.get('box_invalid')
                                and q['nominal_regions']['A'].covers(shapely.Point(x,y))
                                for q in nominal['pieces']))
            block = min((b for b in nominal['between_blocks'] if not b.get('locked')),
                        key=lambda b: b['nominal'].distance(shapely.Point(x,y)))
            edited = replace(settings, reconstruction_between_overrides=((*block['id'], 'min'),))
            draft = live_serial(solver, edited, dict(states=zero, reconstruction=nominal), ref, draft=True)
            frame = draft['reconstruction']
            minimum, = [b for b in frame['between_blocks'] if b['id'] == block['id']]
            self.assertEqual(minimum['mode'], 'min')
            self.assertTrue(minimum['custom'])
            self.assertFalse(minimum['moves'])  # The smaller physical side is fixed here.
            frame = dict(frame, pieces=[dict(q, regions=q['nominal_regions']) for q in frame['pieces']])
            layout = replace(snapshot(data, cache), layers={layer: (source.wkb,)})
            with TemporaryDirectory() as tmp:
                target = Path(tmp)/'sh22.dxf'
                for amount in (0., 1.5, -1.5):
                    with self.subTest(compensation=amount):
                        response = write_regions(layout, frame, roles, target, settings=DxfSettings(),
                                                 compensation={layer:amount}, dbu=data.dbu_um)
                        self.assertTrue(response['topology']['L2_D0']['closed'])
                        contours = {label:shapely.union_all(lines) for label,lines in read(target)[layer].items()}
                        for offset, expected in ((-25., 'B'), (-5., 'C'), (0., 'C'), (5., 'C'), (25., 'B')):
                            # Sample both physical sides of the same trace on a
                            # perpendicular cross-section, away from the joins.
                            a, b = x+offset, y+offset
                            probe = shapely.LineString([(a-6,b+6),(a+23,b-23)])
                            self.assertIn(expected, contours)
                            for label, outline in contours.items():
                                touch = outline.intersection(probe)
                                self.assertEqual(0 if touch.is_empty else len(shapely.get_parts(touch)),
                                                 2 if label==expected else 0,
                                                 (amount, offset, label, touch.wkt))
        finally:
            solver.close()


if __name__ == '__main__':
    unittest.main()
