"""Match local preview instances by exact project, never retarget another window."""
import hashlib
import json
import os
from pathlib import Path
import time

from .storage import Lease
from .transport import Transport, default_endpoint


def project_endpoint(project):
    key = hashlib.sha256(os.path.normcase(str(Path(project).resolve())).encode()).hexdigest()[:24]
    return default_endpoint().parent / ('endpoint-project-' + key + '.json')


def project_matches(info, project):
    return bool(info.get('project')) and Path(info['project']).resolve() == Path(project).resolve()


def prepare(transport, project, stop, progress, timeout=0, on_launch=None):
    until = time.monotonic() + timeout if timeout else float('inf')
    requested = False
    restarts = 0
    while time.monotonic() < until:
        if stop.is_set():
            raise InterruptedError('已停止连接工程')
        try:
            info = transport.request('info', stop=stop, retries=0)['result']
        except (OSError, EOFError, TimeoutError, json.JSONDecodeError):
            known = transport.process is not None or transport.starting or transport.descriptor
            if known and not transport.alive():
                if restarts >= 3:
                    raise RuntimeError('预演软件准备阶段连续退出，自动重启已耗尽；请查看项目日志')
                restarts += 1
                progress(f'预演软件已退出，重新启动同目录程序（{restarts}/3）')
                transport.launch(project)
                if on_launch: on_launch(transport)
                requested = False
                continue
            if stop.wait(.2):
                raise InterruptedError('已停止连接工程')
            continue
        if info.get('project') and not project_matches(info, project):
            raise ValueError('IPC入口属于其他工程，未发送切换或计算指令')
        if info.get('last_error'):
            raise RuntimeError(str(info['last_error']))
        if project_matches(info, project) and info.get('ready'):
            transport.expected_instance = info.get('instance_id')
            return info
        progress(dict(text='连接对应工程 · 等待载入 / 实测准备', current=0, total=1))
        # V47 may enable IPC without automatically preparing geometry.
        if project_matches(info, project) and not requested and not info.get('running') and not info.get('preparing_task'):
            transport.request('session.prepare', stop=stop)
            requested = True
        stop.wait(.25)
    raise TimeoutError('等待工程准备超时')


def connect(project, preferred, stop, progress, timeout=0, trace=None, exact=False, on_launch=None):
    project = Path(project).resolve()
    if not project.is_file():
        raise FileNotFoundError('工程文件不存在')
    preferred = Path(preferred).resolve()
    dedicated = project_endpoint(project)
    candidates = [preferred] if exact else [preferred, dedicated, default_endpoint()]
    if not exact:
        folder = default_endpoint().parent
        others = list(folder.glob('endpoint*.json')) if folder.is_dir() else []
        if len(others) > 256:
            raise ValueError('IPC入口过多，请明确选择目标实例入口')
        candidates.extend(others)
    matches = []
    for endpoint in dict.fromkeys(candidates):
        if stop.is_set():
            raise InterruptedError('已停止查找实例')
        if not endpoint.is_file():
            continue
        transport = Transport(endpoint, timeout=2, trace=trace)
        if not transport.alive():
            continue
        try:
            info = transport.request('info', stop=stop, retries=0)['result']
        except (OSError, EOFError, TimeoutError, json.JSONDecodeError):
            if endpoint == preferred:
                raise RuntimeError('指定实例仍存活但未响应，未重复启动或接管其他窗口')
            continue
        if project_matches(info, project):
            matches.append((transport, info))
        elif exact:
            raise ValueError('指定IPC入口属于其他工程，未发送指令')
    chosen = next((item for item in matches if item[0].endpoint == preferred), None)
    if chosen is None and len(matches) > 1:
        raise ValueError('多个实例打开了同一工程，请在IPC入口中明确选择一个')
    chosen = chosen or (matches[0] if matches else None)
    if chosen:
        transport, info = chosen
        transport.timeout = 15
        return transport, prepare(transport, project, stop, progress, timeout, on_launch), transport.process is not None
    custom_new = preferred != default_endpoint() and preferred.parent != default_endpoint().parent and not preferred.exists()
    endpoint = preferred if exact or custom_new else dedicated
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    # A lifetime file lock covers the descriptor's startup gap across clients.
    with Lease(str(endpoint) + '.startup'):
        transport = Transport(endpoint, trace=trace)
        if transport.alive():
            info = transport.request('info', stop=stop)['result']
            if not project_matches(info, project):
                raise ValueError('项目专用入口已被其他工程占用，未重复启动')
            return transport, prepare(transport, project, stop, progress, timeout, on_launch), transport.process is not None
        if transport.find_starting(project):
            return transport, prepare(transport, project, stop, progress, timeout, on_launch), transport.process is not None
        transport.launch(project)
        if on_launch: on_launch(transport)
        info = prepare(transport, project, stop, progress, timeout, on_launch)
        return transport, info, True
