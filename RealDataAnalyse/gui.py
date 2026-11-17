"""Qt GUI; all IO/Excel/IPC work stays outside the GUI thread."""
import json
import copy
from contextlib import closing
from pathlib import Path
import threading
import time

from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W, Signal
from . import __version__
from .excel import DEFAULT_PROFILE, HEADERS, import_files
from .projects import load_folder, save_folder, inside, copy_excel
from .runner import Runner
from .storage import Ledger, configure_logging
from .transport import Transport, default_endpoint, installation
from .conversation import Conversation
from .instances import connect as connect_instance, project_matches
from .widgets import table_frame, ConversationColors
from .plans import Plans, PlanRunner, capture as capture_plan
from .plan_ui import PlanPanel

COMMANDS = {
    'ping': {}, 'info': {}, 'app.start': {}, 'project.open': {'path': ''},
    'project.close': {}, 'session.prepare': {},
    'output.configure': {'output_directory': '', 'max_threads': 4, 'max_memory_mb': 2048},
    'preview.submit': {'dies': [{'die': 1, 'dx_um': 0, 'dy_um': 0, 'angle_deg': 0}]},
    'export.gds': {'filename': 'measured.gds'},
    'batch.begin': {'filename': 'measured.oas'},
    'batch.submit': {'batch_id': '', 'cell': 'Package_000001', 'position_um': [0, 0], 'dies': []},
    'batch.end': {'batch_id': ''}, 'task.get': {'task_id': ''},
    'compute.cancel': {}, 'app.shutdown': {},
}


class Worker(QtCore.QThread):
    message = Signal(object)
    done = Signal(object, object)

    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function
        self.stop = threading.Event()

    def run(self):
        try:
            result = self.function(self.stop, self.message.emit)
            self.done.emit(result, None)
        except Exception as exc:
            self.done.emit(None, str(exc))


