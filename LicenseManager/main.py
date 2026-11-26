"""Standalone Windows administrator UI."""
import sqlite3
import sys
from pathlib import Path

from die_shift_studio.license_core import LicenseError, PRODUCT, load_public_key, local_directory
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W, run_dialog, ACCEPTED
from die_shift_studio.style import STYLE
from LicenseManager.manager import History, FIELDS, fingerprint, generate, load_private, new_file, public_bytes


class Manager(W.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Die Shift Analyze · License Manager')
        self.setStyleSheet(STYLE)
        self.resize(740, 510)
        self.home = local_directory('DieShiftLicenseManager')
        self.key = None
        self.key_status = W.QLineEdit('未加载私钥',readOnly=True)
        self.key_status.setToolTip('加载后显示 Ed25519 公钥 SHA-256 指纹。')
        self.data = W.QLineEdit(str(self.home))
        admin_folder = Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent
        self.universal_dir = admin_folder
        self.output = W.QLineEdit(str(self.home/'licenses'))
        self.name = W.QLineEdit()
        self.employee = W.QLineEdit()
        self.machine = W.QLineEdit()
        self.notes = W.QLineEdit()
        layout = W.QFormLayout(self)
        layout.addRow(PRODUCT, W.QLabel('Permanent License'))
        layout.addRow('数据目录',self.data)
        layout.addRow('签发目录',self.output)
        layout.addRow('私钥状态 / 指纹',self.key_status)
        layout.addRow(self.buttons(('生成密钥对',self.generate_key),('加载私钥',self.open_key),
                                   ('导出公钥',self.export_key),('校验公钥',self.check_key)))
        for title,field in (('姓名',self.name),('工号',self.employee),('Machine ID',self.machine),('备注',self.notes)):
            layout.addRow(title,field)
        layout.addRow(self.buttons(('签发许可证',self.issue),('打开输出目录',self.open_folder),('授权历史',self.history)))
        layout.addRow(self.buttons(('首次部署：生成管理员通用许可证',self.issue_universal)))
        self.status = W.QLabel()
        self.status.setWordWrap(True)
        layout.addRow(self.status)

    def buttons(self,*items):
        row = W.QHBoxLayout()
        for label, action in items:
            button = W.QPushButton(label)
            button.clicked.connect(lambda checked=False,f=action:self.do(f))
            row.addWidget(button)
        row.addStretch()
        return row

    def do(self, action):
        try:
            action()
        except (LicenseError, OSError, sqlite3.Error, ValueError) as exc:
            W.QMessageBox.warning(self,'操作未完成',str(exc))

    def ask_password(self, title):
        echo = getattr(W.QLineEdit,'EchoMode',W.QLineEdit).Password
        return W.QInputDialog.getText(self,title,'私钥保护密码',echo)

    def set_key(self, key):
        self.key = key
        self.key_status.setText(fingerprint(key))

    def generate_key(self):
        folder = W.QFileDialog.getExistingDirectory(self,'选择新密钥保存目录',self.data.text())
        if not folder:
            return
        password, ok = self.ask_password('设置私钥密码')
        if not ok:
            return
        repeated, ok = self.ask_password('确认私钥密码')
        if not ok:
            return
        if password != repeated:
            raise LicenseError('两次密码不同。')
        _, key = generate(Path(folder),password)
        self.set_key(key)
        self.status.setText(f'密钥已生成：{folder}')
        if not (self.universal_dir/'Universal.lic').exists():
            path=self.save_universal(key)
            self.status.setText(f'密钥已保存在 {folder}。管理员通用许可：{path}')

    def open_key(self):
        path, _ = W.QFileDialog.getOpenFileName(self,'加载加密私钥',self.data.text(),'PEM (*.pem)')
        if path:
            password, ok = self.ask_password('解锁私钥')
            if ok:
                self.set_key(load_private(Path(path),password))

    def require_key(self):
        if self.key is None:
            raise LicenseError('请先加载管理员私钥。')
        return self.key

    def export_key(self):
        key = self.require_key()
        path, _ = W.QFileDialog.getSaveFileName(self,'导出公钥',str(Path(self.data.text())/'public_key.pem'),'PEM (*.pem)')
        if path:
            new_file(path, public_bytes(key))

    def check_key(self):
        key = self.require_key()
        path, _ = W.QFileDialog.getOpenFileName(self,'校验公钥',self.data.text(),'PEM (*.pem)')
        if path:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            supplied=load_public_key(Path(path).read_bytes()).public_bytes(Encoding.Raw,PublicFormat.Raw)
            if supplied!=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw):
                raise LicenseError('公钥与已加载私钥不匹配。')
            self.status.setText('公钥与已加载私钥匹配。')

    def issue(self):
        path = History(Path(self.data.text())).issue(self.require_key(),self.name.text(),self.employee.text(),
                        self.machine.text(),self.notes.text(),Path(self.output.text()))
        self.status.setText(f'已签发：{path}')

    def issue_universal(self):
        key = self.require_key()
        button = W.QMessageBox.StandardButton
        if W.QMessageBox.question(self,'管理员通用许可','该文件可激活任何具备有效硬件信息的电脑。仅保存在管理员目录。',button.Yes|button.No,button.No)!=button.Yes:
            return
        path=self.save_universal(key)
        self.status.setText(f'通用永久许可证：{path}')

    def save_universal(self,key):
        return History(Path(self.data.text())).issue(key,'Administrator','UNIVERSAL','*','管理员通用许可',
                                                    self.universal_dir,universal=True)

    def open_folder(self):
        path = Path(self.output.text()).resolve()
        path.mkdir(parents=True,exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def history(self):
        history = History(Path(self.data.text()))
        dialog = W.QDialog(self)
        dialog.setWindowTitle('授权历史')
        dialog.resize(1000,500)
        layout = W.QVBoxLayout(dialog)
        query = W.QLineEdit()
        query.setPlaceholderText('搜索姓名、工号或机器码')
        layout.addWidget(query)
        table = W.QTableWidget(0,len(FIELDS))
        table.setHorizontalHeaderLabels(('授权编号','姓名','工号','机器码','签发日期','产品','文件','公钥指纹','备注'))
        table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        def read():
            rows = history.search(query.text().strip())
            table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                for j,value in enumerate(row):
                    table.setItem(i,j,W.QTableWidgetItem(value))
        def export(title, filename, filters, callback):
            path, _ = W.QFileDialog.getSaveFileName(dialog,title,str(Path(self.output.text())/filename),filters)
            if path:
                callback(path)
        def selected():
            if table.currentRow()<0:
                raise LicenseError('请先选中一条记录。')
            return tuple(table.item(table.currentRow(),c).text() for c in range(len(FIELDS)))
        def reexport():
            row = selected()
            export('导出原许可证',Path(row[6]).name,'许可证 (*.lic)',lambda path:history.export_original(row[0],path))
        def reissue():
            row = selected()
            if row[3]=='*':
                raise LicenseError('通用许可请使用首次部署入口。')
            self.name.setText(row[1]);self.employee.setText(row[2]);self.machine.setText(row[3]);self.notes.setText(row[8])
            dialog.accept()
        layout.addLayout(self.buttons(('导出原许可证',reexport),('准备重新签发',reissue),
                        ('CSV',lambda:export('导出 CSV','licenses.csv','CSV (*.csv)',lambda p:history.export_csv(p,query.text()))),
                        ('备份',lambda:export('备份历史','license-backup.db','SQLite (*.db)',history.backup))))
        query.textChanged.connect(lambda *_:self.do(read))
        read()
        run_dialog(dialog)


if __name__ == '__main__':
    app = W.QApplication([])
    window = Manager()
    window.show()
    sys.exit(app.exec() if hasattr(app,'exec') else app.exec_())
