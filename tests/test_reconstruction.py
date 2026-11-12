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


















    def test_an_unavoidable_short_is_marked(self):
        # Example F at +35: the shifted pad reaches the neighbouring pad (5 um
        # against a 10 um rule) whatever the cut position, because the motion
        # itself carries the copper there. The piece is marked and carries the
        # measured clearance, the offending net and the line to it.
        engine, data, case = example(BRIDGE)
        try:
            recon = engine.frame(shifted(2, 35.))['reconstruction']
            self.assertEqual(recon['summary']['conflicts'], 2)  # both nominal plan rows report the same offending pair
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








if __name__ == '__main__':
    unittest.main()