class ProgressBar(W.QProgressBar):
    """Single continuous fill and one centered label, independent of Windows chunks."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(30)
        self.setTextVisible(False)
        self.detail = '就绪 · 请选择项目文件夹'
        self.started = None
        self.elapsed = 0
        self.failed = False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QtGui.QColor('#edf2f7'))
        span = max(1, self.maximum() - self.minimum())
        fraction = max(0., min(1., (self.value() - self.minimum()) / span))
        fill = QtCore.QRect(rect)
        fill.setWidth(round(rect.width() * fraction))
        painter.fillRect(fill, QtGui.QColor('#efb3ad' if self.failed else '#79bce8'))
        painter.setPen(QtGui.QColor('#8796a5'))
        painter.drawRect(rect)
        painter.setPen(QtGui.QColor('#142536'))
        elapsed = int(time.monotonic() - self.started) if self.started else self.elapsed
        label = self.detail + (f' · {round(fraction * 100)}%' if self.maximum() > 1 else '') + (f' · 已用 {elapsed // 60:02d}:{elapsed % 60:02d}' if elapsed else '')
        alignment = getattr(QtCore.Qt, 'AlignmentFlag', QtCore.Qt).AlignCenter
        elide = getattr(QtCore.Qt, 'TextElideMode', QtCore.Qt).ElideRight
        painter.drawText(rect.adjusted(8, 0, -8, 0), alignment, self.fontMetrics().elidedText(label, elide, rect.width() - 16))

    def status(self, text, current=None, total=None):
        self.detail = str(text).replace('\n', ' ')
        self.setToolTip(self.detail)
        if current is not None:
            self.setRange(0, max(1, int(total or 1)))
            self.setValue(int(current))
        self.update()


class Window(W.QMainWindow):
    def __init__(self, plan_path=None):
        super().__init__()
        self.setWindowTitle('RealDataAnalyse ' + __version__)
        self.resize(1120, 860)
        self.worker = None
        self.pending_close = False
        self.page = 0
        self.project_root = None
        self.operation = ''
        self.last_log_update = 0
        self.shared_profile = copy.deepcopy(DEFAULT_PROFILE)
        self.file_profiles = {}
        self.mapping_file = None
        self.mapping_mode = True
        self.mapping_guard = False
        self.settings = QtCore.QSettings('DieShiftAnalyze', 'RealDataAnalyse')
        self.logger = configure_logging(installation())
        self.logger.info('RealDataAnalyse %s started', __version__)
        self.conversation = Conversation(message_limit=4000)
        self.conversation.set_root(installation())
        self.bindings = {}
        self.plan_store = Plans(plan_path or installation()/'Jobs'/'.RealDataAnalyse'/'plans.sqlite')
        self.plan_panel = None
        base = W.QWidget()
        self.setCentralWidget(base)
        layout = W.QVBoxLayout(base)
        connection = W.QGroupBox('连接与工程')
        form = W.QFormLayout(connection)
        folder_row = W.QHBoxLayout()
        self.folder = W.QLineEdit()
        self.folder.setReadOnly(True)
        self.folder_button = W.QPushButton('选择项目文件夹…')
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.folder_button)
        form.addRow('项目文件夹', folder_row)
        # Paths are project-owned state, never independent user input rows.
        self.project = W.QLineEdit(self)
        self.database = W.QLineEdit(self)
        self.output = W.QLineEdit(self)
        for state in (self.project, self.database, self.output):
            state.hide()
        self.endpoint = self.path_row(form, 'IPC入口', str(default_endpoint()), '*.json', 'endpoint')
        buttons = W.QHBoxLayout()
        self.connect_button = W.QPushButton('连接状态')
        self.start_button = W.QPushButton('启动预演软件')
        self.plans_button = W.QPushButton('计划任务')
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.plans_button)
        form.addRow(buttons)
        controls = W.QHBoxLayout()
        self.dies = self.spin(controls, 'DIE数量', 1, 10000, 3)
        self.workers = self.spin(controls, '并发线程', 1, 64, 4)
        self.memory = self.spin(controls, '内存MB', 128, 1048576, 2048)
        self.retries = self.spin(controls, '自动重试', 0, 10, 3)
        self.timeout = self.spin(controls, '任务超时秒', 0, 86400, 0)
        self.timeout.setSpecialValueText('0（无超时）')
        form.addRow(controls)
        layout.addWidget(connection)
        tabs = W.QTabWidget()
        self.tabs = tabs
        layout.addWidget(tabs, 1)
        batch = W.QWidget()
        batch_scroll = W.QScrollArea()
        batch_scroll.setWidgetResizable(True)
        batch_scroll.setWidget(batch)
        tabs.addTab(batch_scroll, 'Excel批量 / 故障恢复')
        batch_layout = W.QVBoxLayout(batch)
        row = W.QHBoxLayout()
        self.add_files = W.QPushButton('添加Excel行')
        self.remove_files = W.QPushButton('删除选中行')
        row.addWidget(self.add_files)
        row.addWidget(self.remove_files)
        row.addStretch()
        batch_layout.addLayout(row)
        self.file_view = W.QTableWidget(0, 2)
        self.file_view.setHorizontalHeaderLabels(['使用', 'Excel文件（相对于项目文件夹）'])
        self.file_view.setColumnWidth(0, 55)
        self.file_view.horizontalHeader().setStretchLastSection(True)
        self.file_view.setSelectionBehavior(getattr(W.QAbstractItemView, 'SelectionBehavior', W.QAbstractItemView).SelectRows)
        self.file_view.setMinimumHeight(100)
        self.file_view.setMaximumHeight(160)
        self.file_frame = table_frame(self.file_view, 135, self.settings, 'excel')
        batch_layout.addWidget(self.file_frame)
        profile_box = W.QGroupBox()
        profile_layout = W.QVBoxLayout(profile_box)
        self.mapping_shared = W.QCheckBox('Excel列映射（长度μm，角度°；中心留空沿用工程）')
        self.mapping_shared.setChecked(True)
        self.mapping_shared.setToolTip('勾选：所有Excel共用列映射；取消：点击上方Excel行编辑该文件的独立映射')
        profile_layout.addWidget(self.mapping_shared)
        sheet_row = W.QHBoxLayout()
        sheet_row.addWidget(W.QLabel('工作表'))
        self.sheet = W.QLineEdit(DEFAULT_PROFILE['sheet'])
        sheet_row.addWidget(self.sheet, 1)
        self.header_row = self.spin(sheet_row, '表头行', 1, 1048576, 1)
        profile_layout.addLayout(sheet_row)
        self.profile = W.QTableWidget(len(HEADERS), 3)
        self.profile.setHorizontalHeaderLabels(['数据字段', 'Excel列名', '要求 / 单位'])
        self.profile.verticalHeader().show()
        self.profile.horizontalHeader().setStretchLastSection(True)
        self.profile.setColumnWidth(0, 220)
        self.profile.setColumnWidth(1, 250)
        descriptions = ['必填文本，保留前导零', '必填整数，从1起', '必填 / μm', '必填 / μm', '必填 / °', '必填 / μm', '必填 / μm', '可选，XY成对 / μm', '可选，XY成对 / μm']
        for i, field in enumerate(HEADERS):
            for j, text in enumerate((field, field, descriptions[i])):
                item = W.QTableWidgetItem(text)
                if j != 1:
                    item.setFlags(item.flags() & ~getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsEditable)
                self.profile.setItem(i, j, item)
        self.profile.setMinimumHeight(150)
        self.profile.setMaximumHeight(220)
        self.profile_frame = table_frame(self.profile, 230, self.settings, 'mapping')
        profile_layout.addWidget(self.profile_frame)
        batch_layout.addWidget(profile_box)
        controls = W.QHBoxLayout()
        self.import_button = W.QPushButton('创建任务')
        self.create_plan_button = W.QPushButton('创建计划任务')
        self.run_button = W.QPushButton('运行 / 继续补算')
        self.stop_button = W.QPushButton('安全停止')
        self.stop_button.setEnabled(False)
        for widget in (self.import_button, self.create_plan_button, self.run_button, self.stop_button):
            controls.addWidget(widget)
        batch_layout.addLayout(controls)
        self.table = W.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['封装ID', 'Cell', '各DIE偏移（μm / °）', '状态', '任务/错误'])
        self.table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers if hasattr(W.QAbstractItemView, 'EditTrigger') else W.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((170, 200, 320, 100)):
            self.table.setColumnWidth(col, width)
        self.table.setMinimumHeight(150)
        self.result_frame = table_frame(self.table, 240, self.settings, 'results')
        batch_layout.addWidget(self.result_frame, 1)
        self.table.cellDoubleClicked.connect(self.show_dies)
        paging = W.QHBoxLayout()
        prev = W.QPushButton('上一页')
        nxt = W.QPushButton('下一页')
        paging.addWidget(prev)
        paging.addWidget(nxt)
        self.page_label = W.QLabel()
        paging.addWidget(self.page_label)
        paging.addStretch()
        batch_layout.addLayout(paging)
        debug = W.QWidget()
        tabs.addTab(debug, '全部IPC命令调试')
        debug_layout = W.QVBoxLayout(debug)
        self.command = W.QComboBox()
        self.command.addItems(list(COMMANDS))
        debug_layout.addWidget(self.command)
        self.args = W.QPlainTextEdit('{}')
        debug_layout.addWidget(self.args, 1)
        self.wait_check = W.QCheckBox('自动等待异步任务完成（取消等待不等于取消服务任务）')
        self.wait_check.setChecked(True)
        debug_layout.addWidget(self.wait_check)
        self.send_button = W.QPushButton('发送命令')
        debug_layout.addWidget(self.send_button)
        self.response = W.QPlainTextEdit()
        self.response.setReadOnly(True)
        debug_layout.addWidget(self.response, 2)
        conversation_tab = W.QWidget()
        tabs.addTab(conversation_tab, '实时命令信息')
        conversation_layout = W.QVBoxLayout(conversation_tab)
        conversation_controls = W.QHBoxLayout()
        self.conversation_limit = self.spin(conversation_controls, '保留行数', 100, 10000, 2000)
        self.clear_conversation = W.QPushButton('清空显示')
        conversation_controls.addWidget(self.clear_conversation)
        conversation_layout.addLayout(conversation_controls)
        self.conversation_view = W.QPlainTextEdit()
        self.conversation_view.setReadOnly(True)
        self.conversation_view.setMaximumBlockCount(2000)
        self.conversation_view.setLineWrapMode(getattr(W.QPlainTextEdit,'LineWrapMode',W.QPlainTextEdit).NoWrap)
        self.conversation_colors = ConversationColors(self.conversation_view.document())
        conversation_layout.addWidget(self.conversation_view, 1)
        self.conversation_limit.valueChanged.connect(self.conversation_view.setMaximumBlockCount)
        self.clear_conversation.clicked.connect(self.conversation_view.clear)
        self.conversation_timer = QtCore.QTimer(self)
        self.conversation_timer.setInterval(100)
        self.conversation_timer.timeout.connect(self.flush_conversation)
        self.conversation_timer.start()

        self.log_view = W.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setMaximumHeight(115)
        self.log_check = W.QCheckBox()
        self.log_check.setToolTip('显示 / 隐藏信息输出')
        self.log_check.setAccessibleName('显示信息输出')
        self.log_view.hide()
        self.log_check.toggled.connect(self.log_view.setVisible)
        layout.addWidget(self.log_view)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_row = W.QHBoxLayout()
        progress_row.addWidget(self.log_check)
        progress_row.addWidget(self.progress, 1)
        layout.addLayout(progress_row)
        self.mapping_shared.toggled.connect(self.toggle_mapping)
        self.file_view.currentCellChanged.connect(self.select_mapping)
        self.folder_button.clicked.connect(self.select_folder)
        self.connect_button.clicked.connect(self.connect_server)
        self.start_button.clicked.connect(self.start_server)
        self.add_files.clicked.connect(self.select_files)
        self.remove_files.clicked.connect(self.delete_files)
        self.import_button.clicked.connect(self.do_import)
        self.run_button.clicked.connect(self.do_run)
        self.stop_button.clicked.connect(self.stop_work)
        self.send_button.clicked.connect(self.send)
        self.plans_button.clicked.connect(self.show_plans)
        self.create_plan_button.clicked.connect(self.create_plan)
        self.command.currentTextChanged.connect(self.command_changed)
        prev.clicked.connect(lambda: self.change_page(-1))
        nxt.clicked.connect(lambda: self.change_page(1))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.timeout.connect(self.progress.update)
        self.timer.start()

    def path_row(self, form, label, default, pattern, key, save=False, readonly=False):
        row = W.QHBoxLayout()
        edit = W.QLineEdit(str(self.settings.value(key, default) if key == 'endpoint' else default))
        edit.setReadOnly(readonly)
        button = W.QPushButton('浏览…')
        def browse():
            dialog = W.QFileDialog.getSaveFileName if save else W.QFileDialog.getOpenFileName
            path, _ = dialog(self, label, edit.text(), f'文件 ({pattern});;所有文件 (*)')
            if path:
                edit.setText(path)
        button.clicked.connect(browse)
        row.addWidget(edit, 1)
        if not readonly:
            row.addWidget(button)
        self.path_buttons = getattr(self, 'path_buttons', []) + [button]
        form.addRow(label, row)
        edit.editingFinished.connect(lambda: self.settings.setValue(key, edit.text()))
        return edit

    @staticmethod
    def spin(layout, text, low, high, value):
        layout.addWidget(W.QLabel(text))
        spin = W.QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        layout.addWidget(spin)
        return spin

    def log(self, message):
        if isinstance(message, dict):
            self.progress.status(message.get('text', ''), message.get('current'), message.get('total'))
            now = time.monotonic()
            if now - self.last_log_update >= 1:
                self.last_log_update = now
                text = str(message.get('text', ''))
                self.log_view.appendPlainText(time.strftime('%H:%M:%S ') + text)
                self.logger.info('%s', text)
            return
        text = str(message)
        self.log_view.appendPlainText(time.strftime('%H:%M:%S ') + text)
        self.progress.status(text)

    def busy(self, on):
        for widget in (self.connect_button, self.start_button, self.add_files, self.remove_files,
                       self.folder_button, self.import_button, self.create_plan_button, self.run_button, self.send_button,
                       self.endpoint, self.project, self.database, self.output, self.dies, self.profile,
                       self.workers, self.memory, self.retries, self.timeout, self.file_view, self.sheet, self.header_row, self.mapping_shared,
                       *self.path_buttons):
            widget.setEnabled(not on)
        self.stop_button.setEnabled(on)

    def execute(self, fn, callback=None, failure=None, operation='操作'):
        if self.worker:
            return
        self.operation = operation
        self.busy(True)
        self.progress.failed = False
        self.progress.started = time.monotonic()
        self.progress.status(operation + ' · 正在开始', 0, 100)
        worker = Worker(fn, self)
        self.worker = worker
        worker.message.connect(self.log)
        def result(value, error):
            try:
                if error:
                    raise RuntimeError(error)
                if callback:
                    callback(value)
                self.response.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
                self.log_view.appendPlainText(time.strftime('%H:%M:%S ') + operation + '完成')
                detail = self.progress.detail if self.progress.detail.startswith(('完成 ·', '导入完成 ·')) else operation + '完成'
                if isinstance(value, dict) and value.get('state') == 'paused':
                    self.progress.status('计划任务已暂停')
                elif isinstance(value, dict) and value.get('state') == 'completed_with_errors':
                    self.progress.failed = True
                    self.progress.status(f'计划处理结束 · 完成{value["completed"]} · 失败{value["failed"]} · 跳过{value["skipped"]}', 100, 100)
                else:
                    self.progress.status(detail, 100, 100)
            except Exception as exc:
                self.log(str(exc))
                self.logger.error('%s', exc)
                self.response.setPlainText(str(exc))
                self.progress.failed = True
                self.progress.status(operation + '停止 · ' + str(exc))
                if failure:
                    failure(str(exc))
        worker.done.connect(result)
        def finished():
            self.progress.elapsed = int(time.monotonic() - self.progress.started)
            self.progress.started = None
            self.worker = None
            self.busy(False)
            self.refresh()
            worker.deleteLater()
            if self.pending_close:
                self.close()
        worker.finished.connect(finished)
        worker.start()

    @staticmethod
    def button_status(button, good):
        color = '#237844' if good else '#b33630'
        button.setStyleSheet(f'QPushButton {{background-color:{color}; color:white; padding:5px; border:1px solid {color}; border-radius:3px}}')
        button.setProperty('connected', good)

    def accept_info(self, info):
        if self.project_root and not project_matches(info, self.project.text()):
            raise ValueError('所选IPC入口属于其他工程，未接管该窗口')
        self.info = info
        endpoint = info.get('endpoint') or self.endpoint.text()
        self.endpoint.setText(endpoint)
        self.bindings[str(Path(endpoint).resolve())] = info.get('instance_id')
        self.button_status(self.connect_button, True)
        self.connect_button.setToolTip(f'连接正常 · ready={info.get("ready")} · 工程={info.get("project")}')
        if info.get('die_count'):
            self.dies.setValue(info['die_count'])

    def connect_server(self):
        endpoint = self.endpoint.text()
        self.execute(lambda stop, progress: Transport(endpoint, trace=self.conversation).request('info', stop=stop),
                     lambda reply: self.accept_info(reply['result']),
                     lambda error: self.button_status(self.connect_button, False), '查询连接状态')

    def linked_project(self, endpoint, project, stop, progress, timeout, output=None):
        transport, info, launched = connect_instance(project, endpoint, stop, progress, timeout, self.conversation)
        if output is not None and not info.get('batch') and not info.get('running'):
            directory = Path(output).parent.parent / "Cache" / "Manual"
            directory.mkdir(parents=True, exist_ok=True)
            transport.request('output.configure', {'output_directory': str(directory)}, stop=stop)
        return dict(info, endpoint=str(transport.endpoint))

    def start_server(self):
        try:
            self.persist()
        except (OSError, ValueError) as exc:
            self.log(exc)
            self.button_status(self.start_button, False)
            return
        endpoint, project, timeout, output = self.endpoint.text(), self.project.text(), self.timeout.value(), self.output.text()
        def done(info):
            self.accept_info(info)
            self.button_status(self.start_button, True)
        def failed(error):
            self.button_status(self.start_button, False)
            self.button_status(self.connect_button, False)
        self.execute(lambda stop, progress: self.linked_project(endpoint, project, stop, progress, timeout, output), done, failed, '启动 / 联动预演工程')

    def select_folder(self):
        folder = W.QFileDialog.getExistingDirectory(self, '选择项目文件夹', str(installation() / 'Jobs'))
        if folder:
            self.open_folder(folder)

    def open_folder(self, folder, link=True):
        if self.worker:
            return
        if self.project_root is not None:
            try:
                self.persist()
            except (OSError, ValueError) as exc:
                self.log(exc)
                return
        endpoint, timeout = self.endpoint.text(), self.timeout.value()
        def load(stop, progress):
            config = load_folder(folder)
            configure_logging(Path(config['folder']))
            self.conversation.set_root(config['folder'])
            if link:
                try:
                    config['info'] = self.linked_project(endpoint, config['project'], stop, progress, timeout, config['output'])
                except Exception as exc:
                    config['connection_error'] = str(exc)
            return config
        def done(config):
            self.project_root = Path(config['folder'])
            self.folder.setText(config['folder'])
            self.project.setText(config['project'])
            self.database.setText(config['database'])
            self.output.setText(config['output'])
            self.shared_profile = copy.deepcopy(config['profile'])
            self.file_profiles = {entry['path']: copy.deepcopy(entry['profile']) for entry in config['files'] if 'profile' in entry}
            self.mapping_file = None
            self.mapping_mode = config.get('shared_mapping', True)
            self.mapping_shared.blockSignals(True)
            self.mapping_shared.setChecked(self.mapping_mode)
            self.mapping_shared.blockSignals(False)
            self.set_profile(self.shared_profile)
            self.set_files(config['files'])
            for name, value in config['controls'].items():
                if name in ('dies', 'workers', 'memory', 'retries', 'timeout'):
                    getattr(self, name).setValue(int(value))
            self.logger = configure_logging(self.project_root)
            self.page = 0
            self.table.setRowCount(0)
            if not config.get('controls', {}).get('timeout'):
                self.timeout.setValue(0)
            if config.get('info'):
                self.accept_info(config['info'])
                self.button_status(self.start_button, True)
            elif config.get('connection_error'):
                self.button_status(self.connect_button, False)
                self.button_status(self.start_button, False)
                self.logger.error('工程已选择，但联动失败：%s', config['connection_error'])
                raise ValueError('项目已载入，联动失败：' + config['connection_error'])
        self.execute(load, done, operation='载入项目文件夹')

    def set_files(self, files):
        self.capture_mapping()
        self.file_view.blockSignals(True)
        self.file_view.setRowCount(len(files))
        for row, entry in enumerate(files):
            check = W.QTableWidgetItem()
            check.setFlags(getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsEnabled | getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsSelectable | getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsUserCheckable)
            check.setCheckState(getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked if entry.get('enabled', True) else getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Unchecked)
            self.file_view.setItem(row, 0, check)
            path = Path(entry['path']).resolve()
            item = W.QTableWidgetItem(str(path.relative_to(self.project_root)) if self.project_root else str(path))
            item.setData(getattr(QtCore.Qt, 'ItemDataRole', QtCore.Qt).UserRole, str(path))
            item.setToolTip(str(path))
            item.setFlags(item.flags() & ~getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsEditable)
            self.file_view.setItem(row, 1, item)
        self.file_view.blockSignals(False)
        if files:
            selected = next((i for i, entry in enumerate(files) if entry['path'] == self.mapping_file), 0)
            self.file_view.setCurrentCell(selected, 1)
        self.select_mapping()

    def file_entries(self):
        self.capture_mapping()
        entries = []
        for row in range(self.file_view.rowCount()):
            path = self.file_view.item(row, 1).data(getattr(QtCore.Qt, 'ItemDataRole', QtCore.Qt).UserRole)
            entry = dict(path=path, enabled=self.file_view.item(row, 0).checkState() == getattr(QtCore.Qt, 'CheckState', QtCore.Qt).Checked)
            if path in self.file_profiles:
                entry['profile'] = copy.deepcopy(self.file_profiles[path])
            entries.append(entry)
        return entries

    def capture_mapping(self):
        if self.mapping_guard:
            return
        value = self.profile_value()
        if self.mapping_mode:
            self.shared_profile = value
        elif self.mapping_file:
            self.file_profiles[self.mapping_file] = value

    def select_mapping(self, *unused):
        if self.mapping_guard:
            return
        self.capture_mapping()
        row = self.file_view.currentRow()
        self.mapping_file = self.file_view.item(row, 1).data(getattr(QtCore.Qt, 'ItemDataRole', QtCore.Qt).UserRole) if row >= 0 and self.file_view.item(row, 1) else None
        value = self.shared_profile if self.mapping_mode else self.file_profiles.get(self.mapping_file, self.shared_profile)
        self.set_profile(value)
        self.sheet.setToolTip('所有Excel共用' if self.mapping_mode else f'当前Excel：{self.mapping_file or "请先选择Excel行"}')

    def toggle_mapping(self, shared):
        self.capture_mapping()
        self.mapping_mode = shared
        if not shared:
            for row in range(self.file_view.rowCount()):
                path = self.file_view.item(row, 1).data(getattr(QtCore.Qt, 'ItemDataRole', QtCore.Qt).UserRole)
                self.file_profiles.setdefault(path, copy.deepcopy(self.shared_profile))
        self.set_profile(self.shared_profile if shared else self.file_profiles.get(self.mapping_file, self.shared_profile))
        self.sheet.setToolTip('所有Excel共用' if shared else f'当前Excel：{self.mapping_file or "请先选择Excel行"}')

    def import_profiles(self, entries):
        self.capture_mapping()
        if self.mapping_mode:
            return None
        return {entry['path']: copy.deepcopy(self.file_profiles.get(entry['path'], self.shared_profile)) for entry in entries if entry['enabled']}

    def set_profile(self, profile):
        self.mapping_guard = True
        self.sheet.setText(profile['sheet'])
        self.header_row.setValue(profile['header_row'])
        for row, field in enumerate(HEADERS):
            self.profile.item(row, 1).setText(profile.get('columns', {}).get(field, field))
        self.mapping_guard = False

    def profile_value(self):
        columns = {field: self.profile.item(row, 1).text().strip() for row, field in enumerate(HEADERS)}
        return dict(sheet=self.sheet.text().strip(), header_row=self.header_row.value(), columns=columns)

    def persist(self):
        if self.project_root is None:
            raise ValueError('请先选择项目文件夹')
        files = self.file_entries()
        profile = copy.deepcopy(self.shared_profile)
        save_folder(self.project_root, self.project.text(), files, self.database.text(), self.output.text(), profile,
                    {key: getattr(self, key).value() for key in ('dies', 'workers', 'memory', 'retries', 'timeout')}, shared_mapping=self.mapping_mode)
        return files, profile

    def select_files(self):
        if self.project_root is None:
            self.log('请先选择项目文件夹')
            return
        files, _ = W.QFileDialog.getOpenFileNames(self, '添加各DIE Excel（外部文件将复制到项目内）', str(self.project_root), 'Excel (*.xlsx)')
        if not files:
            return
        entries = self.file_entries()
        existing = {entry['path'] for entry in entries}
        def done(paths):
            for path in paths:
                if path not in existing:
                    entries.append(dict(path=path, enabled=True))
                    existing.add(path)
            self.set_files(entries)
            self.persist()
        root = self.project_root
        self.execute(lambda stop, progress: copy_excel(root, files, stop), done, operation='添加Excel')

    def delete_files(self):
        for row in sorted({index.row() for index in self.file_view.selectedIndexes()}, reverse=True):
            self.file_view.removeRow(row)
        try:
            self.persist()
        except ValueError as exc:
            self.log(exc)

    def do_import(self):
        try:
            files, profile = self.persist()
            target = inside(self.project_root, self.database.text())
            if target.exists():
                import uuid
                target = target.with_name(target.stem + '_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6] + '.sqlite')
        except (OSError, ValueError) as exc:
            self.log(exc)
            return
        profiles = self.import_profiles(files)
        files = [entry['path'] for entry in files if entry['enabled']]
        dies, workers = self.dies.value(), self.workers.value()
        def done(value):
            self.database.setText(value['database'])
            self.page = 0
            self.persist()
        self.execute(lambda stop, progress: import_files(files, target, dies, workers, stop, progress, profile, profiles), done, operation='校验Excel并创建任务')

    def do_run(self):
        try:
            self.persist()
        except (OSError, ValueError) as exc:
            self.log(exc)
            return
        kwargs = dict(database=self.database.text(), endpoint=self.endpoint.text(), project=self.project.text(), output=self.output.text(),
                      workers=self.workers.value(), memory_mb=self.memory.value(), retries=self.retries.value(), timeout=self.timeout.value())
        entries = self.file_entries()
        selected = sorted(p['path'] for p in entries if p['enabled'])
        profile = copy.deepcopy(self.shared_profile)
        profiles = self.import_profiles(entries) or {}
        def run(stop, progress):
            import sqlite3
            with closing(sqlite3.connect(Path(kwargs['database']).resolve().as_uri() + '?mode=ro', uri=True)) as db:
                meta = {k: json.loads(v) for k, v in db.execute('select key,value from meta')}
            if selected != sorted(p['path'] for p in meta.get('sources', [])) or profile != meta.get('profile') or profiles != meta.get('profiles', {}) or self_die != meta.get('die_count'):
                raise ValueError('Excel勾选、列映射或DIE数量与任务记录不符，请先校验并创建新任务')
            from .storage import sha256
            for index, source in enumerate(meta.get('sources', []), 1):
                progress(dict(text=f'核对Excel输入 · {index}/{len(selected)} · {Path(source["path"]).name}', current=index - 1, total=len(selected)))
                if sha256(source['path'], stop) != source['sha256']:
                    raise ValueError('Excel内容已修改，请先校验并创建新任务')
            return Runner(**kwargs, stop=stop, progress=progress, trace=self.conversation).run()
        self_die = self.dies.value()
        self.execute(run, operation='批量计算与OAS输出')

    def command_changed(self, command):
        args = dict(COMMANDS[command])
        if command == 'project.open':
            args['path'] = self.project.text()
        if command == 'output.configure':
            args.update(output_directory=str(Path(self.output.text()).parent.parent / 'Cache' / 'Manual'), max_threads=self.workers.value(), max_memory_mb=self.memory.value())
        if command in ('preview.submit', 'batch.submit'):
            args['dies'] = [dict(die=i + 1, dx_um=0, dy_um=0, angle_deg=0) for i in range(self.dies.value())]
        if command in ('batch.submit', 'batch.end'):
            args['batch_id'] = getattr(self, 'manual_batch', '')
        if command in ('task.get','task.issues'):
            args['task_id'] = getattr(self, 'manual_task', '')
        self.args.setPlainText(json.dumps(args, ensure_ascii=False, indent=2))

    def send(self):
        command = self.command.currentText()
        if command == 'app.start':
            self.start_server()
            return
        try:
            args = json.loads(self.args.toPlainText())
            if not isinstance(args, dict):
                raise ValueError('args须为JSON对象')
        except ValueError as exc:
            self.log(str(exc))
            return
        try:
            if command == 'project.open':
                if self.project_root is None:
                    raise ValueError('请先选择项目文件夹')
                args['path'] = str(inside(self.project_root, args['path']))
            if command == 'output.configure':
                if self.project_root is None:
                    raise ValueError('请先选择项目文件夹')
                directory = inside(self.project_root, args['output_directory'])
                directory.mkdir(parents=True, exist_ok=True)
                args['output_directory'] = str(directory)
        except (OSError, ValueError) as exc:
            self.log(exc)
            return
        endpoint, should_wait, timeout = self.endpoint.text(), self.wait_check.isChecked(), self.timeout.value()
        bound_instance = self.bindings.get(str(Path(endpoint).resolve()))
        expected_project = self.project.text()
        def send(stop, progress):
            t = Transport(endpoint, trace=self.conversation, expected_instance=bound_instance)
            if command not in ('ping', 'info', 'task.get', 'task.issues'):
                if not expected_project:
                    raise ValueError('请先选择项目文件夹')
                info = t.request('info', stop=stop)['result']
                if not project_matches(info, expected_project) and not (command == 'project.open' and not info.get('project')):
                    raise ValueError('实例工程已变化，未发送命令')
                t.expected_instance = info.get('instance_id')
            response = t.request(command, args, stop=stop)
            if should_wait and response.get('task_id'):
                response = dict(response, completed_result=t.wait_task(response['task_id'], stop, timeout, progress=progress))
            return response
        def done(response):
            if command == 'project.open':
                self.project.setText(args['path'])
            if command == 'app.shutdown':
                self.button_status(self.connect_button, False)
                self.button_status(self.start_button, False)
            if response.get('task_id'):
                self.manual_task = response['task_id']
            if response.get('result', {}).get('batch_id'):
                self.manual_batch = response['result']['batch_id']
        self.execute(send, done)

    def stop_work(self):
        if self.worker:
            self.worker.stop.set()
            self.log('已请求停止；批量运行将取消自己的批次，等待安全点')

    def change_page(self, step):
        self.page = max(0, self.page + step)
        self.refresh()

    def refresh(self):
        path = Path(self.database.text())
        if not path.is_file():
            return
        # Read-only connection: viewing an arbitrary file cannot create schema.
        import sqlite3
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.05)) as db:
                meta = {k: json.loads(v) for k, v in db.execute('select key,value from meta')}
                count = meta.get('count', 0)
                self.page = min(self.page, max(0, (count - 1) // 200))
                rows = db.execute('select seq,package,cell,state,coalesce(result,task,\'\'),payload from packages where seq>? order by seq limit 200', (self.page * 200,)).fetchall()
            self.table.setRowCount(len(rows))
            self.table.setVerticalHeaderLabels([str(row[0]) for row in rows])
            role = getattr(QtCore.Qt,'ItemDataRole',QtCore.Qt).UserRole
            for i, row in enumerate(rows):
                seq, package, cell, state, result, payload = row
                poses = json.loads(payload).get('dies', [])
                lines = [self.die_text(pose) for pose in poses]
                preview = '; '.join(lines[:20]) + (f' … 共{len(lines)}颗DIE，双击展开' if len(lines)>20 else '')
                state={'computed_with_issues':'已计算（含问题）','committed_with_issues':'已输出（含问题）','committed':'已输出'}.get(state,state)
                try:
                    receipt=json.loads(result)
                    if isinstance(receipt,dict) and ('drc_count' in receipt or receipt.get('connection_failure_count')):
                        result=f'DRC {receipt.get("drc_count",0)}项；连接失败 {receipt.get("connection_failure_count",0)}项；ERROR矩形 {receipt.get("error_rectangles",0)}个。'+ ('问题位置已移除，正常部分已保留。' if receipt.get('partial_output') else '')
                except (ValueError,TypeError):pass
                for j, value in enumerate((package, cell, preview, state, result)):
                    item = W.QTableWidgetItem(str(value)[:4000])
                    item.setToolTip(('\n'.join(lines) if j==2 else str(value))[:16000])
                    if j==2:item.setData(role, payload)
                    self.table.setItem(i,j,item)
            state = {'imported': '已导入', 'running': '运行中', 'paused': '已暂停', 'blocked': '需处理', 'completed': '已完成'}.get(meta.get('state'), meta.get('state', ''))
            self.page_label.setText(f'{state} · 第{self.page + 1}/{max(1, (count + 199) // 200)}页 · 共{count}封装')
        except (OSError, ValueError, TypeError, sqlite3.Error):
            pass

    @staticmethod
    def die_text(pose):
        text = f'DIE{pose["die"]}: ΔX={pose["dx_um"]:g} μm, ΔY={pose["dy_um"]:g} μm, θ={pose["angle_deg"]:g}°'
        if 'rotation_center_um' in pose:
            text += f', 中心={pose["rotation_center_um"]} μm'
        return text

    def show_dies(self, row, column):
        if column != 2 or not self.table.item(row,column):return
        role = getattr(QtCore.Qt,'ItemDataRole',QtCore.Qt).UserRole
        payload = json.loads(self.table.item(row,column).data(role))
        dialog = W.QDialog(self); dialog.setWindowTitle('各DIE偏移设置'); dialog.resize(720,450)
        layout = W.QVBoxLayout(dialog); text = W.QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText('\n'.join(self.die_text(pose) for pose in payload['dies']))
        layout.addWidget(text)
        dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()

    def flush_conversation(self):
        lines = self.conversation.drain(80)
        if lines:self.conversation_view.appendPlainText('\n'.join(lines))

    def show_plans(self):
        if self.plan_panel is None:self.plan_panel = PlanPanel(self,self.plan_store)
        self.plan_panel.show(); self.plan_panel.raise_(); self.plan_panel.refresh()

    def create_plan(self):
        entries = self.file_entries()
        config = dict(folder=str(self.project_root) if self.project_root else '', project=self.project.text(),
                      files=entries, profile=copy.deepcopy(self.shared_profile), shared_mapping=self.mapping_mode,
                      controls={key:getattr(self,key).value() for key in ('dies','workers','memory','retries','timeout')})
        def create(stop,progress):
            progress(dict(text='保存计划快照 · 校验输入摘要',current=0,total=1))
            snapshot = capture_plan(config,stop)
            plan_id = self.plan_store.add(snapshot)
            return dict(plan_id=plan_id)
        self.execute(create, lambda value:self.show_plans(), operation='创建计划任务')

    def start_plans(self):
        self.show_plans()
        self.execute(lambda stop,progress:PlanRunner(self.plan_store,stop,progress,self.conversation).run(), operation='顺序执行计划任务')

    def closeEvent(self, event):
        if self.worker:
            self.pending_close = True
            self.stop_work()
            event.ignore()
        else:
            if self.project_root is not None:
                try:
                    self.persist()
                except (OSError, ValueError) as exc:
                    self.logger.error('关闭时项目设置未保存：%s', exc)
            self.conversation.close()
            self.logger.info('GUI closed')
            event.accept()


def launch():
    app = W.QApplication.instance() or W.QApplication([])
    app.setApplicationName('RealDataAnalyse')
    window = Window()
    window.show()
    return app.exec() if hasattr(app, 'exec') else app.exec_()
