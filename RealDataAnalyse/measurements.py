"""Import production tables of global measured DIE centers."""
from contextlib import contextmanager
import csv
from itertools import groupby, islice
import math
from pathlib import Path

from .storage import dumps, sha256

HEADINGS = ('行', '列', '是否成功', '理论坐标X', '理论坐标Y', '实际坐标X', '实际坐标Y',
            '偏移X', '偏移Y', '旋转角度')
SUFFIXES = ('.csv', '.xlsx')


def folders(root):
    return sorted((p for p in Path(root).iterdir() if p.is_dir()), key=lambda p:
                  (0, int(p.name), p.name) if p.name.isdecimal() else (1, p.name.casefold(), p.name))


def table_file(path):
    return path.is_file() and path.suffix.lower() in SUFFIXES and not path.name.startswith('~$')


def nominal_geometry(info):
    board = info.get('board_bounds_um')
    dies = info.get('dies') or []
    if not info.get('ready') or not board or not dies:
        raise ValueError('生产导入需要已冻结工程的封装边框和芯片中心')
    return dict(board_origin_um=board[:2], centers_um=[die['center_um'] for die in dies])


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
                raise ValueError(f'找不到工作表 {profile["sheet"]}')
            sheet.reset_dimensions()
            yield sheet.iter_rows(min_row=header, values_only=True)
        finally:
            book.close()
    else:
        import codecs
        # Decode incrementally before parsing so fallback does not yield half a file.
        with path.open('rb') as stream:
            decoder = codecs.getincrementaldecoder('utf-8-sig')()
            try:
                while block := stream.read(65536):
                    decoder.decode(block)
                decoder.decode(b'', final=True)
                encoding = 'utf-8-sig'
            except UnicodeDecodeError:
                encoding = 'gb18030'
        with path.open(encoding=encoding, newline='') as stream:
            yield islice(csv.reader(stream), header - 1, None)


def scalar(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} 须为数值') from None
    if isinstance(value, bool) or not math.isfinite(number) or abs(number) > 1e9:
        raise ValueError(f'{label} 超出有限坐标范围')
    return number


