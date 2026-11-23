"""Qt GUI; all IO/Excel/IPC work stays outside the GUI thread."""
import json
import copy
from contextlib import closing
from pathlib import Path
import threading
import time

from die_shift_studio.jobs import REALDATA_DIRECTORY
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W, Signal
from . import __version__
from .excel import DEFAULT_PROFILE, HEADERS, import_files
from .measurements import HEADINGS, SUFFIXES, folders, nominal_geometry
from .projects import load_folder, save_folder, inside, copy_excel, copy_inputs, template_file
from .runner import Runner, nominal_limits
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
    'output.configure': {'output_directory': '', 'max_threads': 0, 'max_memory_mb': 0},
    'preview.submit': {'dies': [{'die': 1, 'dx_um': 0, 'dy_um': 0, 'angle_deg': 0}]},
    'export.gds': {'filename': 'measured.gds'},
    'batch.begin': {'filename': 'measured.oas', 'output_policy': 'omit_failed_regions', 'nominal_bypass': False},
    'batch.submit': {'batch_id': '', 'cell': 'Package_000001', 'position_um': [0, 0], 'dies': [], 'use_nominal': False},
    'batch.end': {'batch_id': ''}, 'task.get': {'task_id': ''},
    'task.issues': {'task_id': '', 'offset': 0, 'limit': 128},
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
        self.start_time = None
        self.end_time = None
        self.elapsed = 0
        self.failed = False
        self.count_only = False

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
        label = self.label(fraction, show_times=not self.count_only)
        alignment = getattr(QtCore.Qt, 'AlignmentFlag', QtCore.Qt).AlignCenter
        elide = getattr(QtCore.Qt, 'TextElideMode', QtCore.Qt).ElideRight
        painter.drawText(rect.adjusted(8, 0, -8, 0), alignment, self.fontMetrics().elidedText(label, elide, rect.width() - 16))

    def label(self, fraction, *, show_times=True):
        percent = f'{round(fraction * 100)}% · ' if not self.count_only and self.maximum() > 1 else ''
        if self.start_time is None or not show_times:
            return percent + self.detail
        elapsed = int(time.monotonic() - self.started) if self.started is not None else self.elapsed
        started = time.strftime('%H:%M:%S', time.localtime(self.start_time))
        stopped = time.strftime('%H:%M:%S', time.localtime(self.end_time)) if self.end_time is not None else ''
        duration = f'{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}'
        times = f'开始 {started} · ' + (f'结束 {stopped} · ' if stopped else '')
        return f'{percent}{times}耗时 {duration} · {self.detail}'

    def begin(self):
        self.started = time.monotonic()
        self.start_time = time.time()
        self.end_time = None
        self.count_only = False

    def finish(self):
        self.end_time = time.time()
        self.elapsed = int(time.monotonic() - self.started)
        self.started = None
        self.status(self.detail)

    def status(self, text, current=None, total=None):
        self.detail = str(text).replace('\n', ' ')
        if current is not None:
            self.setRange(0, max(1, int(total or 1)))
            self.setValue(int(current))
        self.setToolTip(self.label(self.value() / max(1, self.maximum())))
        self.update()

    def package_status(self, completed, total, published):
        self.count_only = True
        fill = round(10000 * completed / max(1,total))
        self.status(f'已完成 {completed} / {total}', 10000 if published else min(fill, 9900), 10000)


