"""RDL reconstruction preview: cut across the route, rigid move, a cover polygon
built from the four corners of the two cut faces, independently of channel capacity.  Numbers below are measured on the checked-in examples."""
import json
import unittest
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box

from die_shift_studio.core import GDSData, Parameters, build_case, read_gds
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.reconstruction import AUTO_LANDING_DIAMETERS, SCOPE, transform
from die_shift_studio.settings import ViewSettings

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = '示例N_三线路窄通道宽度'        # pad smaller than the auto cut distance
BRIDGE = '示例F_逐芯片幅度与迭代窗口'      # stepped trace: a real cut, seam and cover polygon
PLAIN = '示例L_同网部分适配与既有接触'     # a seam with zero gap that still gets covered


def example(name=BRIDGE, settings=None, reconstruction=True):
    project = json.loads((ROOT / 'examples' / f'{name}.json').read_text(encoding='utf-8'))
    data = read_gds(ROOT / 'examples' / project['gds_path'])
    case = build_case(data, project['roles'], layer_order=project['layer_order'])
    engine = PreviewEngine(GeometryCache(data), case, project['roles'], {},
                           Parameters.from_dict(project['parameters']), None, case['connections'])
    settings = settings or ViewSettings()
    if reconstruction:
        engine.configure_reconstruction(True, settings)
    engine.configure_width(True, settings)
    return engine, data, case


def diagonal(settings=None):
    """A 45° fixed-RDL route under a 20x20 moving via, in one chip frame.

    The route runs from the lower left to the upper right, so a cut that meets
    both of its sides at a right angle is a 135° line.
    """
    def rect(*bounds):
        return [np.asarray(box(*bounds).exterior.coords)]
    root = np.sqrt(.5)
    span, half = 800., 20.
    corners = []
    for along, across in ((1., 1.), (1., -1.), (-1., -1.), (-1., 1.)):
        corners.append((along * root * span - across * root * half,
                        along * root * span + across * root * half))
    layers = {'9/0': rect(-1500., -900., 1500., 900.),
              '8/0': rect(-800., -400., 800., 400.),
              '4/0': rect(-110., -110., -90., -90.),
              '5/0': [np.asarray(corners)]}
    roles = {'9/0': '封装边框', '8/0': '芯片边框', '4/0': '运动过孔', '5/0': '固定RDL'}
    data = GDSData('local.gds', 'local', 'TOP', ['TOP'], .001, layers, roles)
    case = build_case(data, roles)
    engine = PreviewEngine(GeometryCache(data), case, roles, {}, Parameters(), None, case['connections'])
    engine.configure_reconstruction(True, settings or ViewSettings())
    engine.configure_width(True, settings or ViewSettings())
    return engine, data


def chain(settings=None):
    """One fixed-RDL trace carrying two landings of the same die, 40 um apart.

    The trace runs off to the left, which is the shared network; the two vias sit
    near its right end, so they must travel as one chain with a single cut taken
    beyond the landing farthest from the network.
    """
    def rect(*bounds):
        return [np.asarray(box(*bounds).exterior.coords)]
    layers = {'9/0': rect(-1500., -900., 1500., 900.),
              '8/0': rect(-800., -400., 800., 400.),
              '4/0': rect(-300., -20., -260., 20.) + rect(-200., -20., -160., 20.),
              '5/0': rect(-1200., -30., 60., 30.)}
    roles = {'9/0': '封装边框', '8/0': '芯片边框', '4/0': '运动过孔', '5/0': '固定RDL'}
    data = GDSData('local.gds', 'local', 'TOP', ['TOP'], .001, layers, roles)
    case = build_case(data, roles)
    engine = PreviewEngine(GeometryCache(data), case, roles, {}, Parameters(), None, case['connections'])
    engine.configure_reconstruction(True, settings or ViewSettings())
    engine.configure_width(True, settings or ViewSettings())
    return engine, data