def import_part(path, die, profile, scratch, stop, progress):
    import sqlite3
    from contextlib import closing

    before = sha256(path, stop)
    try:
        with closing(sqlite3.connect(scratch)) as db, table_rows(path, profile) as rows:
            db.execute('create table poses(r integer,c integer,die integer,tx real,ty real,ax real,ay real,angle real,dx real,dy real,source text,line integer)')
            headings = [str(value).strip() for value in next(rows, ())]
            columns = []
            for label in HEADINGS:
                name = profile['columns'].get(label) or label
                if headings.count(name) != 1:
                    raise ValueError(f'须包含唯一的“{name}”列')
                columns.append(headings.index(name))
            count = 0
            for line, row in enumerate(rows, profile['header_row'] + 1):
                if stop.is_set():
                    raise InterruptedError('已停止测量文件导入')
                if not any(v is not None and str(v).strip() for v in row):
                    continue
                values = [row[i] if i < len(row) else None for i in columns]
                r, c, success = [scalar(value, f'第{line}行{name}') for value, name in zip(values[:3], HEADINGS)]
                if not r.is_integer() or not c.is_integer() or success not in (0, 1):
                    raise ValueError(f'第{line}行的行列号须为整数，是否成功须为0或1')
                tx, ty = [scalar(value, f'第{line}行{name}') for value, name in zip(values[3:5], HEADINGS[3:])]
                ax, ay, dx, dy, angle = ([scalar(value, f'第{line}行{name}') for value, name in zip(values[5:], HEADINGS[5:])]
                                         if success else [None]*5)
                if angle is not None and abs(angle) > 360:
                    raise ValueError(f'第{line}行的角度超出 ±360°')
                db.execute('insert into poses values(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (int(r), int(c), die, tx, ty, ax, ay, angle, dx, dy, str(path), line))
                count += 1
                if count % 1000 == 0:
                    progress(dict(text=f'读取测量 · {path.parent.name}/{path.name} · {count} 行', current=0, total=1))
            db.commit()
        if count == 0 or before != sha256(path, stop):
            raise ValueError('测量文件为空或读取期间发生修改')
        return dict(path=str(path), die=die, die_name=path.parent.name, rows=count, sha256=before, scratch=str(scratch))
    except (OSError, ValueError) as exc:
        raise ValueError(f'{path.parent.name}/{path.name}: {exc}') from exc


def infer_die_indices(db, centers, scale, tolerance, stop):
    """Match folder names to the physical GDS DIEs through nominal offsets."""
    count = len(centers)
    if count == 1:
        return {1: 1}
    cursor = db.execute('select r,c,die,tx,ty from production order by r,c,die,tx,ty')
    candidates = None
    try:
        for _, records in groupby(cursor, key=lambda entry: entry[:2]):
            if stop.is_set():
                raise InterruptedError('已停止芯片名称匹配')
            points = {}
            for _, _, die, tx, ty in records:
                points.setdefault(die, []).append((tx*scale, ty*scale))
            if len(points) != count:
                continue
            x, y = points[1][0]
            mappings = set()
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
            candidates = mappings if candidates is None else candidates.intersection(mappings)
            if len(candidates) == 1:
                (order,) = candidates
                return dict(enumerate(order, 1))
            if not candidates:
                break
    finally:
        cursor.close()
    raise ValueError('理论中心无法唯一确定芯片名称与工程DIE的对应，请核对坐标单位，或在工程配置指定 die_order')


def package_rows(db, geometry, scale, tolerance, stop, progress):
    """Group each repeated machine row/column by its nominal package origin."""
    local = [[value - origin for value, origin in zip(center, geometry['board_origin_um'])]
             for center in geometry['centers_um']]
    cursor = db.execute('select * from production order by r,c,die,tx,ty')
    skipped = 0
    index = 0
    total = db.execute('select count(*) from production').fetchone()[0]
    checked = 0
    try:
        for (row, col), records in groupby(cursor, key=lambda entry: entry[:2]):
            anchors = []
            for _, _, die, tx, ty, ax, ay, angle, dx, dy, source, line in records:
                checked += 1
                if checked % 1000 == 0:
                    progress(dict(text=f'匹配封装 · 已核对 {checked}/{total} 行 · {index} 有效封装 · {skipped} 跳过',
                                  current=checked, total=total))
                if stop.is_set():
                    raise InterruptedError('已停止封装坐标匹配')
                cx, cy = local[die - 1]
                origin = (tx * scale - cx, ty * scale - cy)
                near = [group for group in anchors if math.dist(group[0], origin) <= tolerance]
                if die == 1 and not near:
                    anchors.append((origin, {1: (origin, ax, ay, angle, dx, dy, source, line)}))
                    continue
                if len(near) != 1 or die in near[0][1]:
                    raise ValueError(f'{Path(source).name} 第{line}行，行列({row}, {col})的理论位置无法唯一匹配；请核对单位、DIE顺序或重复数据')
                near[0][1][die] = (origin, ax, ay, angle, dx, dy, source, line)
            for part, (_, group) in enumerate(anchors, 1):
                if len(group) != len(local):
                    raise ValueError(f'行列({row}, {col})候选{part}缺少DIE记录')
                if any(measurement[1] is None for measurement in group.values()):
                    skipped += 1
                    continue
                index += 1
                position = [math.fsum(record[0][axis] for record in group.values())/len(local) for axis in range(2)]
                package = f'{row}:{col}:{part}'
                cell = f'Package_{index:09d}'
                poses = []
                for die, (_, ax, ay, angle, dx, dy, source, line) in group.items():
                    cx, cy = local[die-1]
                    pose = dict(die=die, dx_um=ax*scale-cx-position[0], dy_um=ay*scale-cy-position[1],
                                angle_deg=angle, rotation_center_um=geometry['centers_um'][die-1])
                    poses.append(pose)
                    audit = dict(pose, actual_center_um=[ax*scale, ay*scale], reported_offset=[dx, dy])
                    db.execute('insert into measurements values(?,?,?,?,?,?,?)',
                               (package, die, *position, dumps(audit), source, line))
                payload = dict(cell=cell, position_um=position, dies=poses)
                db.execute('insert into packages(seq,package,cell,payload) values(?,?,?,?)',
                           (index, package, cell, dumps(payload)))
    finally:
        cursor.close()
    return index, skipped
