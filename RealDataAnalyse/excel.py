"""Replaceable Excel adapter: one workbook per DIE, join by package ID."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import tempfile

from .storage import Ledger, dumps, sha256

HEADERS = ['package_id', 'die', 'dx_um', 'dy_um', 'angle_deg', 'package_x_um', 'package_y_um', 'center_x_um', 'center_y_um']
DEFAULT_PROFILE = {'sheet': 'Measurements', 'header_row': 1, 'columns': {name: name for name in HEADERS}}


def numeric(value, name, limit=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
        raise ValueError(f'{name} 必须为有限数值，范围 ±{limit:g}；空白不当作0')
    return float(value)


def _read_file(path, profile, scratch, stop, progress):
    from openpyxl import load_workbook
    if path.suffix.lower() != '.xlsx':
        raise ValueError(f'{path.name}: 当前适配器支持 .xlsx，请先将旧 .xls 另存为 .xlsx')
    progress(dict(text=f'Excel校验 · {path.name} · 计算输入摘要', current=0, total=1))
    before = sha256(path, stop)
    book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    db = sqlite3.connect(scratch)
    db.execute('create table rows(package text primary key, die integer, x real, y real, payload text, source text, row_number integer)')
    count = 0
    file_die = None
    try:
        sheet = book[profile['sheet']]
        sheet.reset_dimensions()  # don't trust stale worksheet dimension metadata
        rows = sheet.iter_rows(min_row=profile['header_row'], values_only=True)
        headings = next(rows, ())
        names = [str(x).strip() if x is not None else '' for x in headings]
        mapping = profile['columns']
        required = HEADERS[:7]
        positions = {}
        for key in HEADERS:
            name = mapping.get(key, key)
            if not name and key not in required:
                continue
            if names.count(name) > 1:
                raise ValueError(f'重复列名 {name}')
            if name in names:
                positions[key] = names.index(name)
            elif key in required:
                raise ValueError(f'缺少列 {name}；可修改列映射配置')
        if ('center_x_um' in positions) != ('center_y_um' in positions):
            raise ValueError('旋转中心X/Y必须成对提供')
        for row_number, row in enumerate(rows, profile['header_row'] + 1):
            if stop.is_set():
                raise InterruptedError('已停止Excel导入')
            if not any(v is not None for v in row):
                continue
            values = {key: row[i] if i < len(row) else None for key, i in positions.items()}
            if any(isinstance(v, str) and v.startswith('=') for v in values.values()):
                raise ValueError(f'第{row_number}行包含公式；请粘贴为明确的实测值')
            package = values['package_id']
            if not isinstance(package, str) or not package.strip() or len(package) > 240:
                raise ValueError(f'第{row_number}行 package_id 必须是非空文本（保留前导零）')
            package = package.strip()
            die = numeric(values['die'], 'die', 10000)
            if die < 1 or not die.is_integer():
                raise ValueError('die须为1起的整数')
            die = int(die)
            if file_die is not None and die != file_die:
                raise ValueError('一个Excel只能包含一颗DIE的数据')
            file_die = die
            pose = dict(die=die, dx_um=numeric(values['dx_um'], 'dx_um'), dy_um=numeric(values['dy_um'], 'dy_um'),
                        angle_deg=numeric(values['angle_deg'], 'angle_deg', 360))
            cx, cy = values.get('center_x_um'), values.get('center_y_um')
            if cx is not None or cy is not None:
                pose['rotation_center_um'] = [numeric(cx, 'center_x_um'), numeric(cy, 'center_y_um')]
            try:
                db.execute('insert into rows values(?,?,?,?,?,?,?)', (package, die, numeric(values['package_x_um'], 'package_x_um'),
                    numeric(values['package_y_um'], 'package_y_um'), dumps(pose), str(path), row_number))
            except sqlite3.IntegrityError:
                raise ValueError(f'第{row_number}行重复封装ID {package}') from None
            count += 1
            if count % 500 == 0:
                db.commit()
                progress(dict(text=f'Excel读取 · {path.name} · 已校验{count}行', current=0, total=1))
        db.commit()
        if not count:
            raise ValueError('没有实测数据')
        if before != sha256(path, stop):
            raise ValueError('读取过程中Excel已被修改，请关闭编辑后重新导入')
        return dict(path=str(path), sha256=before, die=file_die, rows=count, scratch=str(scratch))
    except Exception as exc:
        raise ValueError(f'{path.name}: {exc}') from exc
    finally:
        book.close()
        db.close()


def import_files(paths, database, die_count, workers, stop, progress=lambda _: None, profile=None, profiles=None):
    """Build a new journal atomically; errors never create a runnable partial run."""
    profile = profile or DEFAULT_PROFILE
    paths = [Path(p).resolve() for p in paths]
    profiles = {str(Path(path).resolve()): value for path, value in (profiles or {}).items()}
    if set(profiles) - {str(p) for p in paths}:
        raise ValueError('独立列映射包含未选择的Excel')
    if not isinstance(profile, dict):
        raise ValueError('共用列映射须为对象')
    for current in [profiles.get(str(path), profile) for path in paths]:
        if not isinstance(current, dict) or type(current.get('header_row')) is not int or current['header_row'] < 1:
            raise ValueError('表头行号须为正整数')
        if not isinstance(current.get('sheet'), str) or not current['sheet'].strip() or not isinstance(current.get('columns'), dict):
            raise ValueError('请填写工作表与列映射')
        if any(not isinstance(value, str) for value in current['columns'].values()):
            raise ValueError('Excel列名须为文本')
    if len(set(paths)) != len(paths) or len(paths) != die_count or not 1 <= die_count <= 10000:
        raise ValueError('应选择每颗DIE各一个Excel；文件不得重复，数量须与工程一致')
    database = Path(database).resolve()
    if database.exists():
        raise ValueError('新任务数据库已存在，请选择新名称或使用继续任务')
    database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rda-import-', dir=database.parent) as temp:
        stage = Path(temp) / 'run.sqlite'
        ledger = Ledger(stage)
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, len(paths), 16)), thread_name_prefix='excel') as pool:
                futures = [pool.submit(_read_file, p, profiles.get(str(p), profile), Path(temp) / f'{i}.sqlite', stop, progress) for i, p in enumerate(paths)]
                sources = []
                for future in as_completed(futures):
                    info = future.result()
                    with closing(sqlite3.connect(info['scratch'])) as part:
                        try:
                            with ledger.db:
                                ledger.db.executemany('insert into measurements values(?,?,?,?,?,?,?)', part.execute('select * from rows'))
                        except sqlite3.IntegrityError:
                            raise ValueError('多个Excel重复同一DIE/封装记录') from None
                    sources.append({k: v for k, v in info.items() if k != 'scratch'})
                    progress(dict(text=f'Excel汇入 · {len(sources)}/{len(paths)}文件 · {info["path"]} · {info["rows"]}行', current=len(sources), total=len(paths)))
            if sorted(v['die'] for v in sources) != list(range(1, die_count + 1)):
                raise ValueError('DIE编号必须完整覆盖1至工程DIE数量，每个文件一颗')
            missing = ledger.db.execute('select package,count(*) from measurements group by package having count(*) != ? limit 20', (die_count,)).fetchall()
            if missing:
                raise ValueError(f'封装缺少部分DIE记录，拒绝遗漏：{missing}')
            conflict = ledger.db.execute('select package from measurements group by package having max(x)-min(x)>0.000001 or max(y)-min(y)>0.000001 limit 20').fetchall()
            if conflict:
                raise ValueError(f'同封装摆放坐标冲突：{conflict}')
            # Disk ordered join: only one package's DIE rows in memory.
            progress(dict(text='匹配封装 · 检查缺测、重复与摆放坐标', current=0, total=1))
            package_total = ledger.db.execute('select count(distinct package) from measurements').fetchone()[0]
            current = None
            poses = []
            seq = 0
            def save():
                nonlocal seq
                if current is None:
                    return
                seq += 1
                if seq == 1 or seq % 500 == 0 or seq == package_total:
                    progress(dict(text=f'封装匹配 · {seq}/{package_total} · {die_count} DIE/封装', current=seq, total=package_total))
                payload = dict(cell=f'Package_{seq:09d}', position_um=position, dies=poses)
                ledger.db.execute('insert into packages(seq,package,cell,payload) values(?,?,?,?)', (seq, current, payload['cell'], dumps(payload)))
            for package, x, y, pose in ledger.db.execute('select package,x,y,payload from measurements order by package,die'):
                if stop.is_set():
                    raise InterruptedError('已停止封装匹配')
                if package != current:
                    save()
                    current, position, poses = package, [x, y], []
                poses.append(json.loads(pose))
            save()
            ledger.db.commit()
            ledger.set('sources', sorted(sources, key=lambda x: x['die']))
            ledger.set('profile', profile)
            ledger.set('profiles', profiles)
            ledger.set('die_count', die_count)
            ledger.set('count', seq)
            ledger.set('state', 'imported')
            ledger.event(f'导入完成：{die_count} DIE，{seq} 封装；所有封装记录完整匹配')
            ledger.db.execute('pragma wal_checkpoint(TRUNCATE)')
        finally:
            ledger.close()
        # Exclusive file creation prevents two imports overwriting one another.
        if stop.is_set():
            raise InterruptedError('已停止导入；未发布任务数据库')
        __import__('os').link(stage, database)
    progress(dict(text=f'导入完成 · {len(paths)}文件 · {seq}封装 · {die_count} DIE', current=seq, total=seq))
    return dict(packages=seq, dies=die_count, database=str(database))


def generate_example(folder, count=24, die_count=3, pitch=(12000., 12000.)):
    """Deterministic synthetic measurements; no claim of production data."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if not 1 <= count <= 1_000_000 or not 1 <= die_count <= 100:
        raise ValueError('示例数量超出限制')
    outputs = []
    for die in range(1, die_count + 1):
        path = folder / f'GD55_SYNTHETIC_DIE{die}.xlsx'
        if path.exists():
            raise FileExistsError(f'示例文件已存在，不覆盖：{path}')
        book = Workbook()
        sheet = book.active
        sheet.title = 'Measurements'
        sheet.append(HEADERS)
        sheet.freeze_panes = 'C2'
        for cell in sheet[1]:
            cell.font = Font(name='Calibri', color='FFFFFF', bold=True)
            cell.fill = PatternFill('solid', fgColor='24476A')
            cell.alignment = Alignment(wrap_text=True)
        sheet.row_dimensions[1].height = 32
        for i in range(count):
            # First package is nominal. Remaining offsets are within ±1 µm/±.01°.
            k = 0 if i == 0 else ((i * (die + 2)) % 11 - 5) / 5
            sheet.append([f'GD55_P{i + 1:06d}', die, k, -k / 2, k / 100, (i % 6) * pitch[0], (i // 6) * pitch[1], None, None])
        for column in 'ABCDEFGHI':
            sheet.column_dimensions[column].width = 22 if column != 'A' else 26
        for row in sheet.iter_rows(min_row=2, min_col=3):
            for cell in row:
                cell.number_format = '0.000000'
        sheet.auto_filter.ref = f'A1:I{count + 1}'
        book.properties.description = 'GD55工作流的合成示例；不是实测或生产验收。坐标μm，角度degree，中心留空沿用工程。'
        book.save(path)
        book.close()
        outputs.append(str(path))
    return outputs
