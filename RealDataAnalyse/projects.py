"""Portable client settings; every project-owned path stays below its folder."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile

from die_shift_studio.jobs import REALDATA_DIRECTORY, atomic_save, provision_project
from .excel import DEFAULT_PROFILE
from .measurements import HEADINGS, folders, measurement_root, table_file
from .storage import sha256

SETTINGS = 'RealDataAnalyse.project.json'
GD55_INPUTS = ('GD55_1105.gds', 'GD55.dshift.json',
               *(f'{REALDATA_DIRECTORY}/{name}' for name in (
                   SETTINGS, 'Template/GD55_Template.gds',
                   *(f'Excel/GD55_SYNTHETIC_DIE{die}.xlsx' for die in range(1, 4)),
               )))
GD55_1119_INPUTS = ('GD55_1119.gds', 'GD55_1119.dshift.json')
SH22_INPUTS = ('sh22.gds', 'SH22.dshift.json', f'{REALDATA_DIRECTORY}/{SETTINGS}',
               *(f'{REALDATA_DIRECTORY}/Excel/{die}/Measurements_{part}.csv'
                 for die in ('101', '102') for part in (1, 2)))


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
    workspace = inside(root, REALDATA_DIRECTORY)
    had_workspace = workspace.is_dir()
    manifest = inside(workspace, SETTINGS)
    if not manifest.is_file():
        manifest = inside(root, SETTINGS)  # V51 and earlier
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
    workspace = provision_project(root)
    files = list(saved.get('files') or [])
    known = {inside(root,item['path']) for item in files}
    excluded = {inside(root,path) for path in saved.get('excluded_excel',[])}
    excel = workspace / 'Excel'
    die_root = measurement_root(excel)
    die_folders = [inside(excel, path) for path in folders(die_root)] if die_root else []
    measurement = None
    if die_folders:
        measurement = dict(saved.get('measurement') or {}, root=str(die_root))
        files = [item for item in files if inside(root, item['path']).parent in die_folders]
        known = {inside(root, item['path']) for item in files}
    legacy_excel = inside(root, 'Excel')
    directories = [excel]
    legacy_selection = any(path.parent == legacy_excel for path in known)
    if legacy_excel.is_dir() and (manifest.parent == root or legacy_selection):
        directories.append(legacy_excel)
    elif not had_workspace and 'files' not in saved and not manifest.is_file():
        directories.append(legacy_excel if legacy_excel.is_dir() else root)
    for directory in die_folders or directories:
        for path in sorted(directory.iterdir()):
            if table_file(path) and (measurement or path.suffix.lower() == '.xlsx'):
                path = inside(directory,path)
                if path not in known and path not in excluded:
                    files.append(dict(path=str(path),enabled=True))
                    known.add(path)
    template = template_file(root,saved.get('template'))
    if 'template' not in saved:
        templates = [path for path in (workspace / 'Template').iterdir()
                     if path.is_file() and path.suffix.lower() in ('.gds', '.oas', '.oasis')]
        if len(templates) == 1:
            template = template_file(root, templates[0])
    normalized = []
    for item in files:
        path = inside(root, item['path'])
        if path.parent in (excel, legacy_excel, *die_folders) and not path.is_file():
            continue
        entry = dict(path=str(path), enabled=bool(item.get('enabled', True)))
        if 'profile' in item:
            entry['profile'] = copy.deepcopy(item['profile'])
        normalized.append(entry)
    database = inside(root, saved.get('database') or root / 'Tasks/measurements.sqlite')
    output = inside(root, saved.get('output') or root / 'Output' / f'{root.name}.oas')
    if not database.is_file():
        database = inside(workspace, 'Tasks/measurements.sqlite')
        output = inside(workspace, Path('Output') / f'{root.name}.oas')
    controls = dict(saved.get('controls', {}))
    if measurement:
        controls['dies'] = len(die_folders)
    default_profile = dict(DEFAULT_PROFILE, columns={label: label for label in HEADINGS}) if measurement else DEFAULT_PROFILE
    profile = copy.deepcopy(saved.get('profile', default_profile))
    if not measurement or not set(profile['columns']).intersection(HEADINGS):
        profile = copy.deepcopy(default_profile if measurement else profile)
    return dict(folder=str(root), workspace=str(workspace), project=str(project), files=normalized,
                template=str(template) if template else '', database=str(database), output=str(output),
                profile=profile, controls=controls, shared_mapping=bool(saved.get('shared_mapping', True)),
                measurement=measurement, excluded_excel=sorted(map(str,excluded)))


def save_folder(folder, project, files, database, output, profile, controls, shared_mapping=True, template=None,
                measurement=None, excluded_excel=()):
    root = Path(folder).resolve()
    workspace = provision_project(root)
    def relative(path):
        return inside(root, path).relative_to(root).as_posix()
    payload = dict(schema=1, project=relative(project), files=[dict(path=relative(p['path']), enabled=p['enabled'], **({'profile': p['profile']} if 'profile' in p else {})) for p in files],
                   database=relative(database), output=relative(output), profile=profile, controls=controls, shared_mapping=shared_mapping)
    if template:
        payload['template'] = relative(template_file(root,template))
    if measurement:
        payload['measurement'] = {key: value for key, value in measurement.items() if key != 'root'}
    selected = {inside(root,item['path']) for item in files}
    payload['excluded_excel'] = sorted(relative(path) for path in excluded_excel if inside(root, path) not in selected)
    atomic_save(inside(workspace, SETTINGS), payload)


def template_file(root, value):
    if not value:
        return None
    path = inside(root,value)
    if path.suffix.lower() not in ('.gds','.oas','.oasis') or not path.is_file():
        raise ValueError(f'模板图纸须为已有GDS/OAS文件：{value}')
    if any(path.is_relative_to(inside(root, directory)) for directory in (
            'Output', f'{REALDATA_DIRECTORY}/Output')):
        raise ValueError('模板图纸须存放在Output目录之外')
    return path


def copy_excel(folder, paths, stop):
    return copy_inputs(folder,paths,stop,'Excel',('.xlsx',))


def copy_inputs(folder, paths, stop, subfolder, suffixes):
    """Copy external inputs without altering originals or replacing existing data."""
    root = Path(folder).resolve()
    destination = inside(provision_project(root), subfolder)
    result = []
    for raw in paths:
        if stop.is_set():
            raise InterruptedError('已停止添加输入文件')
        source = Path(raw).resolve()
        if not source.is_file() or source.suffix.lower() not in suffixes:
            raise ValueError(f'输入文件格式无效：{source}')
        if source.is_relative_to(destination):
            result.append(str(source))
            continue
        digest = sha256(source, stop)
        target = inside(destination, source.name)
        if target.exists() and sha256(target, stop) != digest:
            target = inside(destination, source.stem + '-' + digest[:16] + source.suffix)
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
    """Place the shipped projects next to built EXEs; never alter an existing project."""
    root_jobs = Path(__file__).resolve().parents[1] / 'Jobs'
    jobs = Path(destination).resolve() / 'Jobs'
    jobs.mkdir(parents=True, exist_ok=True)
    for folder, inputs in (('GD55', GD55_INPUTS), ('GD55_1119', GD55_1119_INPUTS), ('SH22', SH22_INPUTS)):
        source = root_jobs / folder
        target = jobs / folder
        if target.exists() or not source.is_dir():
            continue
        with tempfile.TemporaryDirectory(prefix='.case-', dir=jobs) as temp:
            stage = Path(temp) / folder
            provision_project(stage)
            # Only the reviewed case inputs, never copy a developer's task outputs.
            for name in inputs:
                (stage / name).parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source / name, stage / name)
            if not target.exists():
                os.rename(stage, target)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-case', required=True)
    install_case(parser.parse_args().install_case)
