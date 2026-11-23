"""Import production tables of global measured DIE centers."""
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
import csv
from decimal import Decimal
from itertools import groupby, islice
import math
import sqlite3
from pathlib import Path
from zipfile import BadZipFile

from .storage import dumps, sha256

HEADINGS = ('行', '列', '是否成功', '理论坐标X', '理论坐标Y', '实际坐标X', '实际坐标Y',
            '偏移X', '偏移Y', '旋转角度')
SUFFIXES = ('.csv', '.xlsx')
POSE_COLUMNS = ('r integer,c integer,die integer,tx real,ty real,ax real,ay real,angle real,'
                'dx real,dy real,failure_override integer,source text,line integer')


def folders(root):
    return sorted((p for p in Path(root).iterdir() if p.is_dir()), key=lambda p: (p.name.casefold(), p.name))


def table_file(path):
    return path.is_file() and path.suffix.lower() in SUFFIXES and not path.name.startswith('~$')


def measurement_root(excel):
    """Find the DIE directory level, including an archive's enclosing folder."""
    root = Path(excel)
    while True:
        children = [path for path in folders(root) if not path.is_symlink()]
        if any(any(map(table_file, child.iterdir())) for child in children):
            return root
        if any(table_file(path) for path in root.iterdir()):
            return None
        if len(children) != 1:
            break
        root = children[0]
    # An empty set of named DIE folders still lets the user add tables by DIE.
    return Path(excel) if folders(excel) else None


def nominal_geometry(info):
    board = info.get('board_bounds_um')
    dies = info.get('dies') or []
    if not info.get('ready') or not board or not dies:
        raise ValueError('生产导入需要已冻结工程的封装边框和芯片中心')
    return dict(board_origin_um=board[:2], centers_um=[die['center_um'] for die in dies])


class MeasurementFormatError(ValueError):
    pass


@contextmanager
def table_rows(path, profile):
    header = profile['header_row']
    if path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            # The production machine may export a single worksheet under a vendor name.
            sheet = book[profile['sheet']] if profile['sheet'] in book else book.worksheets[0] if len(book.worksheets) == 1 else None
            if sheet is None:
                raise MeasurementFormatError(f'找不到工作表 {profile["sheet"]}')
            sheet.reset_dimensions()
            yield sheet.iter_rows(min_row=header, values_only=True)
        finally:
            book.close()
    else:
        # The heading identifies the encoding. A damaged byte in a later
        # measurement must not change the decoding of every other row.
        with path.open('rb') as stream:
            heading = next(islice(stream, header - 1, None), b'')
        try:
            heading.decode('utf-8-sig')
            encoding = 'utf-8-sig'
        except UnicodeDecodeError:
            encoding = 'gb18030'
        with path.open(encoding=encoding, errors='replace', newline='') as stream:
            def machine_rows():
                # Machine exports have one physical line per observation.
                # An unclosed quote must not consume the remaining batch.
                for line in islice(stream, header - 1, None):
                    try:
                        yield next(csv.reader([line], strict=True))
                    except csv.Error:
                        yield None
            yield machine_rows()


def scalar(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} 须为数值') from None
    if isinstance(value, bool) or not math.isfinite(number) or abs(number) > 1e9:
        raise ValueError(f'{label} 超出有限坐标范围')
    return number