class Window(W.QMainWindow):
    def __init__(self, plan_path=None):
        super().__init__()
        self.setWindowTitle('RealDataAnalyse ' + __version__)
        self.resize(1120, 860)
        self.worker = None
        self.completion_dialog = None
        self.pending_close = False
        self.page = 0
        self.project_root = None
        self.measurement = None
        self.excluded_excel = set()
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
        self.conversation = Conversation(message_limit=4000, display=False)
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
        self.template = self.path_row(form,'模板图纸','', '*.gds *.oas *.oasis','template', on_pick=self.select_template)
        self.template.setClearButtonEnabled(True)
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
        self.workers = self.spin(controls, '并发线程', 0, 64, 0)
        self.memory = self.spin(controls, '内存MB', 0, 1048576, 0)
        self.workers.setSpecialValueText('0（自动）')
        self.memory.setSpecialValueText('0（自动）')
        self.memory.setSingleStep(128)
        self.retries = self.spin(controls, '自动重试', 0, 10, 3)
        self.timeout = self.spin(controls, '任务超时秒', 0, 86400, 0)
        self.timeout.setSpecialValueText('0（无超时）')
        form.addRow(controls)
        bypass = W.QHBoxLayout()
        self.bypass_enabled = W.QCheckBox()
        self.bypass_enabled.setAccessibleName('启用忽略阈值')
        self.bypass_enabled.setToolTip('勾选后按 X、Y 和旋转角度过滤')
        bypass.addWidget(self.bypass_enabled)
        self.bypass_limits = []
        for label, maximum in (('X (μm)', 1e9), ('Y (μm)', 1e9), ('旋转 (°)', 360)):
            bypass.addWidget(W.QLabel(label))
            field = W.QDoubleSpinBox()
            field.setRange(0, maximum)
            field.setDecimals(3)
            field.setEnabled(False)
            field.setSingleStep(.1)
            field.setToolTip('封装内每颗DIE的绝对偏移和绝对旋转均严格小于对应阈值时，使用同一名义封装Cell。任一阈值为0时关闭。')
            bypass.addWidget(field)
            self.bypass_limits.append(field)
        form.addRow('忽略阈值', bypass)
        self.bypass_enabled.toggled.connect(self.sync_bypass_fields)
        self.ignore_measurement_failure = W.QCheckBox('忽略测量失败')
        self.ignore_measurement_failure.setEnabled(False)
        self.ignore_measurement_failure.setToolTip('对机台标记失败的芯片，采用该行的 X/Y 偏移与旋转角。适用于机台生产格式。')
        form.addRow(self.ignore_measurement_failure)
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
        self.profile = W.QTableWidget(0, 3)
        self.profile.setHorizontalHeaderLabels(['数据字段', 'Excel列名', '要求 / 单位'])
        self.profile.verticalHeader().show()
        self.profile.horizontalHeader().setStretchLastSection(True)
        self.profile.setColumnWidth(0, 220)
        self.profile.setColumnWidth(1, 250)
        self.set_profile(DEFAULT_PROFILE)
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
        self.conversation_tab = conversation_tab
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
        conversation_tab.installEventFilter(self)

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

    def path_row(self, form, label, default, pattern, key, save=False, readonly=False, on_pick=None):
        row = W.QHBoxLayout()
        edit = W.QLineEdit(str(self.settings.value(key, default) if key == 'endpoint' else default))
        edit.setReadOnly(readonly)
        button = W.QPushButton('浏览…')
        def browse():
            dialog = W.QFileDialog.getSaveFileName if save else W.QFileDialog.getOpenFileName
            path, _ = dialog(self, label, edit.text(), f'文件 ({pattern});;所有文件 (*)')
            if path:
                if on_pick: on_pick(path)
                else: edit.setText(path)
        button.clicked.connect(browse)
        row.addWidget(edit, 1)
        if not readonly:
            row.addWidget(button)
        self.path_buttons = getattr(self, 'path_buttons', []) + [button]
        form.addRow(label, row)
        if key == 'endpoint':
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
            if message.get('package_count'):
                self.progress.package_status(message['current'], message['total'], message['published'])
                return
            if not self.progress.count_only:
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
        if not self.progress.count_only:
            self.progress.status(text)

    def sync_bypass_fields(self):
        for field in self.bypass_limits:
            field.setEnabled(self.bypass_enabled.isEnabled() and self.bypass_enabled.isChecked())

    def busy(self, on):
        for widget in (self.connect_button, self.start_button, self.add_files, self.remove_files,
                       self.folder_button, self.import_button, self.create_plan_button, self.run_button, self.send_button,
                       self.endpoint, self.project, self.database, self.output, self.template, self.profile,
                       self.workers, self.memory, self.retries, self.timeout, self.file_view, self.sheet, self.header_row, self.mapping_shared,
                       *self.path_buttons, self.bypass_enabled):
            widget.setEnabled(not on)
        self.sync_bypass_fields()
        self.dies.setEnabled(not on and not self.measurement)
        self.ignore_measurement_failure.setEnabled(not on and bool(self.measurement))
        self.stop_button.setEnabled(on)

    def execute(self, fn, callback=None, failure=None, operation='操作', count_only=False):
        if self.worker:
            return
        self.operation = operation
        self.busy(True)
        self.progress.failed = False
        self.progress.begin()
        if count_only:
            self.progress.package_status(0, 0, False)
        else:
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
                if count_only:
                    self.progress.failed = isinstance(value, dict) and value.get('state') == 'completed_with_errors'
                elif isinstance(value, dict) and value.get('state') == 'paused':
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
                if not count_only:
                    self.progress.status(operation + '停止 · ' + str(exc))
                if failure:
                    failure(str(exc))
        worker.done.connect(result)
        def finished():
            self.progress.finish()
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
        if info.get('die_count') and not self.measurement:
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
            configure_logging(config['workspace'])
            self.conversation.set_root(config['workspace'])
            if link:
                try:
                    config['info'] = self.linked_project(endpoint, config['project'], stop, progress, timeout, config['output'])
                except Exception as exc:
                    config['connection_error'] = str(exc)
            return config
        def done(config):
            self.project_root = Path(config['folder'])
            self.measurement = config.get('measurement')
            self.excluded_excel = set(map(Path,config['excluded_excel']))
            self.folder.setText(config['folder'])
            self.project.setText(config['project'])
            self.template.setText(config['template'])
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
            for name, default in (('dies', 3), ('workers', 0), ('memory', 0), ('retries', 3), ('timeout', 0)):
                getattr(self, name).setValue(int(config['controls'].get(name, default)))
            for field, value in zip(self.bypass_limits, nominal_limits(config['controls'].get('bypass_limits',(0,0,0)))):
                field.setValue(value)
            self.bypass_enabled.setChecked(config['controls'].get('bypass_enabled') is True)
            self.ignore_measurement_failure.setChecked(config['controls'].get('ignore_measurement_failure') is True)
            self.logger = configure_logging(config['workspace'])
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
        fields = HEADINGS if self.measurement else HEADERS
        descriptions = (('整数', '整数', '0 / 1', '全局理论中心X', '全局理论中心Y',
                         '全局实际中心X', '全局实际中心Y', '原始偏移X', '原始偏移Y', '度')
                        if self.measurement else
                        ('文本', '从1起的整数', 'μm', 'μm', '度', 'μm', 'μm', '可选 / μm', '可选 / μm'))
        self.mapping_shared.setText('Excel列映射（全局坐标）' if self.measurement else 'Excel列映射（长度μm，角度°；中心留空沿用工程）')
        self.profile.setRowCount(len(fields))
        for row, (field, description) in enumerate(zip(fields, descriptions)):
            for column, text in enumerate((field, profile['columns'].get(field, field), description)):
                item = W.QTableWidgetItem(text)
                if column != 1:
                    item.setFlags(item.flags() & ~getattr(QtCore.Qt, 'ItemFlag', QtCore.Qt).ItemIsEditable)
                self.profile.setItem(row, column, item)
        self.mapping_guard = False

    def profile_value(self):
        columns = {self.profile.item(row, 0).text(): self.profile.item(row, 1).text().strip() for row in range(self.profile.rowCount())}
        return dict(sheet=self.sheet.text().strip(), header_row=self.header_row.value(), columns=columns)

    def controls(self):
        return {**{key:getattr(self,key).value() for key in ('dies','workers','memory','retries','timeout')},
                'bypass_limits':[field.value() for field in self.bypass_limits],
                'bypass_enabled':self.bypass_enabled.isChecked(),
                'ignore_measurement_failure':self.ignore_measurement_failure.isChecked()}

    def persist(self):
        if self.project_root is None:
            raise ValueError('请先选择项目文件夹')
        files = self.file_entries()
        profile = copy.deepcopy(self.shared_profile)
        save_folder(self.project_root, self.project.text(), files, self.database.text(), self.output.text(), profile,
                    self.controls(), shared_mapping=self.mapping_mode, template=self.template.text().strip(),
                    measurement=self.measurement, excluded_excel=self.excluded_excel)
        return files, profile

    def select_template(self,path):
        if self.project_root is None:
            self.log('请先选择项目文件夹')
            return
        root = self.project_root
        def copy(stop, progress):
            target, = copy_inputs(root,[path],stop,'Template',('.gds','.oas','.oasis'))
            return str(template_file(root,target))
        self.execute(copy,self.template.setText,operation='复制项目模板图纸')

    def select_files(self):
        if self.project_root is None:
            self.log('请先选择项目文件夹')
            return
        pattern = '测量表 (*.xlsx *.csv)' if self.measurement else 'Excel (*.xlsx)'
        files, _ = W.QFileDialog.getOpenFileNames(self, '添加各DIE测量表', str(self.project_root), pattern)
        if not files:
            return
        entries = self.file_entries()
        existing = {entry['path'] for entry in entries}
        def done(paths):
            for path in paths:
                self.excluded_excel.discard(Path(path))
                if path not in existing:
                    entries.append(dict(path=path, enabled=True))
                    existing.add(path)
            self.set_files(entries)
            self.persist()
        root = self.project_root
        if self.measurement:
            dies = [path.name for path in folders(self.measurement['root'])]
            name, ok = W.QInputDialog.getItem(self, '选择芯片目录', '芯片名称', dies, 0, False)
            if not ok:
                return
            destination = (Path(self.measurement['root']) / name).relative_to(root / REALDATA_DIRECTORY)
            add = lambda stop, progress: copy_inputs(root, files, stop, destination, SUFFIXES)
        else:
            add = lambda stop, progress: copy_excel(root, files, stop)
        self.execute(add, done, operation='添加Excel')

    def delete_files(self):
        entries = self.file_entries()
        for row in sorted({index.row() for index in self.file_view.selectedIndexes()}, reverse=True):
            self.excluded_excel.add(Path(entries[row]['path']))
            self.file_view.removeRow(row)
        try:
            self.persist()
        except ValueError as exc:
            self.log(exc)

    def do_import(self):
        try:
            files, profile = self.persist()
            workspace = inside(self.project_root, REALDATA_DIRECTORY)
            target = inside(workspace, 'Tasks/measurements.sqlite')
            output = inside(workspace, Path('Output') / f'{self.project_root.name}.oas')
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
            if value.get('info'):
                self.accept_info(value.pop('info'))
            self.database.setText(value['database'])
            self.output.setText(str(output))
            self.page = 0
            for warning in value.get('warnings', ()):
                self.log_view.appendPlainText('导入警告 · ' + warning)
            self.persist()
        project, measurement = self.project.text(), self.measurement
        ignore_failure = bool(measurement) and self.ignore_measurement_failure.isChecked()
        endpoint, timeout = self.endpoint.text(), self.timeout.value()
        def prepare(stop, progress):
            geometry = None
            if measurement:
                info = self.linked_project(endpoint, project, stop, progress, timeout, str(output))
                geometry = nominal_geometry(info)
            result = import_files(files, target, dies, workers, stop, progress, profile, profiles,
                                  measurement=measurement, geometry=geometry, ignore_measurement_failure=ignore_failure)
            if measurement:
                result['info'] = info
            return result
        self.execute(prepare, done, operation='校验Excel并创建任务')

    def do_run(self):
        try:
            self.persist()
        except (OSError, ValueError) as exc:
            self.log(exc)
            return
        kwargs = dict(database=self.database.text(), endpoint=self.endpoint.text(), project=self.project.text(), output=self.output.text(),
                      template=self.template.text().strip(), workers=self.workers.value(), memory_mb=self.memory.value(),
                      retries=self.retries.value(), timeout=self.timeout.value(),
                      bypass_limits=[field.value() for field in self.bypass_limits] if self.bypass_enabled.isChecked() else (0,0,0))
        entries = self.file_entries()
        selected = sorted(p['path'] for p in entries if p['enabled'])
        profile = copy.deepcopy(self.shared_profile)
        profiles = self.import_profiles(entries) or {}
        def run(stop, progress):
            import sqlite3
            with closing(sqlite3.connect(Path(kwargs['database']).resolve().as_uri() + '?mode=ro', uri=True)) as db:
                meta = {k: json.loads(v) for k, v in db.execute('select key,value from meta')}
            progress(dict(current=0, total=meta.get('count',0), package_count=True, published=False))
            if (selected != sorted(p['path'] for p in meta.get('sources', [])) or profile != meta.get('profile')
                    or profiles != meta.get('profiles', {}) or self_die != meta.get('die_count')
                    or measurement != meta.get('measurement')
                    or ignore_failure != meta.get('ignore_measurement_failure',False)):
                raise ValueError('Excel勾选、列映射、DIE数量或测量失败选项与任务记录不符，请先创建新任务')
            from .storage import sha256
            for index, source in enumerate(meta.get('sources', []), 1):
                progress(dict(text=f'核对Excel输入 · {index}/{len(selected)} · {Path(source["path"]).name}', current=index - 1, total=len(selected)))
                if sha256(source['path'], stop) != source['sha256']:
                    raise ValueError('Excel内容已修改，请先校验并创建新任务')
            return Runner(**kwargs, stop=stop, progress=progress, trace=self.conversation).run()
        self_die = self.dies.value()
        measurement = self.measurement
        ignore_failure = bool(measurement) and self.ignore_measurement_failure.isChecked()
        def done(value):
            if value['state'] != 'completed' or self.pending_close:
                return
            if self.completion_dialog is None:
                dialog = W.QDialog(self)
                dialog.setWindowTitle('任务完成')
                dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
                layout = W.QVBoxLayout(dialog)
                layout.addWidget(W.QLabel('最终 OAS 已保存'))
                output_path = W.QLineEdit()
                output_path.setReadOnly(True)
                output_path.setMinimumWidth(340)
                layout.addWidget(output_path)
                buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok)
                buttons.button(W.QDialogButtonBox.StandardButton.Ok).setText('确定')
                buttons.accepted.connect(dialog.accept)
                layout.addWidget(buttons)
                self.completion_dialog = dialog
                self.completion_path = output_path
            self.completion_path.setText(value['output'])
            self.completion_dialog.show()
        self.execute(run, done, operation='批量计算与OAS输出', count_only=True)

    def command_changed(self, command):
        args = dict(COMMANDS[command])
        if command == 'project.open':
            args['path'] = self.project.text()
        if command == 'output.configure':
            if self.project_root is not None:
                args['output_directory'] = str(self.project_root / REALDATA_DIRECTORY / 'Cache' / 'Manual')
            args.update(max_threads=self.workers.value(), max_memory_mb=self.memory.value())
        if command in ('preview.submit', 'batch.submit'):
            args['dies'] = [dict(die=i + 1, dx_um=0, dy_um=0, angle_deg=0) for i in range(self.dies.value())]
        if command=='batch.begin' and self.template.text().strip():
            args['template_path']=self.template.text().strip()
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
                cache = inside(self.project_root, f'{REALDATA_DIRECTORY}/Cache')
                directory = inside(cache, args['output_directory'])
                directory.mkdir(parents=True, exist_ok=True)
                args['output_directory'] = str(directory)
        except (OSError, ValueError) as exc:
            self.log(exc)
            return
        if command=='batch.begin' and args.get('template_path'):
            try:
                if self.project_root is None:
                    raise ValueError('请先选择项目文件夹')
                args['template_path']=str(template_file(self.project_root,args['template_path']))
            except (ValueError,OSError) as exc:
                self.log(exc)
                return
        endpoint, should_wait, timeout = self.endpoint.text(), self.wait_check.isChecked(), self.timeout.value()
        bound_instance = self.bindings.get(str(Path(endpoint).resolve()))
        expected_project = self.project.text()
        def send(stop, progress):
            if command=='batch.begin' and args.get('template_path') and 'template_sha256' not in args:
                from .storage import sha256
                args['template_sha256']=sha256(args['template_path'],stop)
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
                names = meta.get('die_names', [])
                lines = [self.die_text(pose, names) for pose in poses]
                preview = '; '.join(lines[:20]) + (f' … 共{len(lines)}颗DIE，双击展开' if len(lines)>20 else '')
                state={'computed_with_issues':'已计算（含问题）','committed_with_issues':'已输出（含问题）','committed':'已输出'}.get(state,state)
                try:
                    receipt=json.loads(result)
                    if isinstance(receipt,dict) and receipt.get('use_nominal'):
                        result = '原图直出 · 共享名义封装Cell'
                    elif isinstance(receipt,dict) and ('drc_count' in receipt or receipt.get('connection_failure_count')):
                        serious = receipt.get('error2_rectangles', 0)
                        ordinary = receipt.get('error_rectangles', 0) - serious
                        result = (f'DRC {receipt.get("drc_count",0)}项 · 连接失败 {receipt.get("connection_failure_count",0)}项 · '
                                  f'ERROR矩形 {ordinary}个 · ERROR2矩形 {serious}个 · FAIL封装 {receipt.get("failed_packages",0)}个'
                                  + ('。问题位置已移除，正常部分已保留。' if receipt.get('partial_output') else ''))
                except (ValueError,TypeError):pass
                for j, value in enumerate((package, cell, preview, state, result)):
                    item = W.QTableWidgetItem(str(value)[:4000])
                    item.setToolTip(('\n'.join(lines) if j==2 else str(value))[:16000])
                    if j==2:item.setData(role, '\n'.join(lines))
                    self.table.setItem(i,j,item)
            state = {'imported': '已导入', 'running': '运行中', 'paused': '已暂停', 'blocked': '需处理', 'completed': '已完成'}.get(meta.get('state'), meta.get('state', ''))
            skipped = meta.get('skipped_packages', 0)
            warnings = meta.get('import_warning_count', 0)
            self.page_label.setText(f'{state} · 第{self.page + 1}/{max(1, (count + 199) // 200)}页 · 共{count}封装'
                                    + (f' · 跳过{skipped}封装' if skipped else '')
                                    + (f' · {warnings}条导入警告' if warnings else ''))
        except (OSError, ValueError, TypeError, sqlite3.Error):
            pass

    @staticmethod
    def die_text(pose, names=()):
        die = pose['die']
        label = names[die-1] if 0 < die <= len(names) else f'DIE{die}'
        text = f'{label}: ΔX={pose["dx_um"]:g} μm, ΔY={pose["dy_um"]:g} μm, θ={pose["angle_deg"]:g}°'
        if 'rotation_center_um' in pose:
            text += f', 中心={pose["rotation_center_um"]} μm'
        return text

    def show_dies(self, row, column):
        if column != 2 or not self.table.item(row,column):return
        role = getattr(QtCore.Qt,'ItemDataRole',QtCore.Qt).UserRole
        poses = self.table.item(row,column).data(role)
        dialog = W.QDialog(self); dialog.setWindowTitle('各DIE偏移设置'); dialog.resize(720,450)
        layout = W.QVBoxLayout(dialog); text = W.QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(poses)
        layout.addWidget(text)
        dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'conversation_tab', None):
            kinds = getattr(QtCore.QEvent, 'Type', QtCore.QEvent)
            if event.type() == kinds.Show:
                self.conversation.set_display(True)
                self.conversation_timer.start()
            elif event.type() == kinds.Hide:
                self.conversation_timer.stop()
                self.conversation.set_display(False)
        return super().eventFilter(obj, event)

    def flush_conversation(self):
        lines = self.conversation.drain(80)
        if lines:self.conversation_view.appendPlainText('\n'.join(lines))

    def show_plans(self):
        if self.plan_panel is None:self.plan_panel = PlanPanel(self,self.plan_store)
        self.plan_panel.show(); self.plan_panel.raise_(); self.plan_panel.refresh()

    def create_plan(self):
        entries = self.file_entries()
        config = dict(folder=str(self.project_root) if self.project_root else '', project=self.project.text(),
                      files=entries, template=self.template.text().strip(), profile=copy.deepcopy(self.shared_profile), shared_mapping=self.mapping_mode,
                      measurement=self.measurement,
                      controls=self.controls())
        def create(stop,progress):
            progress(dict(text='保存计划快照 · 校验输入摘要',current=0,total=1))
            snapshot = capture_plan(config,stop)
            plan_id = self.plan_store.add(snapshot)
            return dict(plan_id=plan_id)
        self.execute(create, lambda value:self.show_plans(), operation='创建计划任务')

    def start_plans(self):
        self.show_plans()
        workspace = self.project_root / REALDATA_DIRECTORY if self.project_root is not None else installation()
        def run(stop, progress):
            try:
                return PlanRunner(self.plan_store, stop, progress, self.conversation).run()
            finally:
                configure_logging(workspace)
                self.conversation.set_root(workspace)
        self.execute(run, operation='顺序执行计划任务', count_only=True)

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
