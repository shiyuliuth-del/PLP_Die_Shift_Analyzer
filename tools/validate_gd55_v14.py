"""Read-only V14 regression of the user-supplied GD55 layout.

Run with --gds /path/to/GD55_9-21.gds. The attachment is never bundled or edited.
Process numbers below are explicit test assumptions, not inferred factory rules.
"""
import argparse
from dataclasses import asdict, replace
from itertools import product
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import shapely
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from die_shift_studio.core import Parameters, read_gds, build_case
from die_shift_studio.fast_preview import GeometryCache, PreviewEngine
from die_shift_studio.pivots import rotation_centers
from die_shift_studio.probability_overlay import probability_maps, coverage_envelope
from die_shift_studio.representatives import RepresentativeExtremes
from die_shift_studio.settings import ViewSettings
from die_shift_studio.space_map import space_maps, package_map
from die_shift_studio.statistics import statistical_state
from tools.benchmark_v13 import run

ROLES = {'127/0': '封装边框', '128/0': '芯片边框', '3/0': '运动RDL',
         '4/0': '运动过孔', '5/0': '固定RDL', '6/0': '固定过孔'}
ORDER = ['6/0', '5/0', '4/0', '3/0', '128/0', '127/0']


def numerical(path):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = read_gds(path)
    case = build_case(data, ROLES, layer_order=ORDER)
    assert len(data.ignored_geometry) == 0
    repaired = next(v for v in data.repaired_geometry if v['layer'] == '5/0')
    assert repaired['index'] == 45 and repaired['holes'] == 4
    area = unary_union([Polygon(a) for a in data.layers['5/0']]).area
    assert abs(area - 7001062.03804) < .01
    p = Parameters(target_nsigma=6, multi_bound_x_um=35, multi_bound_y_um=35,
                   bound_angle_deg=1, line_width_um=20, line_space_um=20, annulus_um=10,
                   per_die_enabled=True, die_bounds=[[35, 25, 1], [20, 30, 2], [35, 35, 1]],
                   die_pivots=[['top_left', 0, 0], ['bottom_right', 0, 0], ['custom', 200, -150]])
    p.validate()
    cache = GeometryCache(data)
    engine = PreviewEngine(cache, case, ROLES, {}, p, {}, case['connections'])
    model = engine.contact
    assert len(model.records) == 158 and not case['connections'].ownership_notes
    assert [int((case['ownership']['4/0'] == i).sum()) for i in range(3)] == [9, 40, 109]
    origins = rotation_centers(case, p)
    z = np.array([[35, -25, np.pi/180], [-20, 30, -2*np.pi/180], [35, 35, np.pi/180]])
    f = engine.frame(z)
    off_die = 0
    max_error = 0.
    for r in model.records:
        i = r['owner']
        points = np.asarray(r['poly'].exterior.coords)[:-1]
        v = points-origins[i]
        co, si = np.cos(z[i, 2]), np.sin(z[i, 2])
        # Independent explicit formula, not the production rigid_transform helper.
        expected = origins[i] + z[i, :2] + np.column_stack((v[:, 0]*co-v[:, 1]*si,
                                                                        v[:, 0]*si+v[:, 1]*co))
        actual = np.asarray(model.moved(r['poly'], i, z).exterior.coords)[:-1]
        max_error = max(max_error, float(abs(actual-expected).max()))
        assert np.allclose(actual, expected, atol=1e-8, rtol=0)
        assert np.allclose(f['layers'][r['key']]['polygons'][r['index']], expected, atol=1e-8, rtol=0)
        off_die += not case['dies'][i].covers(r['poly'])
    extremes = RepresentativeExtremes(case, p)
    hard = np.asarray(p.die_bounds, float); hard[:, 2] = np.deg2rad(hard[:, 2])
    for entry in extremes.items:
        assert np.all(abs(entry['states']) <= hard+1e-12)
    cfg = ViewSettings(sample_exponent=12, probability_show_ranges=True,
                       probability_ranges=((1, '#567c95'), (3, '#926f9d')))
    start = time.perf_counter()
    maps = probability_maps(cache, model.bound, ROLES, p, masks=model.interface_masks, settings=cfg)
    probability_ms = 1000*(time.perf_counter()-start)
    assert len(maps['maps']) == 158
    full = probability_maps(cache, model.bound, ROLES, replace(p, target_nsigma=0),
                            masks=model.interface_masks, settings=replace(cfg, probability_show_ranges=False))
    worst_area, worst_edge = 0., 0.
    for a, b in zip(maps['maps'], full['maps']):
        assert np.array_equal(a['probability'], b['probability'])
        owner = a['owner']; local = p.for_die(owner); poly = a['nominal_polygon']
        np.testing.assert_allclose(a['rotation_center'], origins[owner])
        worst_area = max(worst_area, abs(a['integrated_area_um2']/a['via_area_um2']-1))
        worst_edge = max(worst_edge, a['edge_probability'])
        x0, x1, y0, y1 = a['extent']; extent = box(x0, y0, x1, y1)
        previous = None
        for h in [1, 3, 6, 12, 0]:
            envelope = coverage_envelope(poly, origins[owner], local, 3, h)
            assert extent.covers(envelope)
            if previous is not None:
                assert envelope.buffer(data.dbu_um*.01).covers(previous)
            previous = envelope
        # Every combined physical XY/angle corner footprint remains inside
        # the plotted domain, even if its probability is below display resolution.
        for signs in product((-1, 1), repeat=3):
            state = np.zeros((3, 3)); state[owner] = hard[owner]*signs
            assert extent.covers(model.moved(poly, owner, state))
    assert worst_area < 1e-5 and worst_edge < 1e-10
    for i in range(3):
        np.testing.assert_array_equal(statistical_state(p.for_die(i), 3)[1],
                                      statistical_state(replace(p, target_nsigma=0).for_die(i), 3)[1])
    sm = space_maps(model, p)
    for field in sm['maps']:
        assert np.isfinite(field['spacing']).all()
        for r in model.records:
            if r['owner'] == field['owner']:
                assert field['domain'].buffer(.001).covers(r['poly'])
    pm = package_map(sm, case['board'], origins, z, {'4/0'})
    assert pm['active'] and np.isfinite(pm['spacing']).all()
    assert not package_map(sm, case['board'], origins, z, set())['active']
    selected = {}
    for item in maps['maps']:
        i = item['owner']; radius = np.linalg.norm(np.asarray(item['nominal_polygon'].centroid.coords[0])-origins[i])
        if i not in selected or radius > selected[i][0]:selected[i] = (radius, item)
    # Independent deterministic integration checks at one far-radius via per DIE.
    masks = {k: np.zeros_like(v) for k, v in model.interface_masks.items()}
    for _, item in selected.values():masks[item['key']][item['index']] = True
    quadrature = probability_maps(cache, model.bound, ROLES, p, masks=masks,
                                 settings=replace(cfg, probability_method='quadrature', quadrature_order=25))
    assert len(quadrature['maps']) == 3
    assert all(abs(a['integrated_area_um2']/a['via_area_um2']-1) < 1e-5 for a in quadrature['maps'])
    draw_probability(selected, p)
    engine.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return dict(status='PASS', sha256=before, source_unchanged=True, layer_roles=ROLES,
                layer_order=ORDER, repaired_5_0=repaired, union_area_5_0_um2=area,
                interfaces=158, per_die_interfaces=[9, 40, 109], off_die_interfaces=off_die,
                parameters=asdict(p), test_states=z.tolist(), rotation_centers_um=origins.tolist(),
                max_transform_error_um=max_error, extreme_states=len(extremes),
                probability_maps=158, probability_method='Sobol, 4096 samples, seed 510',
                probability_first_ms=probability_ms, probability_max_area_relative_error=worst_area,
                probability_max_edge=worst_edge, full_zero_cloud_identical=True,
                deterministic_integral_checks=3, space_maps=len(sm['maps']),
                all_8_corner_footprints_within_rasters=True,
                scope='Read-only attachment test; explicit synthetic test parameters, not factory calibration. '
                      'The finite raster depicts coverage density; conservative dashed envelopes cover all input states.')


