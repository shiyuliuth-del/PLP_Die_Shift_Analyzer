"""Generate the public SH22 synthetic production fixture, never copy plant data."""
import argparse
import csv
import json
from pathlib import Path
import random
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from RealDataAnalyse.measurements import HEADINGS
from RealDataAnalyse.projects import SH22_INPUTS, SETTINGS


def generate(attachment, target):
    from die_shift_studio.core import read_gds, build_case
    from die_shift_studio.gds_merge import restore_project_geometry
    from die_shift_studio.project import normalize_project

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(attachment) as archive:
        drawing = target / 'sh22.gds'
        drawing.write_bytes(archive.read('SH22/sh22.gds'))
        project = json.loads(archive.read('SH22/SH22.dshift.json'))
    project.pop('saved_results', None)
    project['measured_output'] = dict(output_directory='', max_memory_mb=0, max_threads=0)
    project['parameters']['minimum_area_um2'] = 1.0
    project['settings']['drc_tolerance_um'] = 0.5
    (target / 'SH22.dshift.json').write_text(json.dumps(project, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    data, config = restore_project_geometry(read_gds(drawing, project['top_cell']), normalize_project(project))
    case = build_case(data, config['roles'], layer_order=config['layer_order'])
    x0, y0 = case['board'].bounds[:2]
    local = [(die.centroid.x - x0, die.centroid.y - y0) for die in case['dies']]
    area = target / 'RealDataAnalyse'
    rng = random.Random(58000400)
    stress = { (0, 0, 1): (85, -50, 1.4), (1, 4, 3): (-100, 65, -2.2),
               (2, 7, 6): (130, 45, 2.7), (3, 9, 9): (-180, 60, -3.2) }
    for die, (cx, cy) in enumerate(local, 101):
        folder = area / 'Excel' / str(die)
        folder.mkdir(parents=True, exist_ok=True)
        for part in (1, 2):
            with (folder / f'Measurements_{part}.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerow(HEADINGS)
                for quadrant in range(2*(part-1), 2*part):
                    for row in range(10):
                        for col in range(10):
                            x = (-43000 if quadrant % 2 else 5500) + 4050*col
                            y = (42500 if quadrant < 2 else -8500) - 4050*row
                            tx, ty = x+cx, y+cy
                            severity = rng.random()
                            if severity < .02:
                                dx, dy, angle = (rng.uniform(-28, 28), rng.uniform(-28, 28), rng.uniform(-1.3, 1.3))
                            elif severity < .22:
                                dx, dy, angle = (rng.uniform(-12, 12), rng.uniform(-12, 12), rng.uniform(-.55, .55))
                            else:
                                dx, dy, angle = (rng.gauss(0, 2.4), rng.gauss(0, 2.4), rng.gauss(0, .09))
                            if die == 101 and (quadrant, row, col) in stress:
                                dx, dy, angle = stress[quadrant, row, col]
                            if (quadrant, row, col) == (0, 0, 0):
                                dx = dy = angle = 0
                            row_values = [tx/1000, ty/1000, (tx+dx)/1000, (ty+dy)/1000, dx/1000, dy/1000, angle]
                            writer.writerow([row, col, 1, *(f'{value:.8f}' for value in row_values)])
    manifest = dict(schema=1, measurement=dict(scale_um=1000),
                    controls=dict(workers=0, memory=0, retries=3, timeout=0))
    (area / SETTINGS).write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    assert all((target / name).is_file() for name in SH22_INPUTS)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attachment', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    generate(args.attachment, args.target)
