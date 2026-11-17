"""Portable client settings; every project-owned path stays below its folder."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile

from die_shift_studio.jobs import atomic_save
from .excel import DEFAULT_PROFILE
from .storage import sha256

SETTINGS = 'RealDataAnalyse.project.json'


def inside(folder, value):
    root = Path(folder).resolve()
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'文件必须位于所选项目文件夹内：{value}')
    return path


def load_folder(folder):
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('请选择已有项目文件夹')
    manifest = inside(root, SETTINGS)
    saved = json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else {}
    if not isinstance(saved, dict) or saved.get('schema', 1) != 1:
        raise ValueError('不支持的客户端项目配置')
    if saved.get('project'):
        project = inside(root, saved['project'])
    else:
        candidates = []
        for path in sorted(root.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf8'))
                if isinstance(data, dict) and 'gds_path' in data and 'parameters' in data:
                    candidates.append(path)
            except (OSError, ValueError):
                continue
        if len(candidates) != 1:
            raise ValueError('项目文件夹须包含唯一的 Die Shift Analyze 工程JSON，或在客户端项目配置中明确指定')
        project = candidates[0]
    project = inside(root, project)
    data = json.loads(project.read_text(encoding='utf8'))
    source = inside(root, data['gds_path'])
    if not source.is_file():
        raise ValueError(f'项目图纸不存在：{source.name}')
    # Existing cache is validated by DSA; only settings and source are needed here.
    files = saved.get('files')
    if files is None:
        files = [dict(path=p.relative_to(root).as_posix(), enabled=True) for p in sorted(root.rglob('*'))
                 if p.is_file() and p.suffix.lower() == '.xlsx' and not p.name.startswith('~$') and 'Cache' not in p.relative_to(root).parts]
    normalized = []
    for item in files:
        path = inside(root, item['path'])
        entry = dict(path=str(path), enabled=bool(item.get('enabled', True)))
        if 'profile' in item:
            entry['profile'] = copy.deepcopy(item['profile'])
        normalized.append(entry)
    return dict(folder=str(root), project=str(project), files=normalized,
                database=str(inside(root, saved.get('database', 'Tasks/measurements.sqlite'))),
                output=str(inside(root, saved.get('output', 'Output/' + root.name + '.oas'))),
                profile=copy.deepcopy(saved.get('profile', DEFAULT_PROFILE)), controls=saved.get('controls', {}), shared_mapping=bool(saved.get('shared_mapping', True)))


def save_folder(folder, project, files, database, output, profile, controls, shared_mapping=True):
    root = Path(folder).resolve()
    def relative(path):
        return inside(root, path).relative_to(root).as_posix()
    payload = dict(schema=1, project=relative(project), files=[dict(path=relative(p['path']), enabled=p['enabled'], **({'profile': p['profile']} if 'profile' in p else {})) for p in files],
                   database=relative(database), output=relative(output), profile=profile, controls=controls, shared_mapping=shared_mapping)
    atomic_save(inside(root, SETTINGS), payload)


def copy_excel(folder, paths, stop):
    """Copy external inputs without deleting originals or replacing different data."""
    root = Path(folder).resolve()
    result = []
    for raw in paths:
        if stop.is_set():
            raise InterruptedError('已停止添加Excel')
        source = Path(raw).resolve()
        if not source.is_file() or source.suffix.lower() != '.xlsx':
            raise ValueError(f'不是有效的.xlsx文件：{source}')
        if source.is_relative_to(root):
            result.append(str(source))
            continue
        destination = inside(root, 'Excel')
        destination.mkdir(parents=True, exist_ok=True)
        digest = sha256(source, stop)
        target = destination / source.name
        if target.exists() and sha256(target, stop) != digest:
            target = destination / (source.stem + '-' + digest[:16] + source.suffix)
        if not target.exists():
            fd, name = tempfile.mkstemp(prefix='.excel-', dir=destination)
            try:
                with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
                    while chunk := inp.read(1024 * 1024):
                        if stop.is_set():
                            raise InterruptedError('已停止复制Excel')
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if sha256(name, stop) != digest:
                    raise ValueError('复制过程中Excel变化，请关闭编辑后重试')
                try:
                    os.link(name, target)
                except FileExistsError:
                    if sha256(target, stop) != digest:
                        raise ValueError('目标Excel被其他程序修改')
            finally:
                Path(name).unlink(missing_ok=True)
        elif sha256(target, stop) != digest:
            raise ValueError('目标Excel摘要冲突')
        result.append(str(target))
    return result


def install_case(destination):
    """Place the shipped project next to built EXEs; never alter an existing project."""
    source = Path(__file__).resolve().parents[1] / 'Jobs' / 'GD55'
    jobs = Path(destination).resolve() / 'Jobs'
    jobs.mkdir(parents=True, exist_ok=True)
    target = jobs / 'GD55'
    if target.exists():
        return
    with tempfile.TemporaryDirectory(prefix='.case-', dir=jobs) as temp:
        stage = Path(temp) / 'GD55'
        stage.mkdir()
        # Only the six reviewed case inputs, never copy a developer's task outputs.
        for name in ('GD55_1105.gds', 'GD55.dshift.json', 'GD55_SYNTHETIC_DIE1.xlsx',
                     'GD55_SYNTHETIC_DIE2.xlsx', 'GD55_SYNTHETIC_DIE3.xlsx', 'README.md'):
            shutil.copyfile(source / name, stage / name)
        if not target.exists():
            os.rename(stage, target)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-case', required=True)
    install_case(parser.parse_args().install_case)