def import_part(path, die, profile, scratch, stop, progress, ignore_measurement_failure=False):
    before = sha256(path, stop)
    with closing(sqlite3.connect(scratch)) as db:
        db.execute(f'create table poses({POSE_COLUMNS})')
        db.execute('create table warnings(line integer, message text)')

        def warn(line, message):
            db.execute('insert into warnings values (?,?)', (line, message))

        count = repaired = rejected = 0
        ignored_file = False
        try:
            with table_rows(path, profile) as rows:
                headings = [str(value).strip() for value in (next(rows, ()) or ())]
                columns = []
                for label in HEADINGS:
                    name = profile['columns'].get(label) or label
                    if name not in headings and label in ('偏移X', '偏移Y'):
                        warn(profile['header_row'], f'缺少“{name}”列，将从实际与理论中心计算偏移')
                        columns.append(None)
                    elif headings.count(name) != 1:
                        raise MeasurementFormatError(f'须包含唯一的“{name}”列')
                    else:
                        columns.append(headings.index(name))
                for line, row in enumerate(rows, profile['header_row'] + 1):
                    if stop.is_set():
                        raise InterruptedError('已停止测量文件导入')
                    if row is None:
                        warn(line, '已跳过行：CSV引号或字段结构损坏')
                        rejected += 1
                        continue
                    if not any(value is not None and str(value).strip() for value in row):
                        continue
                    values = [row[i] if i is not None and i < len(row) else None for i in columns]
                    try:
                        r, c, tx, ty = [scalar(values[i], HEADINGS[i]) for i in (0, 1, 3, 4)]
                        if not r.is_integer() or not c.is_integer():
                            raise ValueError('行列号须为整数')
                    except ValueError as exc:
                        warn(line, f'已跳过行：{exc}')
                        rejected += 1
                        continue

                    ax = ay = dx = dy = angle = None
                    failure_override = False
                    try:
                        success = scalar(values[2], '是否成功')
                        if success not in (0, 1):
                            raise ValueError('是否成功须为0或1')
                        if success:
                            ax, ay, angle = [scalar(values[i], HEADINGS[i]) for i in (5, 6, 9)]
                        elif ignore_measurement_failure:
                            dx, dy, angle = [scalar(values[i], HEADINGS[i]) for i in (7, 8, 9)]
                            ax, ay = [scalar(Decimal(str(center)) + Decimal(str(offset)), label)
                                      for center, offset, label in ((tx, dx, HEADINGS[5]), (ty, dy, HEADINGS[6]))]
                            failure_override = True
                        if angle is not None and abs(angle) > 360:
                            raise ValueError('角度超出 ±360°')
                    except ValueError as exc:
                        warn(line, f'已跳过本行的实测姿态：{exc}')
                        ax = ay = angle = None
                        failure_override = False
                        rejected += 1

                    if angle is not None and not failure_override:
                        offsets = []
                        fallback = []
                        for i, actual, nominal in ((7, ax, tx), (8, ay, ty)):
                            try:
                                offsets.append(scalar(values[i], HEADINGS[i]))
                            except ValueError:
                                offsets.append(actual - nominal)
                                if columns[i] is not None:
                                    fallback.append(HEADINGS[i])
                        dx, dy = offsets
                        if fallback:
                            warn(line, '使用实际中心减理论中心恢复' + '、'.join(fallback))
                            repaired += 1

                    db.execute('insert into poses values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (int(r), int(c), die, tx, ty, ax, ay, angle, dx, dy, failure_override, str(path), line))
                    count += 1
                    if count % 1000 == 0:
                        progress(dict(text=f'读取测量 · {path.parent.name}/{path.name} · {count} 行', current=0, total=1))
        except (MeasurementFormatError, csv.Error, UnicodeError, BadZipFile) as exc:
            db.execute('delete from poses')
            db.execute('delete from warnings')
            warn(None, f'已跳过文件：{exc}')
            ignored_file = True
            count = repaired = rejected = 0

        if not count and not ignored_file:
            warn(None, '文件无可用于匹配的测量记录')
        if before != sha256(path, stop):
            raise ValueError(f'{path.parent.name}/{path.name} 在读取期间发生修改，请关闭编辑后重新创建任务')
        db.commit()
    return dict(path=str(path), die=die, die_name=path.parent.name, rows=count, sha256=before,
                scratch=str(scratch), repaired_rows=repaired, rejected_rows=rejected, ignored_file=ignored_file)


def grid_rows(db):
    return db.execute('select * from production order by r,c,die,tx,ty,source,line')


def infer_die_indices(db, centers, scale, tolerance, stop):
    """Identify physical DIE positions from repeated nominal patterns."""
    count = len(centers)
    if count == 1:
        return {1: 1}

    votes = Counter()
    with closing(grid_rows(db)) as cursor:
        for _, records in groupby(cursor, key=lambda entry: entry[:2]):
            if stop.is_set():
                raise InterruptedError('已停止芯片位置匹配')
            points = defaultdict(set)
            for _, _, die, tx, ty, *_ in records:
                points[die].add((tx * scale, ty * scale))
            if len(points) != count:
                continue
            # Pick a reference by physical coordinates. Labels only track
            # membership of a measurement file in its folder.
            reference = min(points, key=lambda die: tuple(sorted(points[die])))
            mappings = set()
            for x, y in sorted(points[reference]):
                for cx, cy in centers:
                    expected = [(x-cx+other_x, y-cy+other_y) for other_x, other_y in centers]
                    by_folder = []
                    for die in range(1, count+1):
                        matches = [i for i, center in enumerate(expected, 1)
                                   if any(math.dist(center, point) <= tolerance for point in points[die])]
                        if len(matches) != 1:
                            break
                        by_folder.append(matches[0])
                    if len(set(by_folder)) == count:
                        mappings.add(tuple(by_folder))
            if len(mappings) == 1:
                (mapping,) = mappings
                votes[mapping] += 1
            if votes.total() >= 32:
                break
    if votes:
        (winner, support), = votes.most_common(1)
        if support * 2 > votes.total():
            return dict(enumerate(winner, 1))
    raise ValueError('理论中心无法可靠区分各芯片目录对应的图纸位置。请核对机台坐标单位和封装图纸')


def site_components(by_position, tolerance):
    """Walk nearby theoretical package origins within one machine grid index."""
    buckets = defaultdict(list)
    for x, y in by_position:
        buckets[math.floor(x / tolerance), math.floor(y / tolerance)].append((x, y))
    unvisited = set(by_position)
    for point in sorted(by_position):
        if point not in unvisited:
            continue
        unvisited.remove(point)
        component = [point]
        for x, y in component:
            gx, gy = math.floor(x / tolerance), math.floor(y / tolerance)
            for i in range(gx-1, gx+2):
                for j in range(gy-1, gy+2):
                    for neighbor in buckets.get((i, j), ()):
                        if neighbor in unvisited and math.dist((x, y), neighbor) <= tolerance:
                            unvisited.remove(neighbor)
                            component.append(neighbor)
        yield [record for xy in component for record in by_position[xy]]


def select_site(records, names, tolerance, warn):
    by_die = defaultdict(list)
    for record in records:
        by_die[record[0]].append(record)
    if len(by_die) != len(names):
        missing = '、'.join(name for i, name in enumerate(names, 1) if i not in by_die)
        warn(f'已跳过封装：缺少芯片 {missing} 的匹配记录')
        return None

    unusable = [die for die, samples in sorted(by_die.items()) if all(sample[4] is None for sample in samples)]
    if unusable:
        warn('已跳过封装：没有有效实测姿态的芯片 ' + '、'.join(names[die-1] for die in unusable))
        return None

    selected = []
    for die, samples in sorted(by_die.items()):
        usable = [record for record in samples if record[4] is not None]
        # A valid successful observation takes precedence over a failed repeat.
        usable = [record for record in usable if not record[-3]] or usable
        if len({(p[2], p[3], p[4]) for p in usable}) != 1:
            examples = '、'.join(f'{Path(record[-2]).name} 第{record[-1]}行' for record in usable[:4])
            warn(f'已跳过封装：芯片 {names[die-1]} 有冲突的实测记录（{examples}）')
            return None
        if len(samples) > 1:
            warn(f'芯片 {names[die-1]} 合并了 {len(samples)-1} 条重复或失败记录')
        selected.append(min(usable, key=lambda record: record[1]))

    origins = [row[1] for row in selected]
    if any(math.dist(a, b) > tolerance for i, a in enumerate(origins) for b in origins[:i]):
        warn('已跳过封装：理论位置匹配存在歧义')
        return None
    return selected


def package_rows(db, geometry, scale, tolerance, stop, progress, names, warn):
    """Create complete packages and isolate individual unusable observations."""
    local = [[value - origin for value, origin in zip(center, geometry['board_origin_um'])]
             for center in geometry['centers_um']]
    skipped = index = checked = 0
    units = Decimal(str(scale))
    total = db.execute('select count(*) from production').fetchone()[0]
    with closing(grid_rows(db)) as cursor:
        for (row, col), records in groupby(cursor, key=lambda entry: entry[:2]):
            by_position = defaultdict(list)
            for _, _, die, tx, ty, ax, ay, angle, dx, dy, failure_override, source, line in records:
                if stop.is_set():
                    raise InterruptedError('已停止封装坐标匹配')
                checked += 1
                if checked % 1000 == 0:
                    progress(dict(text=f'匹配封装 · 已核对 {checked}/{total} 行 · {index} 有效封装 · {skipped} 跳过',
                                  current=checked, total=total))
                cx, cy = local[die - 1]
                origin = (tx * scale - cx, ty * scale - cy)
                if max(map(abs, origin)) > 1e9:
                    warn(f'{Path(source).parent.name}/{Path(source).name} 第{line}行：理论坐标超出范围，已跳过')
                    continue
                by_position[origin].append((die, origin, ax, ay, angle, dx, dy, tx, ty, failure_override, source, line))

            for part, site in enumerate(site_components(by_position, tolerance), 1):
                package = f'{row}:{col}:{part}'
                source, line = site[0][-2:]
                location = Path(source)
                def site_warning(message):
                    warn(f'封装 {package} · {location.parent.name}/{location.name} 第{line}行：{message}')
                matching = select_site(site, names, tolerance, site_warning)
                if not matching:
                    skipped += 1
                    continue
                position = [math.fsum(record[1][axis] for record in matching)/len(local) for axis in range(2)]
                poses = []
                audits = []
                measured_max = [0., 0., 0.]
                for die, _, ax, ay, angle, dx, dy, tx, ty, failure_override, source, line in matching:
                    cx, cy = local[die-1]
                    pose = dict(die=die, dx_um=ax*scale-cx-position[0], dy_um=ay*scale-cy-position[1],
                                angle_deg=angle, rotation_center_um=geometry['centers_um'][die-1])
                    if max(abs(pose['dx_um']), abs(pose['dy_um'])) > 1e9:
                        break
                    poses.append(pose)
                    deltas = (Decimal(str(dx)), Decimal(str(dy))) if failure_override else (
                        Decimal(str(ax))-Decimal(str(tx)), Decimal(str(ay))-Decimal(str(ty)))
                    offsets = [float(delta*units) for delta in deltas]
                    measured_max = [max(old, abs(value)) for old, value in zip(measured_max, (*offsets, angle))]
                    audit = dict(pose, actual_center_um=[ax*scale, ay*scale], reported_offset=[dx, dy])
                    if failure_override:
                        audit['measurement_failure_ignored'] = True
                    audits.append((package, die, *position, dumps(audit), source, line))
                if len(poses) != len(local):
                    skipped += 1
                    site_warning('已跳过封装：实测偏移超出范围')
                    continue
                index += 1
                cell = f'Package_{index:09d}'
                db.executemany('insert into measurements values(?,?,?,?,?,?,?)', audits)
                for record in matching:
                    if record[-3]:
                        source, line = record[-2:]
                        path = Path(source)
                        warn(f'封装 {package} · {path.parent.name}/{path.name} 第{line}行：忽略测量失败，已采用本行偏移和旋转角')
                payload = dict(cell=cell, position_um=position, dies=poses, measured_max=measured_max)
                db.execute('insert into packages(seq,package,cell,payload) values(?,?,?,?)',
                           (index, package, cell, dumps(payload)))
    return index, skipped
