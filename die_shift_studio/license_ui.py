"""Activation and permanent-license status, sharing the application's Qt theme."""
from . import __version__
from .license_core import Authorization, LicenseError, embedded_public_key, license_path, machine_id
from .qt_compat import QtCore, QtWidgets as W, Signal, run_dialog, ACCEPTED
from .style import STYLE


class HardwareQuery(QtCore.QThread):
    resolved = Signal(object, str)

    def run(self):
        try:
            self.resolved.emit(Authorization(machine_id(), embedded_public_key(), license_path()), '')
        except (LicenseError, OSError) as exc:
            self.resolved.emit(None, str(exc))


class LicenseDialog(W.QDialog):
    def __init__(self, parent=None, authorization=None):
        super().__init__(parent)
        self.authorization = authorization
        self.selected = None
        self.query = None
        self.setWindowTitle(f'Die Shift Analyze {__version__} · 授权管理')
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(560)
        layout = W.QFormLayout(self)
        self.status = W.QLabel('正在读取本机硬件信息…')
        self.status.setWordWrap(True)
        self.machine = W.QLineEdit(readOnly=True)
        layout.addRow('Machine ID', self.machine)
        layout.addRow(self.status)
        row = W.QHBoxLayout()
        self.copy = W.QPushButton('复制 Machine ID')
        self.import_btn = W.QPushButton('导入许可证')
        self.apply_btn = W.QPushButton('激活')
        self.exit_btn = W.QPushButton('关闭' if parent else '退出')
        for button in (self.copy,self.import_btn,self.apply_btn,self.exit_btn):
            row.addWidget(button)
        layout.addRow(row)
        self.copy.clicked.connect(lambda:W.QApplication.clipboard().setText(self.machine.text()))
        self.import_btn.clicked.connect(self.pick)
        self.apply_btn.clicked.connect(self.apply)
        self.exit_btn.clicked.connect(self.reject)
        self.apply_btn.setEnabled(False)
        self.copy.setEnabled(False)
        self.import_btn.setEnabled(False)
        if authorization is None:
            self.query = HardwareQuery(self)
            self.query.resolved.connect(self.validated_hardware)
            self.query.start()
        else:
            self.validated_hardware(authorization,'')

    def validated_hardware(self, authorization, error):
        self.authorization = authorization
        if authorization is None:
            self.status.setText(error)
            return
        self.machine.setText(authorization.machine_id)
        self.copy.setEnabled(True)
        self.import_btn.setEnabled(True)
        try:
            payload = authorization.check()
        except LicenseError as exc:
            self.status.setText(f'{exc} 请将 Machine ID 发给管理员。')
            return
        if self.parent() is None:
            self.accept()
        else:
            self.status.setText(f"永久授权 · {payload['user_name']} · {payload['employee_id']}\n授权编号 {payload['license_id']}")

    def pick(self):
        path, _ = W.QFileDialog.getOpenFileName(self,'导入许可证','','许可证 (*.lic)')
        if path:
            self.selected = path
            self.apply_btn.setEnabled(True)
            self.apply()

    def apply(self):
        if not self.selected or self.authorization is None:
            return
        try:
            self.authorization.activate(self.selected)
        except LicenseError as exc:
            self.status.setText(str(exc))
            return
        self.accept()


def require_activation():
    app = W.QApplication.instance() or W.QApplication([])
    dialog = LicenseDialog()
    result = run_dialog(dialog)
    if dialog.query is not None:
        dialog.query.wait()
    return (app,dialog.authorization) if result==ACCEPTED else (app,None)