def draw_probability(selected, p):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt, font_manager
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import PathPatch
    from die_shift_studio.plot_geometry import geometry_path
    font = next((x for x in ('Noto Sans CJK JP', 'Microsoft YaHei', 'DejaVu Sans')
                 if x in {f.name for f in font_manager.fontManager.ttflist}), 'sans-serif')
    plt.rcParams.update({'font.family': font, 'font.size': 9, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), layout='constrained')
    cmap = LinearSegmentedColormap.from_list('coverage', ['#ffffff', '#d8e7ec', '#81aabc', '#3c718a'])
    labels = ['左上角', '右下角', '自定义 (+200, −150) μm']
    for i, ax in enumerate(axes):
        radius, a = selected[i]
        im = ax.imshow(a['probability'], extent=a['extent'], origin='lower', cmap=cmap, vmin=0, vmax=1)
        for h, color in [(6, '#bd6479'), (3, '#9577aa'), (1, '#6c9785')]:
            g = coverage_envelope(a['nominal_polygon'], a['rotation_center'], p.for_die(i), 3, h)
            ax.add_patch(PathPatch(geometry_path(g), fill=False, edgecolor=color, ls='--', lw=1.2, label=str(h)+'σ包络'))
        ax.add_patch(PathPatch(geometry_path(a['nominal_polygon']), fill=False, edgecolor='#3f657b', lw=.6))
        ax.set(title=f'DIE {i+1} · {labels[i]}\n4/0 #{a["index"]+1} · 转动半径 {radius:.1f} μm', xlabel='X / μm', ylabel='Y / μm')
        ax.set_aspect('equal');ax.tick_params(labelsize=8)
    fig.colorbar(im, ax=axes, label='过孔实体覆盖概率', shrink=.75)
    axes[0].legend(loc='upper left', fontsize=8, frameon=False)
    for ext in ('png', 'svg'):fig.savefig(ROOT/'figures'/('V14_GD55_probability.'+ext), dpi=300, bbox_inches='tight')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--gds', type=Path, required=True)
    parser.add_argument('--numerical-only', action='store_true')
    a = parser.parse_args()
    result = numerical(a.gds)
    print('GD55_PIVOT_PROBABILITY_PASS', flush=True)
    if not a.numerical_only:
        q = dict(roles=ROLES, layer_order=ORDER, parameters=dict(line_width_um=20, line_space_um=20,
                 annulus_um=10, multi_bound_x_um=25, multi_bound_y_um=25, bound_angle_deg=1))
        result['adaptation'] = run(a.gds, q, np.tile([-25., 25., np.pi/180], (3, 1)), True, 'V14')
    (ROOT/'results/attachment_GD55_V14.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print('GD55_V14_ATTACHMENT_PASS', flush=True)

if __name__ == '__main__':main()