def shared(settings=None):
    """One wide fixed-RDL plate spanning two chip frames, with a via in each.

    The drawing looks like the real package: one copper polygon covers several
    chips, so without an ownership rule the same plate would be claimed by both
    dies and would have to move two different ways at once. Connectivity alone
    cannot tell the two vias apart here (the shared plate merges them), which is
    why the test assigns the owners it is testing the split of.
    """
    def rect(*bounds):
        return [np.asarray(box(*bounds).exterior.coords)]
    layers = {'9/0': rect(-1500., -900., 1500., 900.),
              '8/0': rect(-1400., -300., -600., 300.) + rect(600., -300., 1400., 300.),
              '4/0': rect(-700., -20., -660., 20.) + rect(660., -20., 700., 20.),
              '5/0': rect(-1000., -100., 1000., 100.)}
    roles = {'9/0': '封装边框', '8/0': '芯片边框', '4/0': '运动过孔', '5/0': '固定RDL'}
    data = GDSData('local.gds', 'local', 'TOP', ['TOP'], .001, layers, roles)
    case = build_case(data, roles)
    engine = PreviewEngine(GeometryCache(data), case, roles, {}, Parameters(), None, case['connections'])
    engine.configure_reconstruction(True, settings or ViewSettings())
    engine.configure_width(True, settings or ViewSettings())
    for via in engine.reconstructor.vias:
        via['owner'] = 0 if via['center'][0] < 0 else 1
    return engine, data


def shifted(count, dx=0., dy=0., dtheta=0.):
    states = np.zeros((count, 3))
    states[0] = (dx, dy, dtheta)
    return states


def direction(line):
    coordinates = shapely.get_coordinates(line)
    delta = coordinates[-1] - coordinates[0]
    return delta / np.linalg.norm(delta)


