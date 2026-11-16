"""Qt GUI; all IO/Excel/IPC work stays outside the GUI thread."""
import json
from contextlib import closing
from pathlib import Path
import threading
import time

from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W, Signal
from . import __version__
from .excel import DEFAULT_PROFILE, generate_example, import_files
from .runner import Runner
from .storage import Ledger, configure_logging
from .transport import Transport, default_endpoint, installation

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
    message = Signal(str)
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


class Window(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('RealDataAnalyse ' + __version__)
        self.resize(1120, 860)
        self.worker = None
        self.pending_close = False
        self.page = 0
        self.files = []
        self.settings = QtCore.QSettings('DieShiftAnalyze', 'RealDataAnalyse')
        self.logger = configure_logging(installation())
        self.logger.info('RealDataAnalyse %s started', __version__)
        base = W.QWidget()
        self.setCentralWidget(base)
        layout = W.QVBoxLayout(base)
        title = W.QLabel('RealDataAnalyse  ·  实测封装批量输出')
        title.setStyleSheet('font-size:20px; font-weight:600; padding:6px')
        layout.addWidget(title)
        connection = W.QGroupBox('连接与工程')
        form = W.QFormLayout(connection)
        self.endpoint = self.path_row(form, 'IPC入口', str(default_endpoint()), '*.json', 'endpoint')
        self.project = self.path_row(form, 'Die Shift Analyze工程', '', '*.json', 'project')
        layout.addWidget(connection)
        buttons = W.QHBoxLayout()
        self.connect_button = W.QPushButton('连接 / 查询状态')
        self.start_button = W.QPushButton('启动同目录软件')
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.start_button)
        self.connection_label = W.QLabel('先在 Die Shift Analyze 开启“实测输出”')
        buttons.addWidget(self.connection_label, 1)
        layout.addLayout(buttons)
        tabs = W.QTabWidget()
        layout.addWidget(tabs, 1)
        batch = W.QWidget()
        batch_scroll = W.QScrollArea()
        batch_scroll.setWidgetResizable(True)
        batch_scroll.setWidget(batch)
        tabs.addTab(batch_scroll, 'Excel批量 / 故障恢复')
        batch_layout = W.QVBoxLayout(batch)
        row = W.QHBoxLayout()
        self.add_files = W.QPushButton('选择各DIE Excel')
        self.example = W.QPushButton('生成GD55合成示例')
        self.file_label = W.QLabel('未选择文件；一个Excel对应一颗DIE')
        row.addWidget(self.add_files)
        row.addWidget(self.example)
        row.addWidget(self.file_label, 1)
        batch_layout.addLayout(row)
        self.file_view = W.QPlainTextEdit()
        self.file_view.setReadOnly(True)
        self.file_view.setMaximumHeight(50)
        batch_layout.addWidget(self.file_view)
        settings_form = W.QFormLayout()
        batch_layout.addLayout(settings_form)
        self.database = self.path_row(settings_form, '任务记录 / 继续已有任务', '', '*.sqlite', 'database')
        self.output = self.path_row(settings_form, '最终OAS', str(installation() / 'RealDataAnalyse-output' / 'lot.oas'), '*.oas', 'output', save=True)
        controls = W.QHBoxLayout()
        self.dies = self.spin(controls, 'DIE数量', 1, 10000, 3)
        self.workers = self.spin(controls, '并发线程', 1, 64, 4)
        self.memory = self.spin(controls, '内存MB', 128, 1048576, 2048)
        self.retries = self.spin(controls, '自动重试', 0, 10, 3)
        self.timeout = self.spin(controls, '任务超时秒', 10, 86400, 900)
        settings_form.addRow(controls)
        self.profile = W.QPlainTextEdit(json.dumps(DEFAULT_PROFILE, ensure_ascii=False, indent=2))
        self.profile.setMaximumHeight(95)
        profile_box = W.QGroupBox('Excel列映射（长度μm，角度°；中心留空沿用工程）')
        profile_layout = W.QVBoxLayout(profile_box)
        profile_layout.addWidget(self.profile)
        batch_layout.addWidget(profile_box)
        controls = W.QHBoxLayout()
        self.import_button = W.QPushButton('校验并创建任务')
        self.run_button = W.QPushButton('运行 / 继续补算')
        self.stop_button = W.QPushButton('安全停止')
        self.stop_button.setEnabled(False)
        self.refresh_button = W.QPushButton('刷新记录')
        for widget in (self.import_button, self.run_button, self.stop_button, self.refresh_button):
            controls.addWidget(widget)
        batch_layout.addLayout(controls)
        self.progress = W.QProgressBar()
        self.progress.setFormat('尚未开始 %p%')
        batch_layout.addWidget(self.progress)
        self.summary = W.QLabel('未提交的批次会完整重放；几何失败保留明细，修正后新建任务。')
        batch_layout.addWidget(self.summary)
        self.table = W.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['序号', '封装ID', 'Cell', '状态', '任务/错误'])
        self.table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers if hasattr(W.QAbstractItemView, 'EditTrigger') else W.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((60, 180, 210, 100)):
            self.table.setColumnWidth(col, width)
        self.table.setMinimumHeight(150)
        batch_layout.addWidget(self.table, 1)
        paging = W.QHBoxLayout()
        prev = W.QPushButton('上一页')
        nxt = W.QPushButton('下一页')
        paging.addWidget(prev)
        paging.addWidget(nxt)
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
        self.log_view = W.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setMaximumHeight(115)
        layout.addWidget(self.log_view)
        scope = W.QLabel('批量OAS执行几何/连接检查，跳过完整DRC及概率；完成不代表生产签核。日志在程序旁 logs。')
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.connect_button.clicked.connect(self.connect_server)
        self.start_button.clicked.connect(self.start_server)
        self.add_files.clicked.connect(self.select_files)
        self.example.clicked.connect(self.make_example)
        self.import_button.clicked.connect(self.do_import)
        self.run_button.clicked.connect(self.do_run)
        self.stop_button.clicked.connect(self.stop_work)
        self.send_button.clicked.connect(self.send)
        self.refresh_button.clicked.connect(self.refresh)
        self.command.currentTextChanged.connect(self.command_changed)
        prev.clicked.connect(lambda: self.change_page(-1))
        nxt.clicked.connect(lambda: self.change_page(1))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def path_row(self, form, label, default, pattern, key, save=False):
        row = W.QHBoxLayout()
        edit = W.QLineEdit(str(self.settings.value(key, default)))
        button = W.QPushButton('浏览…')
        def browse():
            dialog = W.QFileDialog.getSaveFileName if save else W.QFileDialog.getOpenFileName
            path, _ = dialog(self, label, edit.text(), f'文件 ({pattern});;所有文件 (*)')
            if path:
                edit.setText(path)
        button.clicked.connect(browse)
        row.addWidget(edit, 1)
        row.addWidget(button)
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
        self.log_view.appendPlainText(time.strftime('%H:%M:%S ') + str(message))

    def busy(self, on):
        for button in (self.connect_button, self.start_button, self.add_files, self.example,
                       self.import_button, self.run_button, self.send_button):
            button.setEnabled(not on)
        for edit in (self.endpoint, self.project, self.database, self.output, self.dies, self.profile,
                     self.workers, self.memory, self.retries, self.timeout):
            edit.setEnabled(not on)
        self.stop_button.setEnabled(on)

    def execute(self, fn, callback=None):
        if self.worker:
            return
        self.busy(True)
        worker = Worker(fn, self)
        self.worker = worker
        worker.message.connect(self.log)
        def result(value, error):
            if error:
                self.log(error)
                self.logger.error('%s', error)
                self.response.setPlainText(error)
            else:
                self.response.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
                if callback:
                    callback(value)
                self.log('操作完成')
        worker.done.connect(result)
        def finished():
            self.worker = None
            self.busy(False)
            self.refresh()
            worker.deleteLater()
            if self.pending_close:
                self.close()
        worker.finished.connect(finished)
        worker.start()

    def connect_server(self):
        endpoint = self.endpoint.text()
        def connected(response):
            info = response['result']
            self.connection_label.setText(f'ready={info.get("ready")}  DIE={info.get("die_count")}  工程={info.get("project")}')
            if info.get('project'):
                self.project.setText(info['project'])
                self.settings.setValue('project', info['project'])
            if info.get('die_count'):
                self.dies.setValue(info['die_count'])
            self.info = info
        self.execute(lambda stop, progress: Transport(endpoint).request('info', stop=stop), connected)

    def start_server(self):
        endpoint, project = self.endpoint.text(), self.project.text()
        def start(stop, progress):
            t = Transport(endpoint)
            try:
                return t.request('info', stop=stop, retries=0)
            except (OSError, EOFError, TimeoutError):
                pid = t.launch(project)
                progress(f'已启动同目录程序 PID {pid}，等待工程准备')
                return dict(result=t.wait_ready(stop))
        self.execute(start)

    def select_files(self):
        files, _ = W.QFileDialog.getOpenFileNames(self, '选择每颗DIE对应的Excel', '', 'Excel (*.xlsx)')
        if files:
            self.files = files
            self.file_label.setText(f'{len(files)} 个Excel')
            self.file_view.setPlainText('\n'.join(files))

    def make_example(self):
        folder = W.QFileDialog.getExistingDirectory(self, '合成示例输出目录')
        if not folder:
            return
        dies = self.dies.value()
        bounds = getattr(self, 'info', {}).get('board_bounds_um')
        pitch = (bounds[2] - bounds[0] + 100, bounds[3] - bounds[1] + 100) if bounds else (12000, 12000)
        def done(files):
            self.files = files
            self.file_label.setText(f'{len(files)} 个合成示例Excel')
            self.file_view.setPlainText('\n'.join(files))
        self.execute(lambda stop, progress: generate_example(folder, die_count=dies, pitch=pitch), done)

    def do_import(self):
        target, _ = W.QFileDialog.getSaveFileName(self, '创建新的任务数据库', str(installation() / 'RealDataAnalyse-run.sqlite'), '任务 (*.sqlite)')
        if not target:
            return
        try:
            profile = json.loads(self.profile.toPlainText())
        except ValueError as exc:
            self.log(str(exc))
            return
        files, dies, workers = list(self.files), self.dies.value(), self.workers.value()
        def done(value):
            self.database.setText(value['database'])
            self.settings.setValue('database', value['database'])
            self.page = 0
        self.execute(lambda stop, progress: import_files(files, target, dies, workers, stop, progress, profile), done)

    def do_run(self):
        kwargs = dict(database=self.database.text(), endpoint=self.endpoint.text(), project=self.project.text(), output=self.output.text(),
                      workers=self.workers.value(), memory_mb=self.memory.value(), retries=self.retries.value(), timeout=self.timeout.value())
        for key in ('database', 'endpoint', 'project', 'output'):
            self.settings.setValue(key, kwargs[key])
        self.execute(lambda stop, progress: Runner(**kwargs, stop=stop, progress=progress).run())

    def command_changed(self, command):
        args = dict(COMMANDS[command])
        if command == 'project.open':
            args['path'] = self.project.text()
        if command == 'output.configure':
            args.update(output_directory=str(Path(self.output.text()).parent), max_threads=self.workers.value(), max_memory_mb=self.memory.value())
        if command in ('preview.submit', 'batch.submit'):
            args['dies'] = [dict(die=i + 1, dx_um=0, dy_um=0, angle_deg=0) for i in range(self.dies.value())]
        if command in ('batch.submit', 'batch.end'):
            args['batch_id'] = getattr(self, 'manual_batch', '')
        if command == 'task.get':
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
        endpoint, should_wait, timeout = self.endpoint.text(), self.wait_check.isChecked(), self.timeout.value()
        def send(stop, progress):
            t = Transport(endpoint)
            response = t.request(command, args, stop=stop)
            if should_wait and response.get('task_id'):
                response = dict(response, completed_result=t.wait_task(response['task_id'], stop, timeout))
            return response
        def done(response):
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
                counts = dict(db.execute('select state,count(*) from packages group by state'))
                rows = db.execute('select seq,package,cell,state,coalesce(result,task,\'\') from packages order by seq limit 200 offset ?', (self.page * 200,)).fetchall()
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    item = W.QTableWidgetItem(str(value)[:4000])
                    item.setToolTip(str(value)[:16000])
                    self.table.setItem(i, j, item)
            done = counts.get('computed', 0) + counts.get('committed', 0)
            self.progress.setRange(0, max(1, count))
            self.progress.setValue(done)
            self.progress.setFormat(f'{meta.get("state")}  {done}/{count}  %p%')
            self.summary.setText(f'第{self.page + 1}页 · 每页200条 · {counts}（computed尚未整批提交）')
        except (OSError, ValueError, sqlite3.Error):
            pass

    def closeEvent(self, event):
        if self.worker:
            self.pending_close = True
            self.stop_work()
            event.ignore()
        else:
            self.logger.info('GUI closed')
            event.accept()


def launch():
    app = W.QApplication.instance() or W.QApplication([])
    app.setApplicationName('RealDataAnalyse')
    window = Window()
    window.show()
    return app.exec() if hasattr(app, 'exec') else app.exec_()
