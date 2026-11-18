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
GD55_INPUTS = ('GD55_1105.gds','GD55.dshift.json',SETTINGS,'GD55_Template.gds',
               *(f'Excel/GD55_SYNTHETIC_DIE{die}.xlsx' for die in range(1,4)))


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
    files = list(saved.get('files') or [])
    known = {inside(root,item['path']) for item in files}
    excluded = {inside(root,path) for path in saved.get('excluded_excel',[])}
    excel = inside(root,'Excel')
    directory = excel if excel.is_dir() else root
    if directory == excel or 'files' not in saved:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower()=='.xlsx' and not path.name.startswith('~$'):
                path = inside(root,path)
                if path not in known and path not in excluded:
                    files.append(dict(path=str(path),enabled=True))
                    known.add(path)
    template = template_file(root,saved.get('template'))
    normalized = []
    for item in files:
        path = inside(root, item['path'])
        entry = dict(path=str(path), enabled=bool(item.get('enabled', True)))
        if 'profile' in item:
            entry['profile'] = copy.deepcopy(item['profile'])
        normalized.append(entry)
    return dict(folder=str(root), project=str(project), files=normalized, template=str(template) if template else '',
                database=str(inside(root, saved.get('database', 'Tasks/measurements.sqlite'))),
                output=str(inside(root, saved.get('output', 'Output/' + root.name + '.oas'))),
                profile=copy.deepcopy(saved.get('profile', DEFAULT_PROFILE)), controls=saved.get('controls', {}), shared_mapping=bool(saved.get('shared_mapping', True)))


def save_folder(folder, project, files, database, output, profile, controls, shared_mapping=True, template=None):
    root = Path(folder).resolve()
    def relative(path):
        return inside(root, path).relative_to(root).as_posix()
    payload = dict(schema=1, project=relative(project), files=[dict(path=relative(p['path']), enabled=p['enabled'], **({'profile': p['profile']} if 'profile' in p else {})) for p in files],
                   database=relative(database), output=relative(output), profile=profile, controls=controls, shared_mapping=shared_mapping)
    if template:
        payload['template'] = relative(template_file(root,template))
    selected = {inside(root,item['path']) for item in files}
    excel = inside(root,'Excel')
    payload['excluded_excel'] = [relative(path) for path in sorted(excel.glob('*'))
                                 if path.is_file() and path.suffix.lower()=='.xlsx'
                                 and not path.name.startswith('~$') and path.resolve() not in selected]
    atomic_save(inside(root, SETTINGS), payload)


def template_file(root, value):
    if not value:
        return None
    path = inside(root,value)
    if path.suffix.lower() not in ('.gds','.oas','.oasis') or not path.is_file():
        raise ValueError(f'模板图纸须为已有GDS/OAS文件：{value}')
    if path.is_relative_to(inside(root,'Output')):
        raise ValueError('模板图纸须存放在Output目录之外')
    return path


def copy_excel(folder, paths, stop):
    return copy_inputs(folder,paths,stop,'Excel',('.xlsx',))


def copy_inputs(folder, paths, stop, subfolder, suffixes):
    """Copy external inputs without altering originals or replacing existing data."""
    root = Path(folder).resolve()
    result = []
    for raw in paths:
        if stop.is_set():
            raise InterruptedError('已停止添加输入文件')
        source = Path(raw).resolve()
        if not source.is_file() or source.suffix.lower() not in suffixes:
            raise ValueError(f'输入文件格式无效：{source}')
        if source.is_relative_to(root):
            result.append(str(source))
            continue
        destination = inside(root, subfolder)
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
                            raise InterruptedError('已停止复制输入文件')
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if sha256(name, stop) != digest:
                    raise ValueError('复制过程中输入文件发生变化，请关闭编辑后重试')
                try:
                    os.link(name, target)
                except FileExistsError:
                    if sha256(target, stop) != digest:
                        raise ValueError('目标输入文件被其他程序修改')
            finally:
                Path(name).unlink(missing_ok=True)
        elif sha256(target, stop) != digest:
            raise ValueError('目标输入文件摘要冲突')
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
        # Only the reviewed case inputs, never copy a developer's task outputs.
        for name in GD55_INPUTS:
            (stage / name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source / name, stage / name)
        if not target.exists():
            os.rename(stage, target)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-case', required=True)
    install_case(parser.parse_args().install_case)