class ReconstructionTests(unittest.TestCase):
    def test_no_motion_builds_no_pieces(self):
        engine, data, case = example()
        try:
            recon = engine.frame(np.zeros((2, 3)))['reconstruction']
            self.assertTrue(recon['complete'])
            self.assertEqual((recon['summary']['pieces'], recon['summary']['bridges'],
                              recon['summary']['orphans']), (0, 0, 0))
            self.assertEqual(recon['replacements'], {})
        finally:
            engine.close()

    def test_cut_meets_a_45_degree_route_at_a_right_angle(self):
        # The motion is along -x, but the route runs at 45°: a cut that keeps 90°
        # to both sides of the route must be a 135° line, not a vertical one.
        engine, data = diagonal()
        try:
            frame = engine.frame(shifted(1, -35.))
            recon = frame['reconstruction']
            self.assertEqual(recon['summary']['pieces'], 1)
            piece = recon['pieces'][0]
            cut = direction(piece['cut'])
            route = np.array([np.sqrt(.5), np.sqrt(.5)])
            self.assertAlmostEqual(abs(float(np.dot(cut, route))), 0., places=6)
            self.assertAlmostEqual(abs(cut[0]), abs(cut[1]), places=6)      # 135°
            self.assertGreater(abs(cut[1]), .5)                             # not vertical
            # The via sits on the movable side, and the seam is the face pair.
            via = shapely.Polygon(data.layers['4/0'][0])
            self.assertTrue(piece['movable'].covers(via))
            self.assertFalse(piece['immovable'].intersects(via))
            anchor, moved_anchor = piece['seam']
            self.assertIsNotNone(anchor)
            self.assertIsNotNone(moved_anchor)
            self.assertLessEqual(shapely.distance(shapely.Point(anchor), piece['cut']), 1e-6)
            self.assertLessEqual(shapely.distance(shapely.Point(anchor), piece['immovable']), 1e-6)
            self.assertLessEqual(shapely.distance(shapely.Point(moved_anchor), piece['moved']), 1e-6)
            # Midpoint to midpoint is the shift itself; the faces are parallel,
            # so the perpendicular crack is the shift projected on the route.
            np.testing.assert_allclose(np.asarray(moved_anchor) - np.asarray(anchor), (-35., 0.), atol=1e-6)
            self.assertAlmostEqual(piece['gap_um'], 35. * np.sqrt(.5), places=3)
            # bridge_length_um is the seam length (the cut face), and the cover
            # polygon is the parallelogram of that face and its moved copy.
            self.assertAlmostEqual(piece['bridge_length_um'], 40., places=3)
            self.assertAlmostEqual(piece['bridge_area_um2'],
                                   piece['bridge_length_um'] * piece['gap_um'], places=3)
        finally:
            engine.close()

    def test_cut_is_straight_across_a_stepped_trace(self):
        engine, data, case = example()
        try:
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertEqual((piece['layer'], piece['index'], piece['die']), ('5/0', 0, 0))
            # The route is horizontal here, so the right angle is a line of
            # constant x, and 2.5 landing diameters of a 20x20 via is 56.42 um.
            cut = shapely.get_coordinates(piece['cut'])
            self.assertTrue(np.allclose(cut[:, 0], cut[0, 0], atol=1e-6))
            self.assertAlmostEqual(float(cut[0, 0]), -66.41895835477563, places=6)
            self.assertAlmostEqual(piece['cut'].length, 32., places=3)   # the local trace width
            # cut_distance_um is the stated distance from the landing to the cut,
            # so it is the auto 2.5 landing diameters (a 20x20 via: 56.42 um).
            self.assertAlmostEqual(piece['cut_distance_um'], 56.418958354775626, places=6)
            self.assertAlmostEqual(piece['chain_span_um'], 10., places=3)
            via = shapely.Polygon(data.layers['4/0'][0])
            self.assertTrue(piece['movable'].covers(via))
            self.assertFalse(piece['immovable'].intersects(via))
        finally:
            engine.close()

    def test_movable_part_keeps_its_shape_and_the_seam_gap_is_the_shift(self):
        engine, data, case = example()
        try:
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertAlmostEqual(piece['movable_area_um2'], 4765.40666735282, places=6)
            self.assertAlmostEqual(piece['movable_area_um2'], piece['moved_area_um2'], places=6)
            self.assertAlmostEqual(piece['shift_um'], 35., places=6)
            self.assertAlmostEqual(piece['gap_um'], 35., places=6)
            # The cover spans the whole fissure: a 32 um face pushed 35 um across.
            self.assertAlmostEqual(piece['bridge_length_um'], 32., places=3)
            self.assertAlmostEqual(piece['bridge_area_um2'], 1120., places=1)
            self.assertAlmostEqual(piece['bridge_area_um2'],
                                   piece['bridge_length_um'] * piece['gap_um'], places=3)
            bounds = shapely.bounds(piece['moved'])
            self.assertAlmostEqual(bounds[0], -31.41895835477563, places=3)
            self.assertAlmostEqual(bounds[2], 65., places=3)
            self.assertAlmostEqual(shapely.bounds(piece['immovable'])[2], -66.41895835477563, places=3)
        finally:
            engine.close()

    def test_cover_polygon_is_built_from_the_four_cut_face_corners(self):
        engine, data, case = example()
        try:
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            cover = piece['bridge']
            self.assertIsNotNone(cover)
            self.assertEqual(cover.geom_type, 'Polygon')
            # Both faces are the same 32 um cut line, one shifted 35 um in x, so
            # the cover is the rectangle they span - the four corners are exactly
            # the face ends and their moved copies.
            corners = shapely.get_coordinates(cover.exterior)
            self.assertEqual(len(corners), 5)                            # closed ring
            face = shapely.get_coordinates(piece['cut'])
            expected = [np.asarray(q, float) for q in face]
            expected += [np.array([x + 35., y]) for x, y in expected]
            self.assertEqual(len(expected), 4)
            for corner in expected:
                closest = min(float(np.linalg.norm(np.asarray(q, float) - corner)) for q in corners)
                self.assertLess(closest, 1e-6)                           # the four corners, exactly
            self.assertAlmostEqual(cover.bounds[0], -66.41895835477563, places=6)
            self.assertAlmostEqual(cover.bounds[1], -16., places=3)
            self.assertAlmostEqual(cover.bounds[2], -31.41895835477563, places=6)
            self.assertAlmostEqual(cover.bounds[3], 16., places=3)
            # The seams sit on its edges, and it touches the copper on both sides.
            anchor, moved_anchor = piece['seam']
            self.assertTrue(cover.covers(shapely.Point(anchor)))
            self.assertTrue(cover.covers(shapely.Point(moved_anchor)))
            self.assertLessEqual(shapely.distance(cover, piece['moved']), 1e-6)
            self.assertLessEqual(shapely.distance(cover, piece['immovable']), 1e-6)
            # The trace is horizontal, so it crosses the vertical seam at 90°.
            self.assertAlmostEqual(abs(float(np.dot(direction(piece['cut']), np.array([1., 0.])))), 0.,
                                   places=6)
            joined = engine.frame(shifted(2, 35.))['reconstruction']['replacements']['5/0'][piece['index']]['geometry']
            self.assertTrue(joined.covers(piece['moved']))
            self.assertTrue(joined.covers(piece['immovable']))
            self.assertTrue(joined.covers(cover))
        finally:
            engine.close()

    def test_a_narrow_seam_is_still_covered(self):
        # Example L has an immovable island inside the movable span, so the two
        # blocks end up with a seam far narrower than the shift (the crack is the
        # shift projected on the cut normal). The cover is added all the same:
        # the rule is "cover every open fissure", not "cover when there is a
        # short". The block whose copper reaches its neighbour leaves no fissure,
        # so nothing is added there.
        engine, data, case = example(PLAIN)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            covered = [q for q in recon['pieces'] if q['bridge'] is not None]
            self.assertEqual(len(covered), 1)
            piece = covered[0]
            self.assertEqual((piece['layer'], piece['index']), ('5/0', 0))
            self.assertFalse(piece['orphan'])
            self.assertGreater(piece['gap_um'], 0.)
            self.assertLess(piece['gap_um'], piece['shift_um'])          # no short, still covered
            self.assertAlmostEqual(piece['bridge_area_um2'], 1050., places=1)
            # The cut face is the whole trace cross-section (30.94 um), not the
            # sliver a line-on-edge intersection used to return.
            self.assertAlmostEqual(piece['bridge_length_um'], 30.94, places=2)
            closed = [q for q in recon['pieces'] if q['bridge'] is None and not q['orphan']]
            self.assertEqual(len(closed), 1)
            self.assertAlmostEqual(closed[0]['gap_um'], 0., places=6)
            self.assertGreaterEqual(recon['summary']['bridges'], 1)
            self.assertAlmostEqual(recon['summary']['cover_area_um2'], 1050., places=1)
        finally:
            engine.close()

    def test_a_flush_seam_is_counted_apart_from_the_covered_ones(self):
        # Example L: one of its three pieces ends up flush with the copper it
        # left (gap 0). The copper keeps running in the direction the piece
        # travels, so no cut position can open those seams; they are counted as
        # 贴合 in the summary rather than hidden or blamed as defects.
        engine, data, case = example(PLAIN)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            summary = recon['summary']
            flush = [q for q in recon['pieces']
                     if q['gap_um'] is not None and q['gap_um'] <= 1e-6]
            self.assertEqual(len(flush), 1)
            self.assertEqual(summary['flush'], len(flush))
            self.assertEqual(summary['orphans'], 0)
            self.assertTrue(all(not q['orphan'] for q in flush))
            # A flush seam gets no cover of its own (nothing is open there) and is
            # still reported apart from the covered pieces.
            self.assertEqual(sum(q['bridge'] is not None for q in flush), 0)
        finally:
            engine.close()

    def test_whole_feature_follows_the_die_when_the_cut_falls_past_it(self):
        # Example N's 60x200 via has a 123.6 um landing diameter, so the auto
        # distance puts the cut well outside its 90x230 pad: the pad is movable.
        engine, data, case = example(CHANNEL)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            piece = recon['pieces'][0]
            self.assertTrue(piece['orphan'])
            self.assertTrue(piece['immovable'].is_empty)
            self.assertIsNone(piece['gap_um'])
            self.assertIsNone(piece['bridge'])
            self.assertIsNone(piece['bridge_area_um2'])
            self.assertAlmostEqual(piece['movable_area_um2'], 20700., places=1)
            self.assertAlmostEqual(piece['cut_distance_um'], 309.01936161855167, places=6)
            self.assertEqual(recon['summary']['orphans'], 1)
            self.assertEqual(recon['summary']['bridges'], 0)
        finally:
            engine.close()

    def test_explicit_distance_replaces_the_auto_landing_diameters(self):
        engine, data, case = example(settings=ViewSettings(reconstruction_length_um=30.))
        try:
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertFalse(piece['orphan'])
            self.assertAlmostEqual(piece['cut_distance_um'], 30., places=6)
            self.assertAlmostEqual(piece['movable_area_um2'], 3920.0, places=1)
            self.assertAlmostEqual(piece['gap_um'], 35., places=6)
            # The cover is the face parallelogram, so its area is face x gap.
            self.assertAlmostEqual(piece['bridge_area_um2'],
                                   piece['bridge_length_um'] * piece['gap_um'], places=3)
            self.assertGreater(piece['bridge_area_um2'], 0.)
        finally:
            engine.close()

    def test_close_landings_share_one_cut_beyond_the_farthest_one(self):
        # Two landings of one die, 40 um apart on one trace: they travel as one
        # chain, so the cut is taken once, beyond the landing farthest from the
        # network -- never between the two landings, which is what used to make a
        # cut sit well past a landing when a feature carried several of them.
        engine, data = chain()
        try:
            recon = engine.frame(shifted(1, 35.))['reconstruction']
            self.assertEqual(recon['summary']['pieces'], 1)
            piece = recon['pieces'][0]
            self.assertEqual(piece['vias'], [0, 1])
            lands = [shapely.Polygon(q) for q in data.layers['4/0']]
            both = shapely.union_all(lands)
            self.assertTrue(piece['movable'].covers(both))
            self.assertFalse(piece['immovable'].intersects(both))
            cut = piece['cut']
            self.assertLess(shapely.bounds(cut)[2], min(q.bounds[0] for q in lands))
            self.assertAlmostEqual(cut.length, 60., places=6)            # the trace width
            step = AUTO_LANDING_DIAMETERS * 2. * np.sqrt(1600. / np.pi)   # 2.5 landing diameters
            self.assertAlmostEqual(piece['cut_distance_um'], step, places=6)
            self.assertAlmostEqual(piece['chain_span_um'], 70., places=6)  # via 1 edge to the pivot
            self.assertAlmostEqual(piece['gap_um'], 35., places=6)
            self.assertAlmostEqual(piece['bridge_area_um2'], 2100., places=1)
            self.assertEqual(recon['summary']['bridges'], 1)
        finally:
            engine.close()

    def test_rotated_die_keeps_the_movable_part_on_its_via(self):
        engine, data, case = example()
        try:
            states = shifted(2, 0., 0., .4)
            piece = engine.frame(states)['reconstruction']['pieces'][0]
            via = shapely.Polygon(data.layers['4/0'][0])
            moved_via = engine.contact.moved(via, 0, states)
            self.assertTrue(piece['moved'].covers(moved_via))
            self.assertAlmostEqual(piece['moved_area_um2'], piece['movable_area_um2'], places=6)
            centre = engine.contact.bound.centers[0]
            self.assertTrue(piece['moved'].equals(transform(piece['movable'], centre, states[0])))
        finally:
            engine.close()

    def test_view_settings_key_drives_the_reconstruction(self):
        engine, data, case = example()
        try:
            first = engine.frame(shifted(2, 35.))
            self.assertIs(engine.frame(shifted(2, 35.)), first)      # memoised
            self.assertAlmostEqual(first['reconstruction']['pieces'][0]['cut_distance_um'], 56.418958354775626, places=6)
            old = engine.cache_key()
            engine.configure_reconstruction(True, ViewSettings(reconstruction_length_um=100.))
            self.assertNotEqual(engine.cache_key(), old)
            deeper = engine.frame(shifted(2, 35.))
            self.assertAlmostEqual(deeper['reconstruction']['pieces'][0]['cut_distance_um'], 100., places=6)
        finally:
            engine.close()

    def test_reconstruction_moves_real_copper_independently_of_channel_capacity(self):
        engine, data, case = example(BRIDGE)
        try:
            states=shifted(2,35.)
            states[1]=(-35.,0.,0.)
            frame=engine.frame(states)
            self.assertEqual(frame['reconstruction']['summary']['pieces'],2)
            replacements=frame['reconstruction']['replacements']['5/0']
            # The actual reconstructed copper touches the opposite moving via.
            hits=[]
            for record in engine.contact.records:
                via=engine.contact.moved(record['poly'],record['owner'],states)
                foreign=[q['geometry'] for q in replacements if any(engine.contact.nodes[engine.contact.ids['5/0',i]]['net']!=record['net'] for i in q['indices'])]
                if foreign:hits.append(min(via.distance(g) for g in foreign))
            self.assertIn(0.,hits)
            engine.configure_reconstruction(False,ViewSettings())
            plain=engine.frame(states)
            self.assertNotIn('reconstruction',plain)
            self.assertEqual(plain['width_analysis']['rows'],frame['width_analysis']['rows'])
        finally:engine.close()

    def test_a_plate_shared_by_two_chips_is_split_at_the_frame(self):
        # One fixed-RDL polygon covers two chips. Copper may follow only the die
        # whose chip frame holds it: each die owns the part of the plate inside
        # its own frame, the part between the frames belongs to neither die and
        # stays put, and when a half moves away from it the frame edge opens a
        # seam that is covered like any other cut.
        engine, data = shared()
        try:
            # each die moves away from the shared middle, so both seams open
            states = np.array([[-35., 0., 0.], [35., 0., 0.]])
            recon = engine.frame(states)['reconstruction']
            self.assertEqual(recon['summary']['pieces'], 2)
            pieces = {q['die']: q for q in recon['pieces']}
            left, right = box(-1400., -300., -600., 300.), box(600., -300., 1400., 300.)
            for die, frame in ((0, left), (1, right)):
                piece = pieces[die]
                # every piece stays inside its own chip
                self.assertLessEqual(shapely.difference(piece['movable'], frame).area, 1e-6)
                # and keeps the copper its via lands on
                land = shapely.Polygon(data.layers['4/0'][die])
                self.assertTrue(piece['movable'].covers(land))
            # the plate between the chips is claimed by neither die
            self.assertEqual(shapely.intersection(pieces[0]['movable'],
                                                 pieces[1]['movable']).area, 0.)
            self.assertLess(pieces[0]['movable'].bounds[2], pieces[1]['movable'].bounds[0])
            # and neither die drags the other's copper along
            self.assertEqual(shapely.intersection(pieces[0]['moved'],
                                                 pieces[1]['moved']).area, 0.)
            self.assertLess(pieces[0]['moved'].bounds[2], pieces[1]['moved'].bounds[0])
            # the frame edge is a seam: gap = shift over the whole frame edge,
            # covered by the four corners of the two faces
            for piece in pieces.values():
                self.assertAlmostEqual(piece['gap_um'], 35., places=3)
                self.assertAlmostEqual(piece['bridge_length_um'], 200., places=3)
                self.assertAlmostEqual(piece['bridge_area_um2'], 35. * 200., places=1)
            self.assertAlmostEqual(recon['summary']['cover_area_um2'], 2. * 35. * 200., places=1)
        finally:
            engine.close()

    def test_a_shared_plate_that_moves_onto_the_middle_needs_no_cover(self):
        # Same split, but the half moves towards the shared middle: the moved
        # copper touches the copper that stays, so no fissure is left to cover.
        engine, data = shared()
        try:
            states = np.array([[35., 0., 0.], [35., 0., 0.]])
            pieces = {q['die']: q for q in engine.frame(states)['reconstruction']['pieces']}
            self.assertAlmostEqual(pieces[0]['gap_um'], 0., places=3)
            self.assertIsNone(pieces[0]['bridge'])
            # the half that moves away from the middle still gets its cover
            self.assertAlmostEqual(pieces[1]['gap_um'], 35., places=3)
            self.assertAlmostEqual(pieces[1]['bridge_area_um2'], 35. * 200., places=1)
        finally:
            engine.close()

    def test_scope_and_parameters_state_that_the_split_is_a_guess(self):
        engine, data, case = example()
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            self.assertEqual(recon['scope'], SCOPE)
            self.assertIn('自动划分', recon['scope'])
            self.assertIn('不代表产线', recon['scope'])
            self.assertEqual(recon['parameters'],
                             dict(length_um=0., width_um=0., overlap_um=0., search_cut=False,
                                  landing_diameters=AUTO_LANDING_DIAMETERS))
            self.assertEqual(recon['summary']['width_um'], 0.)
            self.assertAlmostEqual(recon['summary']['cover_area_um2'], 1120., places=1)
            self.assertEqual(recon['summary']['landing_diameters'], AUTO_LANDING_DIAMETERS)
            self.assertAlmostEqual(recon['summary']['max_shift_um'], 35., places=6)
        finally:
            engine.close()


    def test_the_fixed_part_of_the_trace_completes_the_warm_trace(self):
        engine, settings, data = example()
        try:
            frame = engine.frame(shifted(2, 35.))
            piece = frame['reconstruction']['pieces'][0]
            self.assertFalse(piece['fixed'].is_empty)
            self.assertGreater(piece['fixed_area_um2'], 0.)
            # 固定区不动的铜与移动区原位互不重叠，合起来正好是该图形留在名义
            # 位置上的全部铜（暖色固定区 + 暖色移动后 = 一条完整走线）
            movable = piece['movable']
            self.assertLess(piece['fixed'].intersection(movable).area, 1e-6)
            self.assertAlmostEqual(shapely.union_all([piece['fixed'], movable]).area,
                                   piece['fixed_area_um2'] + piece['movable_area_um2'], places=6)
            summary = frame['reconstruction']['summary']
            self.assertAlmostEqual(summary['fixed_area_um2'], piece['fixed_area_um2'], places=6)
        finally:
            engine.close()

    def test_an_unavoidable_short_is_marked(self):
        # Example F at +35: the shifted pad reaches the neighbouring pad (5 um
        # against a 10 um rule) whatever the cut position, because the motion
        # itself carries the copper there. The piece is marked and carries the
        # measured clearance, the offending net and the line to it.
        engine, data, case = example(BRIDGE)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            self.assertEqual(recon['summary']['conflicts'], 1)
            piece = recon['pieces'][0]
            self.assertTrue(piece['conflict'])
            self.assertAlmostEqual(piece['clearance_um'], 5., places=6)
            self.assertEqual(piece['conflict_net'], 1)
            self.assertIsNotNone(piece['conflict_link'])
            self.assertLess(piece['clearance_um'], recon['summary']['rule_space_um'])
        finally:
            engine.close()

    def test_spacing_follows_the_reconstructed_neighbour(self):
        """The dimension must describe the reconstructed layout, not the nominal one.

        At the mixed pose the two pads travel toward each other, so the copper
        that used to be 5 um away now overlaps. A nominal measurement would keep
        reporting 5 um; the reconstruction has to report the overlap.
        """
        engine,_,_=example()
        try:
            frame=engine.frame(np.array([[35.,0.,0.],[-35.,0.,0.]]),lambda a,b:None,quick=True)
            recon=frame['reconstruction']
            self.assertTrue(recon['pieces'])
            toler=engine.reconstructor.tol*2
            for piece in recon['pieces']:
                self.assertIsNotNone(piece['clearance_um'])
                self.assertLessEqual(piece['clearance_um'],toler)
                self.assertTrue(piece['conflict'])
                self.assertIsNotNone(piece['conflict_link'])
            self.assertGreaterEqual(recon['summary']['conflicts'],1)
        finally:
            engine.close()

    def test_a_safe_piece_is_not_marked(self):
        # Example L sits 15 um from the next net, exactly on the rule, so the
        # piece is not marked — the mark is for a real violation of the rule.
        engine, data, case = example(PLAIN)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            self.assertEqual(recon['summary']['conflicts'], 0)
            piece = recon['pieces'][0]
            self.assertFalse(piece['conflict'])
            self.assertAlmostEqual(piece['clearance_um'], 15., places=6)
            self.assertAlmostEqual(recon['summary']['rule_space_um'], 15., places=6)
        finally:
            engine.close()

    def plane_of(self, piece, move=0.):
        """Manual plane of a piece: through its cut, moved ``move`` μm along the normal."""
        parts = piece['cut'].geoms if hasattr(piece['cut'], 'geoms') else [piece['cut']]
        line = max(parts, key=lambda q: q.length)
        first, second = np.asarray(line.coords[0], float), np.asarray(line.coords[-1], float)
        direction = second - first
        span = float(np.linalg.norm(direction))
        normal = np.array([-direction[1], direction[0]], float) / span
        origin = (first + second) / 2.
        fixed = piece['immovable'].centroid.coords[0]
        if float(np.dot(normal, np.asarray(fixed, float) - origin)) < 0.:
            normal = -normal
        origin = origin + normal * move
        return (piece['layer'], int(piece['index']), tuple(int(v) for v in piece['vias']),
                float(normal[0]), float(normal[1]), float(origin[0]), float(origin[1]))

    def test_a_dragged_cut_plane_pins_the_cut(self):
        engine, data, case = example()
        try:
            base = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertFalse(base['manual'])
            for move in (10., -10.):
                plane = self.plane_of(base, move)
                engine.configure_reconstruction(
                    True, ViewSettings(reconstruction_cuts=(plane,)))
                piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
                self.assertTrue(piece['manual'])
                self.assertAlmostEqual(piece['cut_distance_um'],
                                       base['cut_distance_um'] + move, places=6)
        finally:
            engine.close()

    def test_a_dragged_plane_inside_the_landing_is_pushed_back_to_its_edge(self):
        engine, data, case = example()
        try:
            base = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            plane = self.plane_of(base, -base['cut_distance_um'] - 40.)
            engine.configure_reconstruction(
                True, ViewSettings(reconstruction_cuts=(plane,)))
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertTrue(piece['manual'])
            self.assertGreater(piece['cut_distance_um'], -1e-9)
            self.assertLessEqual(piece['cut_distance_um'], 8. * engine.reconstructor.tol)
        finally:
            engine.close()

    def test_a_drag_past_the_copper_end_stops_at_the_last_crossing(self):
        engine, data, case = example()
        try:
            base = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            plane = self.plane_of(base, 20000.)
            engine.configure_reconstruction(
                True, ViewSettings(reconstruction_cuts=(plane,)))
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            piece = recon['pieces'][0]
            self.assertTrue(piece['manual'])
            # The cut stays inside the feature it belongs to and the piece keeps
            # its own movable copper, so a far drag cannot swallow the whole layer.
            self.assertGreater(piece['cut_distance_um'], base['cut_distance_um'])
            self.assertLess(piece['cut_distance_um'], 1230.)
            self.assertLess(piece['movable_area_um2'], 44000.)
            self.assertEqual(recon['summary']['manual_cuts'], 1)
        finally:
            engine.close()

    def test_a_plane_beyond_the_copper_end_is_flagged_as_clamped(self):
        engine, data, case = example()
        try:
            base = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            plane = self.plane_of(base, 20000.)
            engine.configure_reconstruction(
                True, ViewSettings(reconstruction_cuts=(plane,)))
            piece = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertTrue(piece['manual'])
            self.assertTrue(piece['cut_clamped'])
            window = piece['cut_window']
            self.assertLessEqual(piece['cut_distance_um'],
                                 float(window['high']) - float(window['low']) + 1e-3)
        finally:
            engine.close()

    def test_a_pinned_cut_can_be_dragged_again(self):
        engine, data, case = example()
        try:
            base = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            engine.configure_reconstruction(
                True, ViewSettings(
                                   reconstruction_cuts=(self.plane_of(base, 10.),)))
            first = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertAlmostEqual(first['cut_distance_um'], base['cut_distance_um'] + 10., places=6)
            # Dragging the already pinned cut must move it again, not restart
            # from the automatic choice (the guide line is clamped, the stored
            # plane is not re-used as a limit).
            engine.configure_reconstruction(
                True, ViewSettings(
                                   reconstruction_cuts=(self.plane_of(first, 20.),)))
            second = engine.frame(shifted(2, 35.))['reconstruction']['pieces'][0]
            self.assertAlmostEqual(second['cut_distance_um'], first['cut_distance_um'] + 20., places=6)
        finally:
            engine.close()

    def test_search_targets_cache_and_final_clearance(self):
        from unittest.mock import patch
        for name in (CHANNEL, BRIDGE):
            with self.subTest(example=name):
                engine, _, _ = example(name, settings=ViewSettings(reconstruction_search=True))
                r = engine.reconstructor
                try:
                    states = np.zeros((len(r.model.bound.centers), 3))
                    states[0] = [35., 0., .01]; states[-1] = [-35., 10., -.01]
                    targets = [ref for ref, vias in r.vias_by_target.items()
                               if all(v['owner'] == 0 for v in vias)]
                    self.assertTrue(targets)
                    subset = r.frame(states, targets=targets)
                    quick = r.frame(states, quick=True); full = r.frame(states)
                    self.assertIsNot(quick, full)
                    self.assertFalse(any(p['manual'] for p in full['pieces']))
                    def geometry(frame, layer, index):
                        return next(q['geometry'] for q in frame['replacements'][layer]
                                    if index in q['indices'])
                    for layer, index in targets:
                        self.assertTrue(geometry(subset, layer, index).equals(geometry(full, layer, index)))
                    peer = states.copy(); peer[-1] = [5., 5., .01]
                    with patch.object(r, '_source_blocks', side_effect=AssertionError('unexpected repartition')):
                        again = r.frame(peer, targets=targets)
                    for layer, index in targets:
                        self.assertTrue(geometry(again, layer, index).equals(geometry(subset, layer, index)))
                    for layer, rows in full['replacements'].items():
                        for row in rows:
                            self.assertTrue(row['geometry'].equals(geometry(quick, layer, row['indices'][0])))
                    for piece in full['pieces']:
                        layer = piece['layer']
                        replaced = {i: q['geometry'] for q in full['replacements'].get(layer, []) for i in q['indices']}
                        others = [replaced.get(i, g) for i, g in enumerate(r.model.cache.layers[layer]['geometry'])
                                  if int(r.model.nodes[r.model.ids[layer, i]]['net']) != piece['net']]
                        if others:
                            probe = shapely.union_all([piece['moved']] +
                                                      ([piece['bridge']] if piece['bridge'] is not None else []))
                            self.assertAlmostEqual(piece['clearance_um'], min(probe.distance(g) for g in others), places=9)
                    layer, index = targets[0]
                    source = r.model.cache.layers[layer]['geometry'][index]
                    changed = {'replacements': {layer: [dict(indices=(index,), geometry=source.buffer(4.))]}}
                    with patch.object(r, '_source_blocks', wraps=r._source_blocks) as partition:
                        r.frame(states, changed, targets=[(layer, index)])
                        self.assertTrue(partition.called)
                finally:
                    engine.close()


if __name__ == '__main__':
    unittest.main()
