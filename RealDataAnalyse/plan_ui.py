"""Separate, paginated plan panel; execution belongs to the main worker."""
import time
from pathlib import Path
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W
from .widgets import table_frame

STATES = {'pending':'等待执行','blocked':'需处理','running':'运行中','paused':'已暂停','completed':'已完成','error':'失败'}


class PlanPanel(W.QDialog):
    def __init__(self, owner, plans):
        super().__init__(owner)
        self.owner, self.plans, self.page = owner, plans, 0
        self.setWindowTitle('计划任务'); self.resize(1150,600)
        self.setModal(False)
        layout = W.QVBoxLayout(self)
        row = W.QHBoxLayout()
        self.start = W.QPushButton('开启计划任务')
        self.stop = W.QPushButton('安全停止计划')
        self.remove = W.QPushButton('删除选中计划')
        self.retry = W.QPushButton('重试失败 / 暂停项')
        self.check = W.QPushButton('重新检查')
        for widget in (self.start,self.stop,self.remove,self.retry,self.check): row.addWidget(widget)
        layout.addLayout(row)
        self.table = W.QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels(['项目文件夹','结果OAS','状态','进度 %','开始时间','结束时间','说明'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,180); self.table.setColumnWidth(1,250)
        self.table.setSelectionBehavior(getattr(W.QAbstractItemView,'SelectionBehavior',W.QAbstractItemView).SelectRows)
        self.frame = table_frame(self.table,370,owner.settings,'plans')
        scroll = W.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.frame)
        layout.addWidget(scroll,1)
        footer = W.QHBoxLayout(); self.label = W.QLabel()
        previous=W.QPushButton('上一页'); following=W.QPushButton('下一页')
        footer.addWidget(previous); footer.addWidget(following); footer.addWidget(self.label,1); layout.addLayout(footer)
        self.start.clicked.connect(owner.start_plans); self.stop.clicked.connect(owner.stop_work)
        self.remove.clicked.connect(lambda:self.act('remove')); self.retry.clicked.connect(lambda:self.act('retry'))
        self.check.clicked.connect(lambda:owner.execute(lambda stop,progress:plans.recheck(), operation='检查计划任务'))
        previous.clicked.connect(lambda:self.turn(-1)); following.clicked.connect(lambda:self.turn(1))
        self.timer=QtCore.QTimer(self); self.timer.setInterval(750); self.timer.timeout.connect(self.refresh); self.timer.start()
        self.refresh()

    def turn(self, step):
        self.page=max(0,self.page+step); self.refresh()

    def selected(self):
        role=getattr(QtCore.Qt,'ItemDataRole',QtCore.Qt).UserRole
        return {self.table.item(i.row(),0).data(role) for i in self.table.selectedIndexes() if self.table.item(i.row(),0)}

    def act(self, action):
        ids=self.selected()
        def change(stop,progress):
            for plan_id in ids:getattr(self.plans,action)(plan_id)
        self.owner.execute(change, operation='更新计划任务')

    def refresh(self):
        if not self.isVisible() and self.table.rowCount():return
        # Never replace the active cell editor while the user is entering a path.
        if self.table.state()==getattr(W.QAbstractItemView,'State',W.QAbstractItemView).EditingState:return
        count, rows=self.plans.rows(self.page)
        maximum=max(0,(count-1)//200)
        if self.page>maximum:self.page=maximum; count,rows=self.plans.rows(self.page)
        self.table.blockSignals(True); self.table.setRowCount(len(rows))
        self.table.setVerticalHeaderLabels([str(row['seq']) for row in rows])
        role=getattr(QtCore.Qt,'ItemDataRole',QtCore.Qt).UserRole
        edit_flag=getattr(QtCore.Qt,'ItemFlag',QtCore.Qt).ItemIsEditable
        for i,row in enumerate(rows):
            clock=lambda value:time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(value)) if value else ''
            values=(row['folder'],row['output'],STATES.get(row['status'],row['status']),f'{row["progress"]:.1f}',clock(row['started']),clock(row['ended']),row['message'])
            color='#d9efdf' if row['status']=='completed' else '#f8d9d7' if row['severity']=='error' else '#fff1c7' if row['severity']=='warning' else None
            for j,value in enumerate(values):
                cell=W.QTableWidgetItem(str(value)); cell.setToolTip(str(value)); cell.setData(role,row['id'])
                cell.setFlags(cell.flags() & ~edit_flag)
                if color:cell.setBackground(QtGui.QColor(color))
                self.table.setItem(i,j,cell)
        self.table.blockSignals(False)
        self.label.setText(f'第{self.page+1}/{maximum+1}页 · 共{count}项 · 黄色：输出冲突 · 红色：不可运行 · 绿色：已完成')
        busy=bool(self.owner.worker)
        for button in (self.start,self.remove,self.retry,self.check):button.setEnabled(not busy)
        self.stop.setEnabled(busy)
