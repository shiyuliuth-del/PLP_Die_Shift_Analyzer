from __future__ import annotations

import json
import math
import threading
import traceback
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from .qt_compat import QtCore, QtGui, QtWidgets as W, Signal, HORIZONTAL, VERTICAL, Qt
from .style import STYLE
from .core import Parameters, ROLES, read_gds, build_case, evaluate, fmt
from . import __version__

import matplotlib
from matplotlib import font_manager
_available_fonts={f.name for f in font_manager.fontManager.ttflist}
_plot_font=next((f for f in ('Microsoft YaHei','SimHei','Noto Sans CJK JP','WenQuanYi Zen Hei','DejaVu Sans') if f in _available_fonts),'sans-serif')
matplotlib.rcParams.update({'font.family':[_plot_font],
                            'axes.unicode_minus':False,'font.size':10})
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection, LineCollection, PatchCollection
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.lines import Line2D
from matplotlib.transforms import Affine2D
from matplotlib import patheffects
from .plot_geometry import geometry_path
from .settings import ViewSettings
from .pivots import rotation_centers
from .setting_widgets import PivotEditor,ColorButton,RangeEditor
from .animation import Transition
from .selection import GeometrySelection
from .ticks import DistanceMinorLocator
from .render_cache import SceneRenderer
from .layer_controls import LayerDrag,LayerStyleDialog
from .qt_compat import run_dialog,ACCEPTED
from matplotlib.colors import LinearSegmentedColormap,Normalize

from shapely.geometry import Polygon

ROOT=Path(__file__).resolve().parent.parent

SPECS={
 'panel_layers':('固定RDL层数',0,32,0), 'wafer_layers':('运动RDL层数',0,32,0),
 'line_width_um':('固定线宽 / μm',.01,10000,3),'line_space_um':('固定线距 / μm',.01,10000,3),
 'wafer_line_width_um':('运动线宽 / μm',.01,10000,3),'wafer_line_space_um':('运动线距 / μm',.01,10000,3),
 'via_diameter_um':('连接孔直径 / μm',.01,10000,3),'annulus_um':('最小包围量 / μm',0,10000,3),
   'min_die_gap_um':('芯片间要求净距 / μm',0,10000,3),
 'min_package_clearance_um':('芯片到封装边要求净距 / μm',0,10000,3),
 'single_bound_x_um':('单芯片 X 边界 / μm',0,10000,3),'single_bound_y_um':('单芯片 Y 边界 / μm',0,10000,3),
 'multi_bound_x_um':('多芯片每颗 X 边界 / μm',0,10000,3),'multi_bound_y_um':('多芯片每颗 Y 边界 / μm',0,10000,3),
 'bound_angle_deg':('角度边界 / °',0,180,4),
 'mean_x_um':('平均 X 偏移 / μm',-10000,10000,3),
 'mean_y_um':('平均 Y 偏移 / μm',-10000,10000,3),'mean_angle_deg':('平均转角 / °',-180,180,4),
 'rho_xy':('X/Y相关系数',-1,1,3),
}



class Worker(QtCore.QThread):
    done=Signal(object); failed=Signal(str); progress=Signal(int,str); partial=Signal(object)
    def __init__(self,fn,parent=None):
        super().__init__(parent);self.fn=fn;self.cancelled=threading.Event();self._last_progress=0.;self._partial_lock=threading.Lock();self._partial_value=None;self._partial_queued=False
    def update(self,n,text):
        if self.cancelled.is_set():raise InterruptedError('用户取消')
        import time
        now=time.monotonic()
        if n in (0,100) or now-self._last_progress>=.05:
            self._last_progress=now;self.progress.emit(n,text)
    def publish(self,value):
        with self._partial_lock:
            self._partial_value=value;notify=not self._partial_queued;self._partial_queued=True
        if notify:self.partial.emit(None)
    def take_partial(self):
        with self._partial_lock:
            value=self._partial_value;self._partial_value=None;self._partial_queued=False
        return value
    def run(self):
        try:
            result=self.fn(self.update)
            if not self.cancelled.is_set():self.done.emit(result)
        except InterruptedError:pass
        except ValueError as exc:self.failed.emit(str(exc)+'\n'+traceback.format_exc())
        except Exception as exc:self.failed.emit(str(exc)+'\n'+traceback.format_exc())


def label(text):
    x=W.QLabel(text);x.setWordWrap(True);return x


def table(headers):
    x=W.QTableWidget(0,len(headers));x.setHorizontalHeaderLabels(headers)
    x.setAlternatingRowColors(True);x.verticalHeader().hide()
    x.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers if hasattr(W.QAbstractItemView,'EditTrigger') else W.QAbstractItemView.NoEditTriggers)
    x.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows if hasattr(W.QAbstractItemView,'SelectionBehavior') else W.QAbstractItemView.SelectRows)
    return x


from .project import normalize_project
from .preview import default_motion,extreme_cases,layer_owners,transformed_layers,PREVIEW_SCOPE
from .navigation import Navigation
from .report import report_document
from .formula_render import set_document
from .layer_style import layer_style
from .preview import rigid_transform
from .fast_preview import GeometryCache,PreviewEngine
from .stack import stack_order,resolve_stages,Connections,PHYSICAL
from .representatives import RepresentativeExtremes
from .jobs import installation_root,list_jobs,job_path,atomic_save
from .recognition import automatic_parameters
from .resources import resource_profile

class NoWheelValues(QtCore.QObject):
    def eventFilter(self,obj,event):
        kind=getattr(QtCore.QEvent,'Type',QtCore.QEvent).Wheel
        if event.type()==kind and isinstance(obj,(W.QAbstractSpinBox,W.QComboBox,W.QSlider)):
            # Value controls never consume the wheel as an edit. Forward the
            # gesture to the nearest table / scroll page instead. Scroll bars,
            # text viewers and table viewports retain their native wheel events.
            parent=obj.parentWidget()
            while parent is not None and not isinstance(parent,W.QAbstractScrollArea):parent=parent.parentWidget()
            if parent is not None:
                bar=parent.verticalScrollBar();delta=event.angleDelta().y()/120
                distance=event.pixelDelta().y() if not event.pixelDelta().isNull() else delta*W.QApplication.wheelScrollLines()*bar.singleStep()
                bar.setValue(round(bar.value()-distance));event.accept()
            else:event.ignore()
            return True
        return False


class MainWindow(W.QMainWindow):
    def __init__(self,jobs_dir=None):
        super().__init__();self.setWindowTitle(f'Die Shift Analyze {__version__}')
        self.jobs_dir=Path(jobs_dir) if jobs_dir is not None else installation_root()/'Jobs'
        self.jobs_dir.mkdir(parents=True,exist_ok=True)
        self.wheel_filter=NoWheelValues(self);W.QApplication.instance().installEventFilter(self.wheel_filter)
        self.resize(1540,1000);self.setMinimumSize(1120,740);self.setStyleSheet(STYLE)
        self.data=None;self.result=None;self.case=None;self.worker=None;self.project_path=None
        from .runtime_log import LogBus,RuntimeCapture
        self.log_bus=LogBus(self,self.jobs_dir.parent/'logs'/'runtime.log');self.runtime_capture=RuntimeCapture(self.log_bus);self._tasks={};self._closing=False
        self.per_die_values=[];self.per_die_pivots=[];self.reference_nsigma=6.;self._per_die_initialized=False;self.hardware_worker=None
        from .session_cache import SessionCache
        self.session_values=SessionCache();self.session_engines={};self.retired_sessions=[]
        self.fields={};self.role_boxes={};self.stage_hints={};self.visible_boxes={};self.motion_boxes={}
        self.loading=False;self.revision=0;self.extremes=None;self.active_preview=None;self.owner_cache=None;self.auto_values={}
        self.preview_page=0;self.page_items=[];self.state_result=None;self.issue_artists=[];self.shift_artist=None
        self.rendered_layers={};self.layer_artists={};self._layer_keys=[];self.geometry_cache=None;self.connections=None
        self.preview_worker=None;self.preview_pending=None;self.preview_ticket=0;self.engine=None;self.current_frame=None;self.shown_states=None
        self.die_labels=[];self._default_view=None;self._artist_frame=None;self.report_dirty=True;self._reordering=False
        self.space_data=None;self.space_artists=[];self.space_stamp=None;self.space_colorbar=None;self._resizing=False
        self._issue_table_stamp=None;self.dirty=False;self.manual_states=None;self.evaluation_motions={};self.probability_data=None;self.probability_artists=[];self.probability_stamp=None;self.legend=None;self.zone_artists=[];self.invalid_artists=[]
        self.view_settings=ViewSettings();self.setting_fields={};self.layer_styles={};self.histogram_dirty=False
        self.coverage_timer=QtCore.QTimer(self);self.coverage_timer.setSingleShot(True);self.coverage_timer.setInterval(180);self.coverage_timer.timeout.connect(self.refresh_coverage)
        self._coverage_pending=False;self._evaluation_requested=False;self._motion_intent=None;self.motion_worker=None;self.motion_pending=None;self.motion_generation=0;self.deformation_artists={};self._plotting=False;self.viewport_index=None;self._viewport_stamp=None
        self.issue_navigation=QtCore.QTimer(self);self.issue_navigation.setSingleShot(True);self.issue_navigation.setInterval(0);self.issue_navigation.timeout.connect(lambda:self.locate_issue(self.issue_table.currentRow(),0))
        self.viewport_timer=QtCore.QTimer(self);self.viewport_timer.setSingleShot(True);self.viewport_timer.setInterval(0);self.viewport_timer.timeout.connect(self.viewport_changed)
        self.transition=Transition(self);self._animation_cleanup=None;self._legend_stamp=None
        self._build();self.selection=GeometrySelection(self);self.escape_shortcut=(QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut)(QtGui.QKeySequence('Escape'),self);self.escape_shortcut.activated.connect(self.escape_action);self.scene_renderer=SceneRenderer(self.canvas,self.ax);self.refresh_jobs()
        for name in ('button_press_event','scroll_event'):self.canvas.mpl_connect(name,lambda e:self.stop_animation())
        from .mouse_die import MouseDieMode
        self.mouse_die=MouseDieMode(self)
        self.job_watcher=QtCore.QFileSystemWatcher([str(self.jobs_dir)],self);self.job_watcher.directoryChanged.connect(self.refresh_jobs)
        QtCore.QTimer.singleShot(0,self.discover_hardware)
        self.statusBar().showMessage('打开GDS → 指定图层 → 设置偏移/工艺 → 评估 → 查看报告与极端预演')
    def _page(self,title):
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);page=W.QWidget();lay=W.QVBoxLayout(page)
        lay.setContentsMargins(10,10,10,10);lay.setSpacing(10);scroll.setWidget(page);self.controls.addTab(scroll,title);return lay
    def _group(self,parent,title,keys,collapsed=False):
        box=W.QGroupBox(title);form=W.QFormLayout(box);form.setVerticalSpacing(7)
        for key in keys:
            name,low,high,d=SPECS[key]
            spin=W.QSpinBox() if d==0 else W.QDoubleSpinBox();spin.setRange(low,high)
            if d:spin.setDecimals(d)
            spin.setValue(getattr(Parameters(),key));spin.setKeyboardTracking(False)
            spin.setToolTip(name+'；输入、公式、输出见报告页。');spin.valueChanged.connect(self.invalidate)
            form.addRow(name,spin);self.fields[key]=spin
        if collapsed:
            toggle=W.QToolButton();toggle.setText('▸ '+title);toggle.setCheckable(True);box.hide()
            toggle.toggled.connect(box.setVisible);toggle.toggled.connect(lambda checked:toggle.setText(('▾ ' if checked else '▸ ')+title));parent.addWidget(toggle)
        parent.addWidget(box);return form
    def _build(self):
        central=W.QWidget();root=W.QVBoxLayout(central);self.setCentralWidget(central)
        root.setContentsMargins(6,4,6,4);root.setSpacing(4)
        header=W.QHBoxLayout();header.setSpacing(6);title=W.QLabel('Die Shift Analyze');title.setStyleSheet('font-size:14px;font-weight:bold;color:#213b61;');header.addWidget(title)
        header.addSpacing(14);header.addWidget(W.QLabel('GDS · 极端偏移预演 · μm'));header.addStretch()
        self.progress=W.QProgressBar();self.progress.setMinimumWidth(160);self.progress.setMaximumWidth(360);self.progress.setMaximumHeight(23);self.progress.setTextVisible(True)
        policy=self.progress.sizePolicy();policy.setRetainSizeWhenHidden(True);self.progress.setSizePolicy(policy);header.addWidget(self.progress,1);self.progress.hide()
        self.job_combo=W.QComboBox();self.job_combo.setMinimumWidth(210);self.job_combo.setMaximumWidth(360)
        self.job_combo.setToolTip('Jobs工程；空白项表示未命名的新工程');self.job_combo.currentIndexChanged.connect(self.job_selected);header.addWidget(self.job_combo)
        self.save_button=W.QPushButton('保存工程');self.save_button.clicked.connect(self.save_project);header.addWidget(self.save_button)
        for widget in (title,self.job_combo,self.save_button):widget.setMaximumHeight(27);widget.setStyleSheet(widget.styleSheet()+'padding:2px 5px;min-height:18px;')
        self.runtime_toggle=W.QCheckBox('运行信息');header.addWidget(self.runtime_toggle)
        root.addLayout(header);split=W.QSplitter(HORIZONTAL);root.addWidget(split,1)
        left=W.QWidget();left.setMinimumWidth(410);left.setMaximumWidth(530);lay=W.QVBoxLayout(left);lay.setContentsMargins(0,0,0,0)
        self.controls=W.QTabWidget();lay.addWidget(self.controls,1)
        page=self._page('偏移')
        summary=label('方案1：动静区之间局部重构RDL。\n方案2：RDL整体随动，固定RDL窗口捕获。\n方案3：预制运动RDL，动静接口适配。');summary.setObjectName('Notice');page.addWidget(summary)
        self.gds_label=label('尚未打开GDS');self.gds_label.setTextInteractionFlags(getattr(Qt,'TextInteractionFlag',Qt).TextSelectableByMouse);page.addWidget(self.gds_label)
        row=W.QHBoxLayout();row.addWidget(W.QLabel('顶层Cell'));self.top=W.QComboBox();self.top.currentTextChanged.connect(self.top_changed);row.addWidget(self.top,1);page.addLayout(row)
        self.open_gds_button=W.QPushButton('打开GDS');self.open_gds_button.setObjectName('Primary');self.open_gds_button.clicked.connect(self.open_gds);page.addWidget(self.open_gds_button)
        self.layer_table=table(['显示','层 / 类型','数量','用途']);self.layer_table.setMinimumHeight(275);page.addWidget(self.layer_table)
        self.layer_table.verticalHeader().setSectionsMovable(True)
        self.layer_drag=LayerDrag(self.layer_table);self.layer_drag.styleRequested.connect(self.edit_layer_style)
        self.drag_timer=QtCore.QTimer(self);self.drag_timer.setSingleShot(True);self.drag_timer.setInterval(30)
        self.drag_timer.timeout.connect(lambda:self.plot_layout(preserve=True))
        self.layer_drag.moved.connect(lambda:self.drag_timer.start() if not self.drag_timer.isActive() else None)
        self.layer_drag.finished.connect(self.layer_moved)
        target=W.QGroupBox('目标覆盖要求');form=W.QFormLayout(target)
        self.target_sigma=W.QComboBox();self.target_sigma.setEditable(False);self.target_sigma.addItems([str(i) for i in range(0,13)]+['∞']);self.target_sigma.setCurrentText('6')
        self.target_sigma.setToolTip('有限非零k：最大幅度 B 决定截断前 σ=B/k；0保留最近尺度并检查全部有界概率。∞或具体预演按当前接触判断。')
        self.target_sigma.currentTextChanged.connect(self.coverage_changed);form.addRow('目标覆盖级别',self.target_sigma);page.addWidget(target)
        amplitude=self._group(page,'偏移单边幅度',['single_bound_x_um','single_bound_y_um','multi_bound_x_um','multi_bound_y_um','bound_angle_deg'])
        self.pivot_editor=PivotEditor();amplitude.addRow('旋转中心',self.pivot_editor);self.pivot_editor.valueChanged.connect(self.invalidate)
        self.per_die_toggle=W.QCheckBox('按芯片分别设置单边幅度');page.addWidget(self.per_die_toggle)
        self.per_die_table=table(['芯片','X / μm','Y / μm','O / °','旋转中心']);self.per_die_table.setMaximumHeight(215);page.addWidget(self.per_die_table);self.per_die_table.hide()
        self.per_die_toggle.toggled.connect(self.per_die_changed)
        self._group(page,'系统偏差与相关性',['mean_x_um','mean_y_um','mean_angle_deg','rho_xy'],collapsed=True);page.addStretch()
        page=self._page('工艺')
        self._group(page,'目标层数与线宽线距',['panel_layers','wafer_layers','line_width_um','line_space_um','wafer_line_width_um','wafer_line_space_um'])
        page.addWidget(label('运动层按下层连接追溯所属芯片。固定层保持名义位置，层数由GDS用途自动统计。'))
        self._group(page,'过孔控制区',['annulus_um'])
        self._group(page,'机械净距',['min_die_gap_um','min_package_clearance_um'])
        self.fixed_assignment='all'
        page.addStretch()
        self.build_settings(self._page('设置'))
        self.run_button=W.QPushButton('方案评估');self.run_button.setObjectName('Primary');self.run_button.clicked.connect(lambda:self.run_evaluation());lay.addWidget(self.run_button);split.addWidget(left)
        self.tabs=W.QTabWidget();split.addWidget(self.tabs);split.setSizes([445,1095])
        self.figure=Figure(figsize=(9,5));self.figure.subplots_adjust(left=.043,right=.993,bottom=.095,top=.99);self.canvas=FigureCanvasQTAgg(self.figure);self.ax=self.figure.add_subplot(111)
        page=W.QWidget();v=W.QVBoxLayout(page);v.setContentsMargins(0,0,0,0);self.toolbar=NavigationToolbar2QT(self.canvas,self);v.addWidget(self.toolbar)
        right_split=W.QSplitter(VERTICAL);right_split.addWidget(self.canvas);v.addWidget(right_split,1)
        bottom=W.QWidget();bv=W.QVBoxLayout(bottom);bv.setContentsMargins(5,3,5,3)
        self.preview_tabs=W.QTabWidget();bv.addWidget(self.preview_tabs)
        self.probability_toggle=W.QCheckBox('叠加运动概率');self.probability_toggle.setToolTip('包含实体面积的过孔覆盖概率；半透明显示，设置页可选择数值算法与精度；不是所选极端状态的条件概率')
        corner=W.QWidget();corner_layout=W.QHBoxLayout(corner);corner_layout.setContentsMargins(4,0,4,0)
        self.drc_toggle=W.QCheckBox('DRC问题');self.drc_toggle.setToolTip('全局金属线宽/线距：夹角≤5°的非相邻边；另检查内角、短接和接口包围量。粉色显示当前实际图形的问题。');corner_layout.addWidget(self.drc_toggle)
        self.drc_toggle.toggled.connect(self.drc_changed);corner_layout.addWidget(self.probability_toggle)
        self.space_toggle=W.QCheckBox('Space Map');self.space_toggle.setToolTip('整面所需线距 = 固定线距 + 目标σ下的保守偏移补偿量（μm）；随接口过孔图层显示/隐藏')
        corner_layout.addWidget(self.space_toggle)
        self.deformation_toggle=W.QCheckBox('预演分析');self.deformation_toggle.setToolTip('评估后，调整孔盘或追溯三段式型变；未通过约束的最优候选标为DRC问题，源GDS不变');corner_layout.addWidget(self.deformation_toggle)
        self.deformation_toggle.toggled.connect(self.deformation_changed);self.preview_tabs.setCornerWidget(corner)
        self.probability_toggle.toggled.connect(self.probability_changed);self.space_toggle.toggled.connect(self.space_changed)
        extreme_page=W.QWidget();ep=W.QVBoxLayout(extreme_page);ep.setContentsMargins(0,0,0,0)
        self.page_summary=W.QLabel('尚未评估');ep.addWidget(self.page_summary)
        self.extreme_table=table(['极端情况','DX / μm','DY / μm','转角 / °','极值定义']);self.extreme_table.setRowCount(1)
        self.extreme_table.currentCellChanged.connect(self.select_preview);ep.addWidget(self.extreme_table)
        manual=W.QHBoxLayout();self.manual_die=W.QComboBox();manual.addWidget(self.manual_die);self.manual_fields={}
        for key,title in [('dx','DX / μm'),('dy','DY / μm'),('angle','O / °')]:
            manual.addWidget(W.QLabel(key if key!='angle' else 'O/°'));spin=W.QDoubleSpinBox();spin.setRange(-100000,100000);spin.setDecimals(4);spin.setKeyboardTracking(False);spin.setMinimumWidth(60);spin.setMaximumWidth(105);self.manual_fields[key]=spin;manual.addWidget(spin)
        self.manual_button=W.QPushButton('预览输入');self.manual_button.clicked.connect(self.apply_manual);manual.addWidget(self.manual_button);ep.addLayout(manual)
        self.motion_table=table(['层/类型','用途','运动方式','连接追溯结果'])
        self.state_table=table(['芯片','DX / μm','DY / μm','转角 / °','组合方向'])
        issue_page=W.QWidget();iv=W.QVBoxLayout(issue_page);iv.setContentsMargins(0,0,0,0);ir=W.QHBoxLayout()
        self.issue_scheme=W.QComboBox()
        for number in (1,2,3):self.issue_scheme.addItem(f'方案{number}',number)
        self.show_issues=W.QCheckBox('显示接触位置');self.show_issues.setChecked(True)
        ir.addWidget(self.issue_scheme);ir.addWidget(self.show_issues);self.issue_summary=label('');ir.addWidget(self.issue_summary,1);iv.addLayout(ir)
        self.issue_table=table(['接触判定','DIE','概率范围 / 二值','允许概率','控制距离 / μm','原间距 / μm','接触坐标 / μm']);iv.addWidget(self.issue_table)
        self.issue_table.currentCellChanged.connect(self.issue_row_changed);self.issue_table.cellClicked.connect(lambda *_:self.issue_navigation.start())
        self.issue_scheme.currentIndexChanged.connect(self.issue_filter_changed);self.show_issues.toggled.connect(lambda _:self.plot_layout(preserve=True))
        self.extreme_split=W.QSplitter(HORIZONTAL);self.extreme_split.addWidget(extreme_page);self.extreme_split.addWidget(issue_page);self.extreme_split.setSizes([520,500])
        self.preview_tabs.addTab(self.extreme_split,'极端情况')
        self.die_table=table(['芯片','长 / μm','宽 / μm','接口过孔','边界接口','角点包络 / μm','运动通道占用比','固定接口比值','方案3接口比值'])
        self.die_table.cellDoubleClicked.connect(self.locate_die)
        info=W.QSplitter(HORIZONTAL)
        for caption,widget in [('图层随动设置',self.motion_table),('当前芯片状态',self.state_table),('逐芯片结果',self.die_table)]:
            pane=W.QWidget();pl=W.QVBoxLayout(pane);pl.setContentsMargins(2,2,2,2);pl.addWidget(W.QLabel(caption));pl.addWidget(widget);info.addWidget(pane)
        info.setSizes([360,330,340]);self.info_split=info;self.preview_tabs.addTab(info,'信息')
        self.deformation_table=table(['预演层','源图形','未解原因（保留原图）']);self.preview_tabs.addTab(self.deformation_table,'型变校验');self.deformation_table.currentCellChanged.connect(lambda row,*_:self.locate_deformation(row))
        right_split.addWidget(bottom);right_split.setSizes([560,240]);self.tabs.addTab(page,'结果图形')
        self.navigation=Navigation(self.canvas,self.ax)
        self.canvas.mpl_connect('resize_event',self.resize_plot)
        self.ax.callbacks.connect('xlim_changed',self.update_die_label_visibility);self.ax.callbacks.connect('ylim_changed',self.update_die_label_visibility)
        self.ax.callbacks.connect('xlim_changed',self.queue_viewport);self.ax.callbacks.connect('ylim_changed',self.queue_viewport)
        self.scheme_figure=Figure(figsize=(9,5),layout='constrained');self.scheme_canvas=FigureCanvasQTAgg(self.scheme_figure);self.scheme_canvas.setMinimumHeight(160)
        page=W.QWidget();v=W.QVBoxLayout(page);self.verdict=label('导入GDS后设置图层用途，再进行评估。');self.verdict.setObjectName('Notice');v.addWidget(self.verdict)
        self.analysis_page=page;upper=W.QWidget();uv=W.QVBoxLayout(upper);uv.setContentsMargins(0,0,0,0);uv.addWidget(self.scheme_canvas,1)
        self.scheme_table=table(['方案','状态','Γ','选择理由']);self.scheme_table.setMaximumHeight(160);uv.addWidget(self.scheme_table)
        self.analysis_split=W.QSplitter(VERTICAL);self.analysis_split.addWidget(upper);v.addWidget(self.analysis_split,1);self.tabs.addTab(page,'结果分析')
        chart_page=W.QWidget();cv=W.QVBoxLayout(chart_page);self.chart_status=label('选择极端情况后显示可见运动过孔的位移统计。');cv.addWidget(self.chart_status)
        self.distribution_figure=Figure(figsize=(9,6),layout='constrained');self.distribution_canvas=FigureCanvasQTAgg(self.distribution_figure);self.distribution_canvas.setMinimumHeight(200);cv.addWidget(self.distribution_canvas,1)
        self.analysis_split.addWidget(chart_page);self.analysis_split.setSizes([430,350]);self.histogram_data=None
        self.detail=W.QTextBrowser();self.tabs.addTab(self.detail,'报告');self.tabs.currentChanged.connect(self.tab_changed)
        self.runtime_view=W.QPlainTextEdit();self.runtime_view.setReadOnly(True);self.runtime_view.setMaximumBlockCount(10000);self.runtime_view.setToolTip('界面保留最近10000行；完整日志：'+str(self.log_bus.path));self.runtime_view.setMaximumHeight(170);self.runtime_view.hide();root.addWidget(self.runtime_view)
        self.runtime_toggle.toggled.connect(self.runtime_view.setVisible);self.log_bus.message.connect(self.runtime_view.appendPlainText)
        self.log_bus.emit('启动 '+__version__+'；硬件资源 '+str(resource_profile()))
        self.plot_layout()
    def build_settings(self,page):
        def group(title):
            box=W.QGroupBox(title);form=W.QFormLayout(box);page.addWidget(box);return form
        hardware=group('硬件与计算资源')
        profile=resource_profile()
        self.hardware_label=label(f"{profile['cpu']} 逻辑CPU · 可用内存 {profile['available_memory_mb']} MB · 最多 {profile['workers']} 线程 / {profile['process_workers']} 进程\n显卡检测中…");hardware.addRow(self.hardware_label)
        accel=W.QComboBox();accel.addItem('自动（按任务大小）','auto');accel.addItem('CPU','cpu')
        self.setting_fields['acceleration']=accel;hardware.addRow('显卡加速',accel);accel.currentIndexChanged.connect(self.settings_changed)
        probability=group('过孔实体覆盖概率')
        choices={'probability_method':[('Sobol准蒙特卡洛','sobol'),('蒙特卡洛','monte_carlo'),('确定性积分（无随机，全网格）','quadrature')],
                 'space_cmap':[('浅色 · 青绿紫','pastel'),('黄绿蓝','YlGnBu'),('蓝紫','BuPu'),('Viridis','viridis'),('Cividis','cividis')],
                 'space_interpolation':[('无平滑','nearest'),('双线性','bilinear'),('双三次','bicubic')]}
        choices['probability_cmap']=[('浅色 · 蓝青','blue')]+choices['space_cmap'][1:]
        choices['probability_interpolation']=choices['space_interpolation']
        choices['space_scope']=[('运动内容范围','moving'),('整个封装范围','package')]
        def choice(form,key,title):
            widget=W.QComboBox()
            for name,value in choices[key]:widget.addItem(name,value)
            self.setting_fields[key]=widget;form.addRow(title,widget);widget.currentIndexChanged.connect(self.settings_changed)
        def spin(form,key,title,low,high,decimals=0):
            widget=W.QDoubleSpinBox() if decimals else W.QSpinBox();widget.setRange(low,high)
            if decimals:widget.setDecimals(decimals)
            widget.setKeyboardTracking(False);self.setting_fields[key]=widget;form.addRow(title,widget);widget.valueChanged.connect(self.settings_changed)
        choice(probability,'probability_method','计算方法')
        spin(probability,'sample_exponent','样本数 = 2 的指数',8,18)
        spin(probability,'random_seed','随机种子',0,2147483647)
        spin(probability,'quadrature_order','确定性角度积分阶数',5,81)
        spin(probability,'probability_cell_um','网格 / μm（0自动）',0,1000,3)
        spin(probability,'angle_bins','实体角度分组上限',4,128)
        choice(probability,'probability_cmap','配色方案');choice(probability,'probability_interpolation','显示平滑')
        check=W.QCheckBox('显示目标覆盖级别范围');self.setting_fields['probability_show_ranges']=check;probability.addRow(check);check.toggled.connect(self.settings_changed)
        color=ColorButton();self.setting_fields['probability_target_color']=color;probability.addRow('当前级别虚线颜色',color);color.valueChanged.connect(self.settings_changed)
        ranges=RangeEditor();self.setting_fields['probability_ranges']=ranges;probability.addRow(ranges);ranges.valueChanged.connect(self.settings_changed)
        spin(probability,'probability_alpha','叠加不透明度',.05,.95,2)
        spin(probability,'contact_box_budget','接触概率自适应积分预算',128,32768)
        page.addWidget(label('实体概率按所选方法计算；虚线包络表示输入幅度全部组合，并非等概率边界。默认接触判定用概率上下界；积分不足时明确标为待细化。∞仅作当前位置二值判断。'))
        space=group('Space Map')
        choice(space,'space_scope','显示范围')
        choice(space,'space_cmap','配色方案');choice(space,'space_interpolation','显示平滑')
        spin(space,'space_resolution','长边网格数',31,401)
        spin(space,'space_alpha','叠加不透明度',.05,.95,2)
        animation=group('视图与预演运动')
        enabled=W.QCheckBox('启用渐变插值运动');self.setting_fields['animation_enabled']=enabled;animation.addRow(enabled);enabled.toggled.connect(self.settings_changed)
        spin(animation,'animation_duration_ms','时长上限 / ms',80,3000)
        spin(animation,'animation_speed','预演速度级别',1,5)
        spin(animation,'animation_fps','FPS（自动计算插值数）',10,120)
        spin(animation,'camera_speed','定位视角速度级别',1,5)
        deformation=group('预演分析 · 固定RDL型变')
        choices['deformation_continuity']=[('C1 · 切向连续','C1'),('C2 · 切向与曲率连续','C2')]
        choices['deformation_method']=[('贝塞尔权重过渡','bezier'),('圆弧路径混合','arc')]
        choice(deformation,'deformation_continuity','过渡连续性');choice(deformation,'deformation_method','引线渐变方式')
        spin(deformation,'deformation_arc_angle_deg','圆弧路径圆心角 / °',1,120,1)
        spin(deformation,'deformation_min_angle_deg','最小铜图形内角 / °',1,179,1)
        self.setting_fields['deformation_min_angle_deg'].setToolTip('铜图形内部最小凸角；圆弧以DBU折线离散，角度检验容差0.2°。无法达标时恢复原图，显示未适配原因。')
        spin(deformation,'deformation_tolerance_um','曲线细分误差 / μm',.001,1,3)
        page.addWidget(label('连续性约束作用于型变位移场。孔盘调整失败后自动追溯三段式适配；未通过则恢复原图并显示琥珀叉号，型变校验列出原因。DRC检查固定RDL的≤5°非相邻边；型变另验证异网最小净距。'))
        drawing=group('接触标记');spin(drawing,'intrusion_alpha','侵入面积不透明度',.05,.85,2)
        for prefix,title in [('contact','接触空心圆'),('unadapted','未适配叉号')]:
            color=ColorButton();self.setting_fields[prefix+'_marker_color']=color;drawing.addRow(title+'颜色',color);color.valueChanged.connect(self.settings_changed)
            spin(drawing,prefix+'_marker_size',title+'直径 / pt 或 μm',.1,10000,2)
            check=W.QCheckBox(title+'使用图纸绝对大小 / μm');self.setting_fields[prefix+'_marker_absolute']=check;drawing.addRow(check);check.toggled.connect(self.settings_changed)
        page.addWidget(label('平滑只改变热图显示，不改变线距需求、连接归属和接触判定。资源由CPU/内存自动限额，多线程计算几何和概率，切换预演复用数值结果。'))
        page.addStretch();self.load_view_settings()
    def discover_hardware(self):
        from .acceleration import capabilities,graphics_inventory
        def work(progress):
            progress(5,'检测显卡与计算运行时');cap=capabilities();inventory=graphics_inventory();progress(100,'硬件检测完成');return cap,inventory
        worker=Worker(work,self);self.hardware_worker=worker;self.watch_worker(worker,'硬件检测')
        def accepted(value):
            cap,adapters=value;profile=resource_profile()
            self.hardware_label.setText(f"{profile['cpu']} 逻辑CPU · 可用内存 {profile['available_memory_mb']} MB · 最多 {profile['workers']} 线程 / {profile['process_workers']} 进程\n"+('; '.join(d['name'] for d in (cap['devices'] or adapters)) or '未检出显卡')+'\n'+cap['reason'])
            self.log_bus.emit(self.hardware_label.text())
            combo=self.setting_fields['acceleration'];combo.blockSignals(True)
            if cap['backend']=='cuda' and combo.findData('cuda')<0:combo.addItem('CUDA · '+cap['devices'][0]['name'],'cuda')
            combo.setCurrentIndex(max(0,combo.findData(self.view_settings.acceleration)));combo.blockSignals(False)
        def finished():self.hardware_worker=None;worker.deleteLater()
        worker.done.connect(accepted);worker.finished.connect(finished);worker.start()
    def load_view_settings(self):
        for key,widget in self.setting_fields.items():
            widget.blockSignals(True);value=getattr(self.view_settings,key)
            if isinstance(widget,W.QComboBox):widget.setCurrentIndex(max(0,widget.findData(value)))
            elif isinstance(widget,W.QCheckBox):widget.setChecked(value)
            else:widget.setValue(value)
            widget.blockSignals(False)
        self.enable_settings()
    def enable_settings(self):
        if not self.setting_fields:return
        self.setting_fields['deformation_arc_angle_deg'].setEnabled(self.setting_fields['deformation_method'].currentData()=='arc')
        deterministic=self.setting_fields['probability_method'].currentData()=='quadrature'
        for key in ('sample_exponent','random_seed','angle_bins'):self.setting_fields[key].setEnabled(not deterministic)
        for key in ('quadrature_order',):self.setting_fields[key].setEnabled(deterministic)
    def settings_changed(self,*_):
        if self.loading:return
        values={k:(w.currentData() if isinstance(w,W.QComboBox) else w.isChecked() if isinstance(w,W.QCheckBox) else w.value()) for k,w in self.setting_fields.items()}
        intent=self._motion_intent if self.transition.timer.isActive() else None
        self.stop_animation()
        old_settings=self.view_settings;updated=ViewSettings.from_dict(values);deform=any(getattr(updated,k)!=getattr(self.view_settings,k) for k in ('deformation_continuity','deformation_method','deformation_tolerance_um','deformation_arc_angle_deg','deformation_min_angle_deg'));numeric=(updated.numeric_key()!=self.view_settings.numeric_key() or (updated.probability_show_ranges!=old_settings.probability_show_ranges and self.target_sigma.currentText()=='∞'));self.view_settings=updated;self.enable_settings()
        live_motion=any(getattr(updated,k)!=getattr(old_settings,k) for k in ('animation_enabled','animation_duration_ms','animation_fps','animation_speed'))
        if numeric:
            self.dirty=self.result is not None
            if self.result:self.verdict.setText('计算设置已修改：以下为上次评估结果。\n'+self.result['recommendation']['name'])
            self.revision+=1;self.probability_data=None;self.space_data=None
            if self.worker and self._evaluation_requested:self._coverage_pending=True;self.coverage_timer.start()
            if self.data:self.request_preview()
        elif (deform or live_motion) and (self.deformation_toggle.isChecked() or self.drc_toggle.isChecked()):self.request_preview()
        elif intent:
            if intent[0]=='frame':self.present_frame(intent[1])
            else:self.animate_view(*intent[1:])
        elif self.data:self.plot_layout(preserve=True)
    def style_for(self,key):
        style=layer_style(self.roles().get(key,'不参与'));saved=self.layer_styles.get(key,{})
        if isinstance(saved,dict):
            if QtGui.QColor(str(saved.get('color',''))).isValid():style['color']=saved['color']
            for field,low,high in [('alpha',0,1),('width',.1,5)]:
                value=saved.get(field)
                if isinstance(value,(int,float)) and math.isfinite(value):style[field]=min(high,max(low,value))
            if isinstance(saved.get('fill'),bool):style['fill']=saved['fill']
            if saved.get('linestyle') in ('-','--',':'):style['linestyle']=saved['linestyle']
        return style
    def edit_layer_style(self,row):
        if self.loading or not 0<=row<len(self._layer_keys):return
        key=self._layer_keys[row];dialog=LayerStyleDialog(key,self.style_for(key),self)
        if run_dialog(dialog)==ACCEPTED:self.layer_styles[key]=dialog.value();self.plot_layout(preserve=True)
    def roles(self):
        basic={key:box.currentText() for key,box in self.role_boxes.items()}
        return resolve_stages(self.data,basic,self.ordered_layers(),self.stage_hints) if self.data else basic
    def ordered_layers(self):
        header=self.layer_table.verticalHeader()
        return [self._layer_keys[header.logicalIndex(i)] for i in range(header.count())] if len(self._layer_keys)==header.count() else self._layer_keys.copy()
    def layer_moved(self,*_):
        if self.loading or self._reordering:return
        self.case=None;self.connections=None
        try:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        except ValueError:pass
        self.refresh_motion_table();self.apply_automatic();self.invalidate();self.plot_layout(preserve=True)
    def motions(self):return {key:default_motion(role) for key,role in self.roles().items()}
    def params(self):
        data={k:w.value() for k,w in self.fields.items()}
        if self.data:data.update(self.auto_values)
        data['target_nsigma']=math.inf if self.target_sigma.currentText() in ('∞','无穷大','Infinity') else float(self.target_sigma.currentText())
        if 0<data['target_nsigma']<math.inf:self.reference_nsigma=data['target_nsigma']
        mode,x,y=self.pivot_editor.value()
        data.update(reference_nsigma=self.reference_nsigma,rotation_center=mode,rotation_x_um=x,rotation_y_um=y,die_pivots=self.per_die_pivots,per_die_enabled=self.per_die_toggle.isChecked(),die_bounds=self.per_die_values if self._per_die_initialized else [])
        data.update(input_nsigma=data['target_nsigma'],fixed_via_assignment=self.fixed_assignment);return Parameters.from_dict(data)
    def per_die_changed(self,*_):
        if self.per_die_toggle.isChecked() and not self._per_die_initialized and not self.loading:
            self.per_die_values=[];self.per_die_pivots=[];self.per_die_table.setRowCount(0);self._per_die_initialized=True
        self.per_die_table.setVisible(self.per_die_toggle.isChecked());self.pivot_editor.setEnabled(not self.per_die_toggle.isChecked());self.sync_per_die()
        for key in ('single_bound_x_um','single_bound_y_um','multi_bound_x_um','multi_bound_y_um','bound_angle_deg'):
            self.fields[key].setEnabled(not self.per_die_toggle.isChecked())
        if not self.loading:self.invalidate()
    def sync_per_die(self):
        count=len(self.case['dies']) if self.case else 0
        prefix='single' if count==1 else 'multi'
        default=[self.fields[prefix+'_bound_x_um'].value(),self.fields[prefix+'_bound_y_um'].value(),self.fields['bound_angle_deg'].value()]
        self.per_die_values=(self.per_die_values+[default.copy() for _ in range(count)])[:count]
        self.per_die_pivots=(self.per_die_pivots+[self.pivot_editor.value() for _ in range(count)])[:count]
        if self.per_die_table.rowCount()==count and getattr(self,'_die_table_source',None)==id(self.data):return
        self._die_table_source=id(self.data);self.per_die_table.setRowCount(count)
        for i,values in enumerate(self.per_die_values):
            self.per_die_table.setItem(i,0,W.QTableWidgetItem(f'DIE {i+1}'))
            for j,value in enumerate(values):
                spin=W.QDoubleSpinBox();spin.setRange(0,180 if j==2 else 10000);spin.setDecimals(4 if j==2 else 3);spin.setValue(value);spin.setKeyboardTracking(False)
                spin.valueChanged.connect(lambda value,i=i,j=j:self.set_die_bound(i,j,value));self.per_die_table.setCellWidget(i,j+1,spin)
            pivot=PivotEditor();pivot.setValue(self.per_die_pivots[i]);pivot.valueChanged.connect(lambda i=i,w=pivot:self.set_die_pivot(i,w.value()));self.per_die_table.setCellWidget(i,4,pivot)
        for j in range(4):self.per_die_table.setColumnWidth(j,72)
        self.per_die_table.setColumnWidth(4,190);self.per_die_table.resizeRowsToContents()
    def set_die_pivot(self,i,value):
        self.per_die_pivots[i]=value;self.per_die_table.resizeRowsToContents()
        if not self.loading:self.invalidate()
    def rotation_centers(self):
        if self.current_frame is not None and "rotation_centers" in self.current_frame:return self.current_frame["rotation_centers"]
        return rotation_centers(self.case,self.params())
    def marker_size(self,prefix):
        size=getattr(self.view_settings,prefix+'_marker_size')
        if getattr(self.view_settings,prefix+'_marker_absolute'):
            size*=self.ax.bbox.width/max(np.ptp(self.ax.get_xlim()),1e-20)*72/self.figure.dpi
        return size**2
    def set_die_bound(self,i,j,value):
        self.per_die_values[i][j]=value
        if not self.loading:self.invalidate()
    def task_progress(self,worker,n,text):
        self._tasks[id(worker)]=(n,text);self.progress.show();self.progress.setRange(0,100);self.progress.setValue(n);self.progress.setFormat(f'{n}% · '+text[:36]);self.statusBar().showMessage(text)
        self.log_bus.emit(text)
    def watch_worker(self,worker,name):
        import time
        start=time.perf_counter();self.task_progress(worker,0,name+'：开始')
        worker.progress.connect(lambda n,t:self.task_progress(worker,n,t))
        worker.failed.connect(lambda text:self.log_bus.emit(name+'：'+text,'ERROR'))
        def finish():
            self._tasks.pop(id(worker),None);self.log_bus.emit(f'{name}：'+('已取消' if worker.cancelled.is_set() else '线程退出')+f'，耗时 {time.perf_counter()-start:.3f} s')
            if self._closing and not self._tasks:QtCore.QTimer.singleShot(0,self.close)
            if not self._tasks:self.progress.hide();self.cleanup_retired_sessions()
            else:
                n,t=next(reversed(self._tasks.values()));self.progress.setValue(n);self.progress.setFormat(f'{n}% · '+t[:36])
        worker.finished.connect(finish)
    def reset_session_cache(self):
        self.stop_animation()
        if self.preview_worker:self.preview_worker.cancelled.set()
        if self.motion_worker:self.motion_worker.cancelled.set()
        self.retired_sessions.append((self.session_values,self.session_engines))
        from .session_cache import SessionCache
        self.session_values=SessionCache();self.session_engines={};self.engine=None
        self.cleanup_retired_sessions()
    def cleanup_retired_sessions(self):
        if self._tasks:return
        for values,engines in self.retired_sessions:
            values.close()
            for engine in engines.values():engine.close()
        if self.retired_sessions:
            from .deformation import _cached_native_region
            from .route_adaptation import boundary_tolerant
            from .distribution import normalization
            _cached_native_region.cache_clear();boundary_tolerant.cache_clear();normalization.cache_clear()
            from . import native_backend
            if native_backend._backend is not None:native_backend._backend.execute({'op':'clear'})
        self.retired_sessions.clear()
    def clear_preview(self):
        self.extremes=None;self.active_preview=None;self.owner_cache=None;self.page_items=[];self.preview_page=0;self.state_result=None
        self.preview_ticket+=1;self.preview_pending=None;self._prepared_motion=None;self.engine=None;self.current_frame=None;self.shown_states=None;self.report_dirty=True
        self.extreme_table.blockSignals(True);self.extreme_table.clearContents();self.extreme_table.setRowCount(1);self.extreme_table.setCurrentCell(0,0);self.extreme_table.blockSignals(False)
        self.state_table.setRowCount(0);self.issue_table.setRowCount(0);self._issue_table_stamp=None;self.page_summary.setText('尚未评估')
    def coverage_changed(self,*_):
        if self.loading:return
        if self.target_sigma.currentText() not in ('0','∞'):self.reference_nsigma=float(self.target_sigma.currentText())
        self.stop_animation();self.revision+=1;self.engine=None;self.connections=None
        self.probability_data=None;self.space_data=None;self.preview_ticket+=1;self.preview_pending=None
        if self.preview_worker:self.preview_worker.cancelled.set()
        self._coverage_pending=True;self.coverage_timer.start()
        self.statusBar().showMessage('覆盖级别已改变，正在更新概率、Space Map和窗口判定…')
    def refresh_coverage(self):
        if not self._coverage_pending or not self.data:return
        if self.worker is not None:self.coverage_timer.start();return
        self._coverage_pending=False
        if self.result is not None or self._evaluation_requested:self.run_evaluation(automatic=True)
        else:self.request_preview()
    def invalidate(self,*_):
        if self.loading:return
        self.revision+=1;self.dirty=self.result is not None;self.engine=None;self.connections=None;self.probability_data=None;self.space_data=None
        self.statusBar().showMessage('参数已修改；保留上次计算结果。预演控制区使用当前工艺，重新评估后更新统计结论。')
        if self.result:
            self.verdict.setText('参数已修改：以下为上次计算结果。\n'+self.result['recommendation']['name'])
        if self.data and (self.active_preview is not None or self.manual_states is not None or self.probability_toggle.isChecked() or self.space_toggle.isChecked() or self.deformation_toggle.isChecked() or self.drc_toggle.isChecked()):
            try:self.request_preview()
            except ValueError:pass
    def drop_result(self):
        self.result=None;self.clear_preview();self.manual_states=None;self.dirty=False;self.probability_data=None;self.space_data=None
        self.verdict.setText('正在评估…');self.scheme_table.setRowCount(0);self.die_table.setRowCount(0)
        self.scheme_figure.clear();self.scheme_canvas.draw_idle();self.detail.clear();self.plot_layout(preserve=True);self.update_histograms()
    def error(self,text):
        self.log_bus.emit(text,'ERROR');W.QMessageBox.warning(self,'操作未完成',str(text))
    def start_worker(self,fn,success,interactive=False):
        if self.worker is not None:self.error('上一项操作尚未完成');return
        self.progress.show();self.progress.setValue(0);self.run_button.setEnabled(False);self.controls.setEnabled(interactive);self.job_combo.setEnabled(False);self.save_button.setEnabled(False)
        worker=Worker(fn,self);self.worker=worker
        self.watch_worker(worker,'主计算')
        worker.done.connect(success);worker.failed.connect(lambda text:(self.refresh_jobs(),self.error(text)));worker.finished.connect(lambda:self.finish_worker(worker));worker.start()
    def finish_worker(self,worker):
        self.controls.setEnabled(True);self.job_combo.setEnabled(True);self.save_button.setEnabled(True);self.run_button.setEnabled(True)
        if self.worker is worker:self.worker=None
        worker.deleteLater()
        if self._coverage_pending:self.coverage_timer.start()
    def open_gds(self):
        path,_=W.QFileDialog.getOpenFileName(self,'输入GDS','','GDSII (*.gds *.gdsii *.GDS)')
        if path:self.load_gds(path)
    def load_gds(self,path,top=None,project=None,project_path=None):
        def read_and_prepare(progress):
            data=read_gds(path,top,progress)
            if isinstance(data,dict):return data
            settings=project if project and (not project.get('sha256') or project['sha256']==data.sha256) else {}
            roles=settings.get('roles',data.auto_roles)
            progress(35,'构建几何索引与图层缓存');data.geometry_cache=GeometryCache(data)
            data.prepared_roles=roles;data.prepared_auto=automatic_parameters(data,roles)
            try:data.prepared_case=build_case(data,roles,layer_order=settings.get('layer_order'))
            except ValueError:data.prepared_case=None
            return data
        def accepted(data):
            settings=project
            if isinstance(data,dict):
                name,ok=W.QInputDialog.getItem(self,'选择顶层Cell','待评估顶层：',data['top_names'],0,False)
                if ok:QtCore.QTimer.singleShot(10,lambda:self.load_gds(path,name,project,project_path))
                return
            if settings and settings.get('sha256') and settings['sha256']!=data.sha256:
                self.error('GDS已改变，请重新设置图层。');settings=None
            self.set_data(data,settings)
            self.project_path=Path(project_path).resolve() if settings and project_path and Path(project_path).resolve().parent==self.jobs_dir.resolve() else None
            self.refresh_jobs()
        self.start_worker(read_and_prepare,accepted)
    def set_data(self,data,project=None):
        self.reset_session_cache()
        project=normalize_project(project) if project else {};pars=Parameters.from_dict(project.get('parameters',{}));self.manual_states=None;self.probability_data=None;self.space_data=None;self.dirty=False
        self.coverage_timer.stop();self._coverage_pending=False;self._evaluation_requested=False
        self.loading=True
        try:
            self.data=data;self.gds_label.setText(Path(data.path).name+'\n'+str(Path(data.path).resolve()));self.gds_label.setToolTip(str(Path(data.path).resolve()))
            self.view_settings=ViewSettings.from_dict(project.get('settings'));self.load_view_settings();self.layer_styles=project.get('layer_styles',{})
            self.case=None;self.result=None;self.geometry_cache=getattr(data,'geometry_cache',None);self.connections=None;self.reset_plot();self.top.clear();self.top.addItems(data.top_names);self.top.setCurrentText(data.top_name)
            selected_roles=project.get('roles',data.auto_roles)
            self.stage_hints=project.get('stage_hints',{})
            self.role_boxes={};self.visible_boxes={};self._layer_keys=stack_order(data,selected_roles,project.get('layer_order'))
            self.layer_table.setRowCount(0);self.layer_table.setRowCount(len(self._layer_keys))
            header=self.layer_table.verticalHeader()
            for i in range(header.count()):header.moveSection(header.visualIndex(i),i)
            for i,key in enumerate(self._layer_keys):
                role=selected_roles.get(key,'不参与');check=W.QCheckBox();check.setChecked(project.get('visibility',{}).get(key,role not in ('不参与','非金属图形')))
                check.toggled.connect(lambda checked,k=key:self.visibility_changed(k));self.visible_boxes[key]=check;self.layer_table.setCellWidget(i,0,check);self.layer_drag.watch(check)
                self.layer_table.setItem(i,1,W.QTableWidgetItem(key));self.layer_table.setItem(i,2,W.QTableWidgetItem(str(len(data.layers[key]))))
                box=W.QComboBox();box.addItems(ROLES);box.setCurrentText(role)
                self.role_boxes[key]=box;self.layer_table.setCellWidget(i,3,box);self.layer_drag.watch(box)
                box.currentTextChanged.connect(lambda _,k=key:self.role_changed(k))
            for j,width in enumerate([38,82,58,170]):self.layer_table.setColumnWidth(j,width)
            self.layer_table.horizontalHeader().setStretchLastSection(True)
            for key,w in self.fields.items():w.setValue(getattr(pars,key))
            self.reference_nsigma=pars.reference_nsigma;self.per_die_pivots=[list(v) for v in pars.die_pivots];self.pivot_editor.setValue([pars.rotation_center,pars.rotation_x_um,pars.rotation_y_um])
            self.target_sigma.setCurrentText('∞' if math.isinf(pars.target_nsigma) else f'{pars.target_nsigma:g}')
            self.fixed_assignment=pars.fixed_via_assignment
            self.per_die_values=[list(v) for v in pars.die_bounds];self._per_die_initialized=bool(pars.die_bounds);self.per_die_table.setRowCount(0);self.per_die_toggle.blockSignals(True);self.per_die_toggle.setChecked(pars.per_die_enabled);self.per_die_toggle.blockSignals(False)
            if getattr(data,'prepared_roles',None)==self.roles() and getattr(data,'prepared_case',None) and data.prepared_case['connections'].order==self.ordered_layers():self.case=data.prepared_case
            else:
                try:self.case=build_case(data,self.roles(),layer_order=self.ordered_layers())
                except ValueError:pass
            self.apply_automatic();self.per_die_changed();self.refresh_motion_table(project.get('motions',{}))
        finally:self.loading=False
        self.clear_preview();self.invalidate();self.plot_layout()
    def role_changed(self,key):
        if self.loading:return
        self.stage_hints.pop(key,None)
        self.visible_boxes[key].blockSignals(True);self.visible_boxes[key].setChecked(self.role_boxes[key].currentText()!='不参与');self.visible_boxes[key].blockSignals(False)
        old=self.motions();old[key]=default_motion(self.roles()[key]);self.case=None
        try:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        except ValueError:pass
        self.loading=True
        try:self.apply_automatic();self.refresh_motion_table(old)
        finally:self.loading=False
        self.invalidate();self.plot_layout(preserve=True)
    def apply_automatic(self):
        if not self.data:return
        if getattr(self.data,'prepared_roles',None)==self.roles():values,sources=self.data.prepared_auto
        else:values,sources=automatic_parameters(self.data,self.roles())
        self.auto_values=values;self.automatic_sources=sources
        for key in ('panel_layers','wafer_layers','via_diameter_um'):
            widget=self.fields.get(key)
            if widget is None:continue
            widget.setEnabled(key not in values)
            widget.setVisible(key not in values)
            row_label=widget.parentWidget().layout().labelForField(widget)
            if row_label:row_label.setVisible(key not in values)
            if key in values:
                widget.setValue(values[key]);widget.setToolTip('自动：'+sources[key])
        single=sum(len(v) for k,v in self.data.layers.items() if self.roles().get(k)=='芯片边框')==1
        for key in ('single_bound_x_um','single_bound_y_um','multi_bound_x_um','multi_bound_y_um'):
            widget=self.fields[key];visible=key.startswith('single')==single;widget.setVisible(visible)
            label_widget=widget.parentWidget().layout().labelForField(widget)
            if label_widget:label_widget.setVisible(visible)
        count=len(self.case['dies']) if self.case else sum(len(v) for k,v in self.data.layers.items() if self.roles().get(k)=='芯片边框')
        current=self.manual_die.currentIndex();self.manual_die.clear();self.manual_die.addItems([f'DIE {i+1}' for i in range(count)]);self.manual_die.setCurrentIndex(max(0,min(current,count-1)))
        self.sync_per_die()
        profile=resource_profile()
        skipped=len(getattr(self.data,'ignored_geometry',[]));repaired=len(getattr(self.data,'repaired_geometry',[]))
        self.statusBar().showMessage(f'{Path(self.data.path).name}；{self.data.count:,}个有效图形；修复{repaired}个，忽略{skipped}个异常图形；{profile["cpu"]}逻辑CPU → 最多{profile["workers"]}工作线程')
    def refresh_motion_table(self,saved=None):
        self.motion_table.setRowCount(len(self.role_boxes));self.motion_boxes={}
        for i,key in enumerate(self.ordered_layers()):
            role=self.roles()[key];owners=(self.case or {}).get('ownership',{}).get(key,[])
            if role in ('运动RDL','运动过孔'):
                counts=[f'DIE{owner+1}: {int(np.sum(owners==owner))}个' for owner in sorted(set(owners)) if owner>=0]
                unresolved=int(np.sum(np.asarray(owners)<0))
                if unresolved:counts.append(f'{unresolved}个归属待确认')
                detail='；'.join(counts) or '设置边框与上下层序后追溯'
                motion='按下层连接随芯片'
            else:motion='随对应芯片' if role=='芯片边框' else '固定';detail='由用途确定'
            for j,value in enumerate((key,role,motion,detail)):
                item=W.QTableWidgetItem(value);item.setToolTip(value);self.motion_table.setItem(i,j,item)
        for j,width in enumerate((70,100,150)):self.motion_table.setColumnWidth(j,width)
        self.motion_table.horizontalHeader().setStretchLastSection(True)
        if self.active_preview is None and self.manual_states is None:self.plot_layout(preserve=True)
    def visibility_changed(self,key):
        if self.loading:return
        self.plot_layout(preserve=True);self.update_histograms()
    def top_changed(self,name):
        if not self.loading and self.data and name!=self.data.top_name:self.load_gds(self.data.path,name)
    def validate_geometry(self):
        if not self.data:self.error('请先打开GDS');return
        try:
            self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers());self.refresh_motion_table(self.motions());self.plot_layout()
            self.statusBar().showMessage(f"几何有效：{len(self.case['dies'])}颗芯片；运动过孔{sum(map(len,self.case['pins']))}个；固定过孔{len(self.case['fixed_via'])}个")
        except Exception as e:self.error(e)
    def reset_plot(self):
        self.stop_animation()
        if hasattr(self,'mouse_die'):self.mouse_die.exit();self.mouse_die.pending=False;self.mouse_die.full_pending=False;self.mouse_die.timer.stop();self.mouse_die.idle.stop()
        if hasattr(self,'selection'):self.selection.reset()
        self._legend_stamp=None
        self.clear_space_artists()
        for artist in list(self.ax.collections)+list(self.ax.texts)+list(self.ax.patches):artist.remove()
        self.deformation_artists={};self.layer_artists={};self.die_labels=[];self.issue_artists=[];self.shift_artist=None;self.failure_artist=None;self._artist_frame=None;self.invalid_artists=[]
        for artist in self.probability_artists:
            if artist.axes is not None:artist.remove()
        self.probability_artists=[];self.probability_stamp=None;self.zone_artists=[]
        if self.legend is not None:self.legend.remove();self.legend=None
        self.space_stamp=None
        self._default_view=None
    def set_view(self,center,width,height):
        ratio=max(1,self.ax.bbox.width)/max(1,self.ax.bbox.height)
        width=max(float(width),float(height)*ratio);height=width/ratio
        self.ax.set_xlim(center[0]-width/2,center[0]+width/2)
        self.ax.set_ylim(center[1]-height/2,center[1]+height/2)
    def stop_animation(self):
        if not hasattr(self,'transition'):return
        running=self.transition.timer.isActive();self.transition.stop();self._motion_intent=None;self.motion_generation+=1;self.motion_pending=None
        if self._animation_cleanup:
            cleanup=self._animation_cleanup;self._animation_cleanup=None;cleanup()
        if running and getattr(self,'_interpolated_states',None) is not None and self.engine is not None:
            self._interpolated_states=None
            self.plot_layout(preserve=True)
        elif running:self.canvas.draw_idle()
    def animate_view(self,center,width,height):
        self.stop_animation()
        self._motion_intent=('view',center,width,height)
        if not self.view_settings.animation_enabled:
            self._motion_intent=None;self.set_view(center,width,height);self.viewport_changed();return
        start_center=np.array([np.mean(self.ax.get_xlim()),np.mean(self.ax.get_ylim())]);start_size=np.array([np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim())])
        end_center=np.asarray(center);end_size=np.asarray([width,height]);ratio=self.ax.bbox.width/max(1,self.ax.bbox.height)
        end_size[0]=max(end_size[0],end_size[1]*ratio);end_size[1]=end_size[0]/ratio
        # View interpolation uses geometric scaling to keep zoom speed uniform.
        def update(t):
            size=np.exp(np.log(start_size)*(1-t)+np.log(end_size)*t)
            self.set_view(start_center*(1-t)+end_center*t,*size);self.viewport_changed()
        def complete():self._motion_intent=None;self.set_view(end_center,*end_size);self.viewport_changed(force=True)
        from .animation import motion_timing
        distance=float(np.linalg.norm(end_center-start_center)+np.max(abs(end_size-start_size))*.5)
        duration,steps=motion_timing(self.view_settings,distance,float(max(start_size)),True)
        self.transition.start(duration,steps,update,complete)
    def show_frame_status(self,frame):
        message=('预演已更新' if frame is not None else '名义位置')+f' · 当前所选方案 {len(self.filtered_issues)} 处接触或风险'
        if frame is not None:
            from .statistics import mean_and_bounds
            params=self.params();mu,_=mean_and_bounds(params,len(frame['states']));bounds=np.array([mean_and_bounds(params.for_die(i),len(frame['states']))[1] for i in range(len(frame['states']))])
            if np.any(abs(frame['states']-mu)>bounds+1e-10):message+=' · 手动状态超出输入最大幅度；概率云仍为配置分布'
        if frame and self.deformation_toggle.isChecked() and 'deformation' in frame:
            q=frame['deformation'];message+=f" · 型变 {q['accepted_count']} 个铜连通区，未解 {len(q['diagnostics'])} 个"
        if frame and frame.get('deformation',{}).get('search_quality')=='interactive':message+=' · 交互候选已复核，停止操作后完整搜索'
        self.statusBar().showMessage(message)
    def present_frame(self,frame):
        self.stop_animation()
        self._motion_intent=('frame',frame)
        def complete():
            self._motion_intent=None;self.transition.stop();self.motion_generation+=1;self.motion_pending=None
            if self._animation_cleanup:
                cleanup=self._animation_cleanup;self._animation_cleanup=None;cleanup()
            self._interpolated_states=None;self.current_frame=frame;self.plot_layout(preserve=True);self.update_histograms()
            self.show_frame_status(frame)
        if not self.view_settings.animation_enabled or self.engine is None or self.case is None or (self.deformation_toggle.isChecked() and frame is not None):
            complete();return
        count=len(self.case['dies']);start=np.zeros((count,3)) if self.shown_states is None else self.shown_states.copy()
        end=np.zeros((count,3)) if frame is None else frame['states']
        if np.array_equal(start,end):complete();return
        from .animation import motion_timing
        radii=np.array([np.linalg.norm(np.asarray(d.exterior.coords)-self.engine.bound.centers[i],axis=1).max() for i,d in enumerate(self.case['dies'])])
        # Include external fanout: actual vertices determine the longest travel.
        radii=np.maximum(radii,self.engine.bound.owner_radii if hasattr(self.engine.bound,'owner_radii') else radii)
        travel=float(np.max(np.linalg.norm(end[:,:2]-start[:,:2],axis=1)+radii*np.abs(end[:,2]-start[:,2])))
        duration,steps=motion_timing(self.view_settings,travel,max(np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim())),False)
        def update(t):
            states=start*(1-t)+end*t
            if (self.deformation_toggle.isChecked() and self.result) or self.drc_toggle.isChecked():
                self.motion_pending=(self.motion_generation,self.revision,self.engine,states.copy());self.launch_motion_frame()
            else:self.show_motion_frame(self.engine.frame(states))
        self.statusBar().showMessage('插值运动中；控制区与接触标记随当前帧实时更新。')
        self.transition.start(duration,steps,update,complete)
    def show_motion_frame(self,frame):
        self._interpolated_states=frame['states'];self.current_frame=frame;self._in_animation=True
        try:self.plot_layout(preserve=True)
        finally:self._in_animation=False
    def launch_motion_frame(self):
        if self.motion_worker is not None or self.motion_pending is None:return
        generation,revision,engine,states=self.motion_pending;self.motion_pending=None
        worker=Worker(lambda progress:engine.frame(states,progress,quick=True),self);self.motion_worker=worker;self.watch_worker(worker,'插值帧')
        def accepted(frame):
            if generation==self.motion_generation and revision==self.revision and self.transition.timer.isActive():self.show_motion_frame(frame)
        def finished():
            self.motion_worker=None;worker.deleteLater();self.launch_motion_frame();self.launch_preview()
        worker.done.connect(accepted);worker.finished.connect(finished)
        worker.failed.connect(lambda message:self.statusBar().showMessage('型变中间帧跳过：'+message.split('\n')[0]));worker.start()
    def deformation_changed(self,*_):
        if self.loading:return
        if self.deformation_toggle.isChecked() and self.result is None:
            self.statusBar().showMessage('预演分析已启用；先运行方案评估，再选择极端情况。');return
        self.request_preview()
    def escape_action(self):
        if hasattr(self,'mouse_die') and self.mouse_die.exit():return
        self.selection.clear()
    def drc_changed(self,*_):
        if self.loading:return
        if self.drc_toggle.isChecked():self.request_preview()
        else:self.plot_layout(preserve=True)
    def fit_plot(self):
        if not self.data:return
        if self.case:bounds=self.case['board'].bounds
        else:
            values=[np.asarray(a) for k,v in self.data.layers.items() for a in v if self.visible_boxes[k].isChecked()]
            if not values:return
            points=np.concatenate(values);bounds=(*points.min(axis=0),*points.max(axis=0))
        x0,y0,x1,y1=bounds
        self.set_view(((x0+x1)/2,(y0+y1)/2),max(1,x1-x0)*1.1,max(1,y1-y0)*1.1)
        self._default_view=(self.ax.get_xlim(),self.ax.get_ylim())
    def resize_plot(self,event=None):
        if self._resizing or not self.data:return
        self._resizing=True
        try:
            limits=(self.ax.get_xlim(),self.ax.get_ylim())
            home=self._default_view is None or np.allclose(limits,self._default_view,rtol=1e-7,atol=1e-4)
            self.fit_plot()
            if not home:
                center=[np.mean(v) for v in limits]
                # Preserve vertical extent while adapting the horizontal field of view.
                height=np.ptp(limits[1]);ratio=max(1,self.ax.bbox.width)/max(1,self.ax.bbox.height)
                self.set_view(center,height*ratio,height)
            self.update_die_label_visibility();self.canvas.draw_idle()
        finally:self._resizing=False
    def queue_viewport(self,*_):
        if not self._plotting and self.data:self.viewport_timer.start()
    def viewport_changed(self,force=False):
        if self._plotting or not self.data:return
        self._in_viewport=True
        try:self.plot_layout(preserve=True)
        finally:self._in_viewport=False
    def plot_layout(self,preserve=False):
        if self._plotting:return
        self._plotting=True
        try:self._plot_layout(preserve)
        finally:self._plotting=False
    def _plot_layout(self,preserve=False):
        self.ax.set(xlabel='',ylabel='');self.ax.set_aspect('equal',adjustable='datalim');self.ax.grid(alpha=.12)
        if not self.data:self.canvas.draw_idle();return
        roles=self.roles();motions=self.motions();order=self.ordered_layers();frame=self.current_frame
        if not preserve or self._default_view is None:self.fit_plot()
        from .viewport import ViewportIndex,expanded_bounds,overlaps
        self.view_bounds=expanded_bounds(self.ax.get_xlim(),self.ax.get_ylim())
        vstamp=(id(self.data),id(self.case),self.rotation_centers().tobytes() if self.case else None)
        if vstamp!=self._viewport_stamp:
            self.geometry_cache=self.geometry_cache or GeometryCache(self.data)
            self.viewport_index=ViewportIndex(self.geometry_cache,self.case,motions,self.params());self._viewport_stamp=vstamp
            if self.viewport_index.bound is not None:self.viewport_index.bound.centers=self.rotation_centers()
        states=frame['states'] if frame else None
        stamp=(id(self.data),id(frame),self.view_bounds);changed=stamp!=self._artist_frame;self.rendered_layers={}
        self.preview_applied=frame is not None;self.shown_states=frame['states'] if frame else None
        for rank,key in enumerate(order):
            values=frame['layers'][key]['polygons'] if frame and key in frame['layers'] else self.data.layers[key]
            role=roles[key];sty=self.style_for(key);visible=self.visible_boxes[key].isChecked()
            polys=values
            ids=self.viewport_index.query(key,self.view_bounds,states) if visible else np.empty(0,int)
            deformation=frame.get('deformation',{}) if frame and self.deformation_toggle.isChecked() else {}
            replacements=deformation.get('replacements',{}).get(key,[])
            hidden={i for q in replacements for i in q['indices']}
            ids=np.array([i for i in ids if i not in hidden],int)
            drawn=[polys[i] for i in ids]
            if key not in self.layer_artists:
                self.layer_artists[key]=PolyCollection([]);self.ax.add_collection(self.layer_artists[key])
            artist=self.layer_artists[key]
            geometry_stamp=(id(values),ids.tobytes())
            if getattr(artist,'_geometry_stamp',None)!=geometry_stamp:artist.set_verts(drawn);artist._visible_ids=ids.tobytes();artist._geometry_stamp=geometry_stamp
            artist.set_facecolor(sty['color'] if sty['fill'] else 'none');artist.set_edgecolor(sty['color'] if not sty['fill'] else 'none')
            artist.set_alpha(sty['alpha']);artist.set_linewidth(sty['width']);artist.set_linestyle(sty.get('linestyle','-'));artist.set_visible(visible)
            artist.set_zorder(2+rank/max(1,len(order)) if role in PHYSICAL else sty['z'])
            if default_motion(role)=='fixed':artist._static_preview=(geometry_stamp,str(sty),artist.get_zorder())
            elif hasattr(artist,'_static_preview'):del artist._static_preview
            if visible:self.rendered_layers[key]=polys
            if key not in self.deformation_artists:
                base=PatchCollection([],linewidth=0);dark=PatchCollection([],linewidth=0);adapt=PatchCollection([],facecolor='none',edgecolor='#38536f',linestyle=(0,(4,3)),linewidth=.9)
                self.ax.add_collection(base);self.ax.add_collection(dark);self.ax.add_collection(adapt);self.deformation_artists[key]=(base,dark,adapt)
            base,dark,adapt=self.deformation_artists[key]
            visible_replacements=[q for q in replacements if overlaps(q['geometry'].bounds,self.view_bounds)]
            for overlay,part in ((base,'geometry'),(dark,'dark')):
                overlay.set_paths([PathPatch(geometry_path(q[part])) for q in visible_replacements]);overlay.set_visible(visible and bool(visible_replacements));overlay.set_zorder(artist.get_zorder()+(.002 if part=='dark' else .001))
                color=np.array(matplotlib.colors.to_rgb(sty['color']))
                overlay.set_facecolor(color if part=='geometry' else color*.70);overlay.set_alpha(sty['alpha'])
            adapt.set_alpha(sty['alpha']);adapt.set_paths([PathPatch(geometry_path(q['adaptation'])) for q in visible_replacements if q.get('adaptation') is not None and not q['adaptation'].is_empty]);adapt.set_visible(visible and bool(visible_replacements));adapt.set_zorder(artist.get_zorder()+.003)
        self._artist_frame=stamp
        for artist in self.invalid_artists:artist.remove()
        self.invalid_artists=[]
        for q in getattr(self.data,'ignored_geometry',[]):
            if not self.visible_boxes[q['layer']].isChecked():continue
            x0,y0,x1,y1=q['bounds'];pad=max(1.,self.data.dbu_um*2)
            path=np.array([[x0-pad,y0-pad],[x1+pad,y0-pad],[x1+pad,y1+pad],[x0-pad,y1+pad],[x0-pad,y0-pad]])
            artist=LineCollection([path],colors='#d72b91',linestyles=(0,(3,3)),linewidths=.7,zorder=13)
            self.ax.add_collection(artist);self.invalid_artists.append(artist)
        segments=[]
        if frame:
            for key in order:
                if roles[key]!='运动过孔' or not self.visible_boxes[key].isChecked():continue
                q=self.geometry_cache.layers[key]
                if motions.get(key)=='fixed':continue
                mask=np.zeros(len(q['centers']),bool)
                for region in frame['contact']['regions']:
                    if region['key']==key:mask[region['index']]=True
                segments.append(np.stack((q['centers'][mask],frame['layers'][key]['centers'][mask]),axis=1))
        self.connector_segments=np.concatenate(segments) if segments else np.empty((0,2,2))
        if self.shift_artist is None:self.shift_artist=LineCollection([],colors='#d9893e',linestyles=(0,(4,3)),linewidths=1.05,zorder=8);self.ax.add_collection(self.shift_artist)
        self.shift_artist.set_segments(self.connector_segments)
        scheme=self.issue_scheme.currentData()
        # Reuse collections across animation frames: no Qt table churn or Matplotlib allocation.
        if not self.zone_artists:
            for color in ('#3c9995','#aa78ab'):
                artist=LineCollection([],colors=color,linestyles=(0,(4,3)),linewidths=.85,zorder=7)
                self.ax.add_collection(artist);self.zone_artists.append(artist)
        for name,artist in zip(('annulus','spacing'),self.zone_artists):
            paths=[np.asarray(q[name].exterior.coords) for q in frame['contact']['regions'] if self.visible_boxes[q['key']].isChecked() and overlaps(q['bounds'] if 'bounds' in q else q[name].bounds,self.view_bounds)] if frame else []
            artist.set_segments(paths);artist.set_visible(bool(paths))
        entries=frame['contact']['issues'] if frame else (self.result or {}).get('issues',[])
        self.display_issues=[q for q in entries if q['scheme']==scheme and self.visible_boxes.get(q.get('source_layer')) is not None and self.visible_boxes[q['source_layer']].isChecked() and self.visible_boxes.get(q.get('target_layer')) is not None and self.visible_boxes[q['target_layer']].isChecked()]
        if not self.issue_artists:
            area=PatchCollection([],facecolor='#e32e43',edgecolor='#ba2535',linewidth=.45,zorder=8.7);area._intrusion=True;self.ax.add_collection(area,autolim=False)
            circles=self.ax.scatter([],[],s=66,facecolors='none',edgecolors='#bc243e',linewidths=1.6,zorder=11,path_effects=[patheffects.withStroke(linewidth=3,foreground='white',alpha=.85)])
            lines=LineCollection([],colors='#6c747d',linestyles=(0,(4,3)),linewidths=1.05,zorder=9,path_effects=[patheffects.withStroke(linewidth=2.3,foreground='white',alpha=.7)]);self.ax.add_collection(lines)
            self.issue_artists=[area,circles,lines]
        area,circles,lines=self.issue_artists
        patches=[PathPatch(geometry_path(q['geometry'])) for q in frame['contact'].get('intrusions',[]) if self.visible_boxes[q['key']].isChecked() and self.visible_boxes[q['target_layer']].isChecked() and overlaps(q['geometry'].bounds,self.view_bounds)] if frame else []
        area.set_paths(patches);area.set_alpha(self.view_settings.intrusion_alpha);area.set_visible(self.show_issues.isChecked() and bool(patches))
        circles.set_sizes([self.marker_size('contact')]);circles.set_edgecolor(self.view_settings.contact_marker_color);circles.set_offsets(np.asarray([q['point'] for q in self.display_issues]).reshape(-1,2));circles.set_visible(self.show_issues.isChecked() and bool(self.display_issues))
        lines.set_segments([[self.issue_origin(q),q['point']] for q in self.display_issues]);lines.set_visible(circles.get_visible())
        if not hasattr(self,'violation_artist') or self.violation_artist.axes is None:
            self.violation_artist=PatchCollection([],facecolor='#e956b5',edgecolor='#ca398f',linewidth=.5,alpha=.48,zorder=10.2);self.ax.add_collection(self.violation_artist)
        violations=frame.get('drc',frame.get('deformation',{}).get('violations',[])) if frame else []
        vp=[PathPatch(geometry_path(q['geometry'])) for q in violations if any(self.visible_boxes[k].isChecked() for k in q.get('layers',(q['layer'],))) and overlaps(q['geometry'].bounds,self.view_bounds)]
        self.violation_artist.set_paths(vp);self.violation_artist.set_visible(self.drc_toggle.isChecked() and bool(vp))
        diagnostics=frame.get('deformation',{}).get('diagnostics',[]) if frame and self.deformation_toggle.isChecked() else []
        self.deformation_diagnostics=diagnostics
        if not hasattr(self,'unadapted_artist') or self.unadapted_artist.axes is None:
            self.unadapted_artist=self.ax.scatter([],[],marker='x',s=65,color='#ba7628',linewidths=1.6,zorder=12,path_effects=[patheffects.withStroke(linewidth=3,foreground='white',alpha=.85)])
        points=[point for q in diagnostics if self.visible_boxes[q['layer']].isChecked() for point in q.get('points',[q['point']])]
        self.unadapted_artist.set_sizes([self.marker_size('unadapted')]);self.unadapted_artist.set_color(self.view_settings.unadapted_marker_color)
        self.unadapted_artist.set_offsets(np.asarray(points).reshape(-1,2));self.unadapted_artist.set_visible(bool(points))

        self.state_result=frame['checks'][scheme] if frame else None
        if self.failure_artist is not None:self.failure_artist.remove();self.failure_artist=None
        self.draw_probability();self.draw_space()
        if not getattr(self,'_in_animation',False):
            self.fill_issues()
            diagnostics=frame.get('deformation',{}).get('diagnostics',[]) if frame and self.deformation_toggle.isChecked() else []
            self.deformation_table.blockSignals(True);self.deformation_table.setRowCount(len(diagnostics))
            for i,q in enumerate(diagnostics):
                for j,v in enumerate([q['layer'],', '.join(str(n+1) for n in q['indices']),q['reason']]):self.deformation_table.setItem(i,j,W.QTableWidgetItem(v))
            self.deformation_table.blockSignals(False)
            self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.deformation_table),f'型变校验（{len(diagnostics)} 未适配）')
            self.deformation_table.resizeColumnsToContents();self.deformation_table.horizontalHeader().setStretchLastSection(True)
        if self.case:
            if len(self.die_labels)!=len(self.case['dies']):
                for label_ in self.die_labels:label_.remove()
                self.die_labels=[self.ax.text(0,0,f'DIE{i+1}',ha='center',va='center',fontsize=10,color='#3c5169',weight='medium',zorder=12,clip_on=True) for i in range(len(self.case['dies']))]
            for i,(label_,info) in enumerate(zip(self.die_labels,self.case['infos'])):
                point=np.array([info['cx'],info['cy']]);point=rigid_transform(point,self.rotation_centers()[i],self.shown_states[i]) if self.shown_states is not None else point;label_.set_position(point)
        if not preserve or self._default_view is None:self.fit_plot()
        self.refresh_legend();self.update_die_label_visibility()
        if not getattr(self,'_in_animation',False):
            for i,key in enumerate(self._layer_keys):
                sty=self.style_for(key);item=self.layer_table.item(i,1)
                if item:
                    background=QtGui.QColor(sty['color']).lighter(125);item.setForeground(QtGui.QColor('#ffffff' if background.lightnessF()<.55 else '#33465a'));item.setBackground(background);item.setToolTip(sty['label'])
                self.role_boxes[key].setToolTip(roles[key])
            self.ax.ticklabel_format(style='plain',useOffset=False,axis='both')
            self.ax.xaxis.set_minor_locator(DistanceMinorLocator());self.ax.yaxis.set_minor_locator(DistanceMinorLocator())
            self.ax.tick_params(which='major',labelsize=8,pad=2,length=4);self.ax.tick_params(which='minor',length=2,width=.55)
        if hasattr(self,'selection'):self.selection.draw(redraw=False)
        if hasattr(self,'mouse_die'):self.mouse_die.update_text()
        self.render_scene()
    def render_scene(self):
        if hasattr(self,'scene_renderer'):
            key=(tuple(self.ax.get_xlim()),tuple(self.ax.get_ylim()),tuple(self.ax.bbox.bounds),self._legend_stamp,self.space_stamp,
                 self.legend.get_visible() if self.legend is not None else False,tuple(a.get_visible() for a in self.space_artists))
            self.scene_renderer.draw(key,self.legend)
        else:self.canvas.draw_idle()
    def update_die_label_visibility(self,*_):
        if not self._default_view:return
        zoom=min(np.ptp(self.ax.get_xlim())/np.ptp(self._default_view[0]),np.ptp(self.ax.get_ylim())/np.ptp(self._default_view[1]))
        visible=zoom>=.995 and any(self.visible_boxes[k].isChecked() for k,r in self.roles().items() if r=='芯片边框')
        for label_ in self.die_labels:label_.set_visible(visible)
        if self.legend is not None:self.legend.set_visible(np.allclose(self.ax.get_xlim(),self._default_view[0],rtol=0,atol=1e-5) and np.allclose(self.ax.get_ylim(),self._default_view[1],rtol=0,atol=1e-5))
    def issue_origin(self,issue):
        origin=np.asarray(issue['origin'],float)
        if self.current_frame is not None:
            owner=int(issue['die'])-1
            if owner>=0:
                origin=rigid_transform(origin,self.rotation_centers()[owner],self.current_frame['states'][owner])
        return origin
    def refresh_legend(self):
        stamp=(tuple((k,self.visible_boxes[k].isChecked(),str(self.style_for(k))) for k in self.ordered_layers()),
               self.current_frame is not None,bool(getattr(self,'deformation_diagnostics',[])),self.probability_toggle.isChecked(),id(self.probability_data),self.view_settings.probability_cmap,self.view_settings.probability_alpha,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.view_settings.contact_marker_color,self.view_settings.unadapted_marker_color)
        if stamp==self._legend_stamp:return
        self._legend_stamp=stamp
        if self.legend is not None:self.legend.remove()
        entries={}
        for key,role in self.roles().items():
            if not self.visible_boxes[key].isChecked():continue
            sty=self.style_for(key)
            name=(key+' '+sty['label']) if key in self.layer_styles else sty['label']
            entries[name]=Patch(facecolor=sty['color'] if sty['fill'] else 'none',edgecolor=sty['color'],alpha=sty['alpha'],label=name)
        handles=list(entries.values())
        if getattr(self,'deformation_diagnostics',[]):handles.append(Line2D([],[],marker='x',ls='none',color=self.view_settings.unadapted_marker_color,label='未适配：保留原图'))
        if getattr(self.data,'ignored_geometry',[]):handles.append(Line2D([],[],color='#d72b91',ls='--',lw=.7,label='忽略的无效图形'))
        if self.current_frame is not None:
            handles += [Line2D([],[],color='#3c9995',ls='--',label='最小包围区域'),Line2D([],[],color='#aa78ab',ls='--',label='线距控制区域'),Line2D([],[],color='#92979e',ls='--',label='当前孔中心→接触位置')]
        if self.probability_toggle.isChecked() and self.probability_data:
            maximum=max((float(q['probability'].max()) for q in self.probability_data['maps']),default=0)
            handles += [Line2D([],[],color='#729cb5',ls=':',lw=.7,label='输入最大幅度包络（非等概率线）'),Patch(facecolor=self.probability_colormap()(0.),alpha=self.view_settings.probability_alpha,label='实体覆盖概率：浅色较低'),Patch(facecolor=self.probability_colormap()(maximum),alpha=self.view_settings.probability_alpha,label=f'实体覆盖概率高（最大{maximum:.1%}）')]
        if self.probability_toggle.isChecked() and self.probability_data and self.view_settings.probability_show_ranges:
            levels=dict(self.view_settings.probability_ranges);levels[self.params().target_nsigma]=self.view_settings.probability_target_color
            for level,color in levels.items():
                name='全部输入范围' if level==0 else '∞ 名义位置' if math.isinf(level) else f'{level:g}σ 有界扫掠包络'
                handles.append(Line2D([],[],color=color,ls='--',lw=1.25,label=name))
        self.legend=self.ax.legend(handles=handles,loc='upper right',fontsize=7,framealpha=1,ncol=1)
    def draw_probability(self):
        active=self.probability_toggle.isChecked() and self.probability_data is not None
        stamp=(id(self.probability_data),bool(active),self.view_settings.probability_alpha,self.view_settings.probability_cmap,self.view_settings.probability_interpolation,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.target_sigma.currentText())
        if stamp!=self.probability_stamp:
            for a in self.probability_artists:a.remove()
            self.probability_artists=[];self.probability_stamp=stamp
            if active:
                cmap=self.probability_colormap()
                for q in self.probability_data['maps']:
                    values=np.ma.masked_where(q['probability']==0,q['probability'])
                    a=self.ax.imshow(values,extent=q['extent'],origin='lower',cmap=cmap,norm=Normalize(0,1),interpolation=self.view_settings.probability_interpolation,alpha=self.view_settings.probability_alpha,zorder=4)
                    a._via_layer=q['key'];e=q['extent'];a._world_bounds=(e[0],e[2],e[1],e[3]);self.probability_artists.append(a)
                    boundary=LineCollection([q['input_envelope']],colors='#729cb5',linewidths=.65,linestyles=(0,(2,3)),alpha=.5,zorder=4.1)
                    boundary._via_layer=q['key'];xy=q['input_envelope'];boundary._world_bounds=(*xy.min(axis=0),*xy.max(axis=0));self.ax.add_collection(boundary);self.probability_artists.append(boundary)
                    if self.view_settings.probability_show_ranges:
                        from .probability_overlay import coverage_envelope
                        p=self.params().for_die(q['owner']);levels=dict(self.view_settings.probability_ranges)
                        levels[p.target_nsigma]=self.view_settings.probability_target_color
                        for level,color in levels.items():
                            key=('range',q['nominal_polygon'].wkb,tuple(q['rotation_center']),str(asdict(p)),level)
                            hull=self.session_values.get_or_compute(key,lambda:coverage_envelope(q['nominal_polygon'],q['rotation_center'],p,len(self.case['dies']),level))
                            xy=np.asarray(hull.exterior.coords)
                            line=LineCollection([xy],colors=color,linewidths=1.25,linestyles=(0,(5,2)),alpha=.9,zorder=4.3,path_effects=[patheffects.withStroke(linewidth=2.2,foreground='white',alpha=.7)])
                            line._via_layer=q['key'];line._coverage_level=level;line._world_bounds=(*xy.min(axis=0),*xy.max(axis=0));self.ax.add_collection(line);self.probability_artists.append(line)
        from .viewport import overlaps
        for a in self.probability_artists:
            a.set_visible(active and self.visible_boxes[a._via_layer].isChecked() and overlaps(a._world_bounds,self.view_bounds))
            a._static_preview=(self.probability_stamp,a._via_layer,a.get_visible())
    def probability_colormap(self):
        return LinearSegmentedColormap.from_list('via_probability',['#ecf5f8','#abd1df','#287e98']) if self.view_settings.probability_cmap=='blue' else matplotlib.colormaps[self.view_settings.probability_cmap]
    def probability_changed(self,*_):
        if self.loading:return
        if self.probability_toggle.isChecked() and self.probability_data is None:self.request_preview()
        else:self.plot_layout(preserve=True)
    def clear_space_artists(self):
        if self.space_colorbar is not None:
            self.space_colorbar.remove();self.space_colorbar=None
        for artist in self.space_artists:
            if artist.axes is not None:artist.remove()
        self.space_artists=[]
    def draw_space(self):
        active=self.space_toggle.isChecked() and self.space_data is not None
        cfg=self.view_settings
        stamp=(id(self.space_data),bool(active),cfg.space_cmap,cfg.space_interpolation,cfg.space_alpha,cfg.space_scope)
        if stamp!=self.space_stamp:
            self.clear_space_artists();self.space_stamp=stamp
            if active and self.space_data['maps']:
                maps=self.space_data['maps'];finite=np.concatenate([q['spacing'][np.isfinite(q['spacing'])] for q in maps])
                lo=self.space_data['base_spacing_um'] if cfg.space_scope=='package' else (float(finite.min()) if len(finite) else 0.);hi=float(finite.max()) if len(finite) else lo+1.
                constant=np.isclose(lo,hi);norm=Normalize(lo,hi if not constant else lo+max(1.,abs(lo)*.01))
                cmap=LinearSegmentedColormap.from_list('space_requirement',['#f2f4dd','#c4dfcd','#9abed6','#8b85b2']) if cfg.space_cmap=='pastel' else cfg.space_cmap
                if cfg.space_scope=='package':
                    for owner in (-1,-2):
                        artist=self.ax.imshow([[lo]],extent=self.case['board'].bounds[::2]+self.case['board'].bounds[1::2],origin='lower',cmap=cmap,norm=norm,alpha=cfg.space_alpha,interpolation=cfg.space_interpolation,zorder=3.5+(owner==-2)*.01)
                        artist._owner=owner;artist._source_layers=sorted({k for q in maps for k in q['layers']});self.space_artists.append(artist)
                    self._package_field_key=None
                for q in ([] if cfg.space_scope=='package' else maps):
                    trans=self.ax.transData
                    values=np.where(np.isfinite(q['spacing']),q['spacing'],norm.vmax)
                    extent=[q['x_edges'][0],q['x_edges'][-1],q['y_edges'][0],q['y_edges'][-1]]
                    artist=self.ax.imshow(values,extent=extent,origin='lower',cmap=cmap,norm=norm,interpolation=cfg.space_interpolation,alpha=cfg.space_alpha,zorder=3.5,transform=trans)
                    artist._space_domain=geometry_path(q['domain']);artist._owner=q['owner']
                    artist.set_clip_path(artist._space_domain,trans)
                    artist._source_layers=q['layers'];self.space_artists.append(artist)
                # Figure-level scale stays outside axes, aligned with the X label.
                cax=self.figure.add_axes([.72,.035,.24,.012]);cax.set_facecolor('white')
                self.space_colorbar=self.figure.colorbar(self.space_artists[0],cax=cax,orientation='horizontal')
                if not len(finite):
                    if cfg.space_scope=='package':self.space_colorbar.set_ticks([lo,norm.vmax],labels=[f'{lo:g}','∞'])
                    else:self.space_colorbar.set_ticks([(lo+hi)/2],labels=['∞'])
                elif constant:self.space_colorbar.set_ticks([lo],labels=[f'{lo:.2f}'])
                self.space_colorbar.ax.tick_params(labelsize=7,pad=1)
                self.space_colorbar.ax.text(-.04,.5,'Space Map · 所需线距 / μm',transform=self.space_colorbar.ax.transAxes,ha='right',va='center',fontsize=7,clip_on=False)
        for artist in self.space_artists:
            if artist._owner<0:
                from .space_map import package_map
                visible={k for k,b in self.visible_boxes.items() if b.isChecked()}
                resolution=min(31,cfg.space_resolution) if getattr(self,'_in_animation',False) else cfg.space_resolution
                key=(id(self.space_data),None if self.shown_states is None else self.shown_states.tobytes(),tuple(sorted(visible)),resolution)
                if getattr(self,'_package_field_key',None)!=key:
                    centers=self.rotation_centers()
                    prepared=getattr(self,'_prepared_package',None)
                    cache_key=('package',getattr(self.engine,'signature',id(self.space_data)),key[1],key[2],resolution)
                    self._package_field=prepared[1] if prepared is not None and prepared[0]==key else self.session_values.get_or_compute(cache_key,lambda:package_map(self.space_data,self.case['board'],centers,self.shown_states,visible,resolution));self._package_field_key=key
                q=self._package_field
                if getattr(artist,'_field_key',None)!=key:
                    values=np.full((2,2),self.space_data['base_spacing_um']) if artist._owner==-1 else np.where(np.isinf(q['display_spacing']),artist.norm.vmax,q['display_spacing'])
                    artist.set_data(values);artist.set_extent(q['extent']);artist._field_key=key
                    domain=self.case['board'].difference(q['domain']) if artist._owner==-1 else q['domain']
                    artist.set_clip_path(geometry_path(domain),self.ax.transData);artist._has_domain=not domain.is_empty
                artist.set_visible(active and artist._has_domain and any(k in visible for k in artist._source_layers));continue
            transform=Affine2D()
            if self.shown_states is not None:
                center=self.rotation_centers()[artist._owner];state=self.shown_states[artist._owner]
                transform.rotate_around(*center,state[2]).translate(*state[:2])
            trans=transform+self.ax.transData;artist.set_transform(trans);artist.set_clip_path(artist._space_domain,trans)
            artist.set_visible(active and any(self.visible_boxes[k].isChecked() for k in artist._source_layers))
        if self.space_colorbar is not None:self.space_colorbar.ax.set_visible(any(a.get_visible() for a in self.space_artists))
    def space_changed(self,*_):
        if self.loading:return
        if self.space_toggle.isChecked() and self.space_data is None:self.request_preview()
        else:self.plot_layout(preserve=True)
    def apply_manual(self):
        if not self.data:return
        try:
            self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers());count=len(self.case['dies']);index=self.manual_die.currentIndex()
            if not 0<=index<count:return
            states=self.shown_states.copy() if self.shown_states is not None and len(self.shown_states)==count else np.zeros((count,3))
            states[index]=[self.manual_fields['dx'].value(),self.manual_fields['dy'].value(),math.radians(self.manual_fields['angle'].value())]
            self.manual_states=states;self.active_preview=None
            self.extreme_table.blockSignals(True);self.extreme_table.clearSelection();self.extreme_table.blockSignals(False)
            self.request_preview()
        except ValueError as exc:self.error(exc)
    def locate_deformation(self,row):
        rows=getattr(self,'deformation_diagnostics',[])
        if not 0<=row<len(rows):return
        point=rows[row].get('points',[rows[row]['point']])[0]
        span=max(120,self.params().line_width_um*12)
        self.animate_view(point,span*1.6,span)
    def request_preview(self,direct=False,settle=False):
        if not self.data:return
        self.stop_animation()
        self.preview_ticket+=1;ticket=self.preview_ticket;self._prepared_motion=None
        if self.preview_worker is not None:self.preview_worker.cancelled.set()
        state=self.manual_states if self.manual_states is not None else (self.extremes[self.active_preview]['states'].copy() if self.active_preview is not None and self.extremes else None)
        if state is None and not self.probability_toggle.isChecked() and not self.space_toggle.isChecked() and not self.drc_toggle.isChecked() and not (self.deformation_toggle.isChecked() and self.result):
            self.preview_pending=None;self.present_frame(None);return
        try:p=self.params();roles=self.roles();case=self.case or build_case(self.data,roles,layer_order=self.ordered_layers())
        except ValueError as exc:self.error(exc);return
        show_frame=state is not None
        if state is None:state=np.zeros((len(case['dies']),3))
        self.preview_pending=(self.revision,self.view_settings,ticket,state.copy(),self.motions(),roles,p,self.result,case,self.geometry_cache,self.connections,self.ordered_layers(),show_frame,self.probability_toggle.isChecked(),self.space_toggle.isChecked())
        self.preview_pending+=(self.deformation_toggle.isChecked(),self.drc_toggle.isChecked(),direct,settle)
        self.statusBar().showMessage('正在后台生成预演…保留已显示状态。');self.launch_preview()
    def launch_preview(self):
        if self.preview_worker is not None or self.motion_worker is not None or self.preview_pending is None:return
        revision,settings,ticket,states,motions,roles,params,result,case,cache,connections,order,show_frame,heat,space,deform,drc,direct,settle=self.preview_pending;self.preview_pending=None;engine=self.engine;probability=self.probability_data;space_data=self.space_data;data=self.data
        visible=tuple(sorted(k for k,b in self.visible_boxes.items() if b.isChecked()))
        session_values=self.session_values;engines=self.session_engines
        start_states=self.shown_states.copy() if self.shown_states is not None else np.zeros_like(states)
        view_span=max(np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim()))
        def work(progress):
            from .probability_overlay import probability_maps
            from .space_map import space_maps
            actual_cache=cache or GeometryCache(data);con=connections or Connections(data,roles,order,actual_cache.geometries)
            from .session_cache import fingerprint
            signature=fingerprint((data.sha256,data.top_name,roles,order,motions,asdict(params),result is not None,(result or {}).get('parameters'),(result or {}).get('contact_analysis'),(result or {}).get('issues')))
            actual=engines.get(signature)
            if actual is None:
                actual=PreviewEngine(actual_cache,case,roles,motions,params,result,con);engines[signature]=actual
            actual.signature=signature
            actual.configure_deformation(deform,settings);actual.configure_drc(drc,settings)
            distribution=None;field=None
            if heat:distribution=session_values.get_or_compute(('probability',signature,settings.numeric_key(),bool(settings.probability_show_ranges and math.isinf(params.target_nsigma))),lambda:probability_maps(actual_cache,actual.bound,roles,params,masks=actual.contact.interface_masks,settings=settings,progress=progress))
            if space:field=session_values.get_or_compute(('space',signature,settings.space_resolution),lambda:space_maps(actual.contact,params,resolution=settings.space_resolution))
            prepared=None
            if space and field is not None and settings.space_scope=='package':
                from .space_map import package_map
                key=(id(field),states.tobytes() if show_frame else None,visible,settings.space_resolution)
                prepared=(key,session_values.get_or_compute(('package',signature,states.tobytes(),visible,settings.space_resolution),lambda:package_map(field,case['board'],actual.bound.centers,states,visible,settings.space_resolution)))
            motion=None
            if show_frame and deform and result is not None and settings.animation_enabled and not direct and not np.array_equal(start_states,states):
                import time
                from .animation import motion_timing
                travel=float(np.max(np.linalg.norm(states[:,:2]-start_states[:,:2],axis=1)+actual.bound.owner_radii*np.abs(states[:,2]-start_states[:,2])))
                duration,steps=motion_timing(settings,travel,view_span)
                motion_key=('animation',signature,actual.cache_key(),start_states.tobytes(),states.tobytes(),duration,steps)
                replay=session_values.get(motion_key)
                started=time.perf_counter();frames=0
                if replay is not None:
                    for elapsed,intermediate in replay:
                        worker.cancelled.wait(max(0,elapsed-(time.perf_counter()-started)))
                        progress(round(90*elapsed/max(.001,duration/1000)),'重用已校验动画缓存')
                        worker.publish((actual,intermediate,distribution,con,actual_cache,field));frames+=1
                else:
                    replay=[];fraction=min(.2,1/max(2,steps))
                    while fraction<1:
                        t=fraction*fraction*(3-2*fraction)
                        intermediate=actual.frame(start_states*(1-t)+states*t,progress,quick=True)
                        elapsed=min(duration/1000,time.perf_counter()-started)
                        replay.append((elapsed,intermediate));progress(round(90*fraction),'型变插值：已校验当前帧')
                        worker.publish((actual,intermediate,distribution,con,actual_cache,field));frames+=1
                        fraction=max(fraction+1/max(2,steps),(time.perf_counter()-started)*1000/duration)
                    session_values[motion_key]=replay
                motion=dict(streamed=True,count=frames,cached=motion_key in session_values)
            frame=actual.frame(states,progress,quick=direct and not settle) if show_frame or drc or (deform and result is not None) else None
            if frame is not None and not show_frame:
                frame=dict(frame,nominal_global=True)
                if deform and result is not None and math.isfinite(params.target_nsigma):
                    frame=actual.global_frame(frame,settings,progress)
                elif math.isfinite(params.target_nsigma):
                    frame['contact']=dict(frame['contact'],issues=(result or {}).get('issues',[]),intrusions=[])
            if direct:motion=dict(direct=True,settled=settle)
            return actual,frame,distribution,con,actual_cache,field,prepared,motion
        worker=Worker(work,self);worker.interactive_refinement=bool(direct and settle);self.preview_worker=worker;self.watch_worker(worker,'预演计算')
        def partial(_):
            value=worker.take_partial()
            if value is None:return
            if revision!=self.revision or ticket!=self.preview_ticket or data is not self.data:return
            actual,frame,distribution,con,cache,field=value
            self.engine=actual;self.case=case;self.probability_data=distribution;self.connections=con;self.geometry_cache=cache;self.space_data=field
            self.show_motion_frame(frame)
        worker.partial.connect(partial)
        def accepted(value):
            if revision!=self.revision or data is not self.data or ticket!=self.preview_ticket:return
            self.engine,frame,self.probability_data,self.connections,self.geometry_cache,self.space_data,self._prepared_package,self._prepared_motion=value
            if ticket!=self.preview_ticket:return
            self.case=case;self.refresh_motion_table()
            if frame and frame.get('global_deformation') and self.result:
                self.result['adaptive_preview']=frame['global_deformation'];self.report_dirty=True
            if self._prepared_motion and (self._prepared_motion.get('streamed') or self._prepared_motion.get('direct')):
                self.current_frame=frame;self.plot_layout(preserve=True);self.update_histograms();self.show_frame_status(frame);self.log_bus.emit('交互帧已更新' if self._prepared_motion.get('direct') else '型变流式帧完成：'+str(self._prepared_motion['count']))
            else:self.present_frame(frame)
            if self.current_frame is not None:
                self.state_table.setRowCount(len(states))
                for i,z in enumerate(states):
                    for j,x in enumerate([i+1,fmt(z[0],6),fmt(z[1],6),fmt(math.degrees(z[2]),6),'手动输入' if self.manual_states is not None else '代表极端']):self.state_table.setItem(i,j,W.QTableWidgetItem(str(x)))
        def rejected(message):
            if ticket!=self.preview_ticket:return
            self.statusBar().showMessage('预演未应用：'+message.split('Traceback')[0]);self.error(message)
        worker.progress.connect(lambda n,t:self.statusBar().showMessage(t) if ticket==self.preview_ticket else None)
        worker.done.connect(accepted);worker.failed.connect(rejected)
        def finished():
            self.preview_worker=None;worker.deleteLater();self.launch_preview()
        worker.finished.connect(finished);worker.start()
    def update_histograms(self,clear=False):
        if not hasattr(self,'distribution_figure'):return
        self.histogram_data=None
        if clear or self.current_frame is None:
            self.chart_status.setText('选择极端情况后显示当前可见橙色连线的位移统计。');self.distribution_figure.clear();self.distribution_canvas.draw_idle();return
        seg=self.connector_segments;delta=seg[:,1]-seg[:,0];magnitude=np.linalg.norm(delta,axis=1);nonzero=magnitude>1e-9
        directions=(np.degrees(np.arctan2(delta[nonzero,1],delta[nonzero,0]))+360)%360
        bins=min(24,max(5,int(np.sqrt(len(magnitude)))))
        mag_counts,mag_edges=np.histogram(magnitude,bins=bins,range=(0,max(1e-6,float(magnitude.max())*1.001))) if len(magnitude) else (np.zeros(5,int),np.linspace(0,1,6))
        dir_counts,dir_edges=np.histogram(directions,bins=np.linspace(0,360,13))
        self.histogram_data=dict(magnitude=magnitude,direction=directions,magnitude_counts=mag_counts,direction_counts=dir_counts,magnitude_edges=mag_edges,direction_edges=dir_edges,zero_count=int((~nonzero).sum()))
        self.chart_status.setText(f'可见运动过孔 {len(seg):,} 个；零位移 {(~nonzero).sum():,} 个（无方向）。方向：+X为0°，逆时针增加。')
        self.histogram_dirty=True
        if self.tabs.currentWidget() is self.analysis_page:self.render_histograms()
    def render_histograms(self):
        if not self.histogram_data or not self.histogram_dirty:return
        self.histogram_dirty=False;q=self.histogram_data
        mag_edges=q['magnitude_edges'];mag_counts=q['magnitude_counts'];dir_edges=q['direction_edges'];dir_counts=q['direction_counts']
        self.distribution_figure.clear();axes=self.distribution_figure.subplots(1,2)
        axes[0].bar(mag_edges[:-1],mag_counts,width=np.diff(mag_edges)*.92,align='edge',color='#abc7df',edgecolor='#718fa7',linewidth=.5)
        axes[0].set(xlabel='偏移幅值 / μm',ylabel='运动过孔数量',title='偏移数值分布')
        axes[1].bar(dir_edges[:-1],dir_counts,width=27,align='edge',color='#bdd6c8',edgecolor='#719783',linewidth=.5)
        axes[1].set(xlabel='位移方向 / °',ylabel='运动过孔数量',title='偏移方向分布',xlim=(0,360),xticks=np.arange(0,361,45))
        from matplotlib.ticker import MaxNLocator
        for ax in axes:
            ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True);ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        self.distribution_canvas.draw_idle()
    def run_evaluation(self,automatic=False):
        if not self.data:self.error('请先打开GDS');return
        try:p=self.params();roles=self.roles();order=self.ordered_layers()
        except Exception as e:self.error(e);return
        if self.worker is not None:return
        self._evaluation_requested=True
        if not automatic:self.reset_session_cache();self.drop_result()
        rev=self.revision;data=self.data;cache=self.geometry_cache;motions=self.motions();settings=self.view_settings;values=self.session_values
        def work(progress):
            progress(1,'构建图层连接与几何索引');actual_cache=cache or GeometryCache(data);connections=Connections(data,roles,order,actual_cache.geometries);case=build_case(data,roles,connections=connections)
            from .session_cache import fingerprint
            key=('assessment',fingerprint((data.sha256,data.top_name,roles,order,asdict(p),settings.numeric_key())))
            r=values.get_or_compute(key,lambda:evaluate(data,roles,p,progress=progress,layer_order=order,connections=connections,motions=motions,settings=settings));r=dict(r,motions=motions,view_settings=asdict(settings))
            catalog=RepresentativeExtremes(case,p);r['preview_count']=catalog.total
            return r,case,actual_cache,connections,catalog
        def accepted(value):
            if data is not self.data:return
            if rev!=self.revision:
                self.log_bus.emit('评估快照已过期：排队计算最新参数，旧任务不会覆盖结果');self._coverage_pending=True;self.coverage_timer.start();return
            self.log_bus.emit('评估结果已应用：版本代号 '+str(rev))
            r,self.case,self.geometry_cache,self.connections,catalog=value;self.display_result(r,catalog,preserve=automatic)
        self.start_worker(work,accepted,interactive=True)
    def display_result(self,r,catalog=None,preserve=False):
        saved=(self.active_preview,self.manual_states,self.extreme_table.currentRow(),self.issue_scheme.currentIndex())
        self.result=r;self.dirty=False;self.evaluation_motions=r.get('motions',self.motions());self.manual_states=None
        if self.case is None:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        self.extremes=catalog or RepresentativeExtremes(self.case,self.params());self.active_preview=None;self.owner_cache=None;self.engine=None;self.current_frame=None;self.report_dirty=True
        self.show_extreme_page(0,redraw=False)
        first=next((s['scheme'] for s in r['schemes'] if any(q['scheme']==s['scheme'] for q in r.get('issues',[]))),r['recommendation'].get('scheme') or 1)
        self.issue_scheme.blockSignals(True);self.issue_scheme.setCurrentIndex(first-1);self.issue_scheme.blockSignals(False);self.fill_issues()
        rec=r['recommendation'];self.verdict.setText('优先建议：'+rec['name']+'。\n'+rec['reason'])
        self.scheme_table.setRowCount(3)
        for i,s in enumerate(r['schemes']):
            for j,v in enumerate([f"{s['scheme']} {s['name']}",s['status'],fmt(s['gamma']),'; '.join(s['reasons'])]):self.scheme_table.setItem(i,j,W.QTableWidgetItem(str(v)))
        self.scheme_table.setColumnWidth(0,230);self.scheme_table.setColumnWidth(1,120);self.scheme_table.setColumnWidth(2,65);self.scheme_table.horizontalHeader().setStretchLastSection(True);self.scheme_table.resizeRowsToContents()
        self.scheme_figure.clear();ax=self.scheme_figure.add_subplot(111);values=[s['gamma'] for s in r['schemes']];finite=[x for x in values if math.isfinite(x)]
        cap=max(1.4,min(8,max(finite or [1])*1.2));display=[min(x,cap) for x in values];ax.barh([0,1,2],display,color=['#a8c5de','#accfbd','#d6bedf'],height=.48)
        ax.axvline(1,color='#708395',ls='--',lw=1);ax.set_yticks([0,1,2],['方案1','方案2','方案3']);ax.invert_yaxis();ax.set_xlim(0,cap*1.15)
        for i,s in enumerate(r['schemes']):ax.text(display[i]+cap*.02,i,fmt(s['gamma']),va='center',color='#42536b')
        ax.set(xlabel='保守需求 / 能力 Γ（≤1仅为初筛通过）',title='三方案比较');ax.spines[['top','right']].set_visible(False);self.scheme_canvas.draw_idle()
        self.die_table.setRowCount(len(r['dies']))
        for i,d in enumerate(r['dies']):
            vals=[d['die'],d['width'],d['height'],d['pin_count'],d['output_count'],d['corner_bound_um'],d['traffic']['ratio'],d['fixed_via_ratio'],d['output_capture_ratio']]
            for j,value in enumerate(vals):self.die_table.setItem(i,j,W.QTableWidgetItem(fmt(value)))
        self.die_table.resizeColumnsToContents();self.detail.clear()
        if preserve:
            self.active_preview,self.manual_states,row,scheme=saved
            self.extreme_table.blockSignals(True);self.extreme_table.setCurrentCell(max(0,row),0);self.extreme_table.blockSignals(False)
            self.issue_scheme.blockSignals(True);self.issue_scheme.setCurrentIndex(scheme);self.issue_scheme.blockSignals(False)
        else:self.tabs.setCurrentIndex(0);self.preview_tabs.setCurrentIndex(0)
        self.plot_layout(preserve=preserve)
        self.refresh_motion_table();self.request_preview()
        if self.tabs.currentWidget() is self.detail:self.update_report()
        self.statusBar().showMessage('评估完成：选择下方极端行预演，或打开“报告”查看逐步计算')
    def tab_changed(self,*_):
        if self.tabs.currentWidget() is self.analysis_page:self.render_histograms()
        if self.tabs.currentWidget() is self.detail and self.result and self.report_dirty:self.update_report()
    def update_report(self):
        if getattr(self,'_report_rendering',False):return
        self._report_rendering=True;result=self.result;self.progress.show();self.progress.setValue(0)
        try:
            def progress(n,text):
                self.progress.setValue(n);self.progress.setFormat(text);self.log_bus.emit(text)
                flag=getattr(QtCore.QEventLoop,'ProcessEventsFlag',QtCore.QEventLoop).ExcludeUserInputEvents
                W.QApplication.processEvents(flag)
            html,resources=report_document(result,self.evaluation_motions,progress)
            if self.result is result:set_document(self.detail,html,resources);self.report_dirty=False
        except Exception:self.error(traceback.format_exc())
        finally:
            self._report_rendering=False
            if not self._tasks:self.progress.hide();self.cleanup_retired_sessions()
    def show_extreme_page(self,page=0,redraw=True):
        if self.extremes is None:return
        self.preview_page=0;self.page_items=self.extremes.page(0);self.active_preview=None
        self.page_summary.setText(f'{self.extremes.total} 个输入幅度边界组合 · 空白首行恢复名义位置')
        self.extreme_table.blockSignals(True);self.extreme_table.clearContents();self.extreme_table.setRowCount(len(self.page_items)+1)
        for i,(index,e) in enumerate(self.page_items,1):
            states=e['states'];values=[e['name']]
            for j in range(3):
                x=np.rad2deg(states[:,j]) if j==2 else states[:,j]
                values.append(fmt(x[0],5) if np.allclose(x,x[0]) else f'{fmt(min(x),5)} ～ {fmt(max(x),5)}')
            values.append(e['detail'])
            detail='\n'.join(f'DIE {j+1}: DX={state[0]:.6g} μm, DY={state[1]:.6g} μm, θ={math.degrees(state[2]):.6g}°' for j,state in enumerate(states))
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(value);item.setToolTip(detail);self.extreme_table.setItem(i,j,item)
        self.extreme_table.setCurrentCell(0,0);self.extreme_table.blockSignals(False)
        for j,width in enumerate([125,135,135,125]):self.extreme_table.setColumnWidth(j,width)
        self.extreme_table.horizontalHeader().setStretchLastSection(True);self.state_table.setRowCount(0)
        if redraw:self.plot_layout(preserve=True)
    def select_preview(self,row,column,*_):
        if self.loading:return
        if hasattr(self,'mouse_die'):
            self.mouse_die.cancel_pending();self.mouse_die.exit()
        self.manual_states=None
        self.active_preview=self.page_items[row-1][0] if 1<=row<=len(self.page_items) else None
        self.state_table.setRowCount(0)
        if self.active_preview is not None:
            e=self.extremes[self.active_preview];self.state_table.setRowCount(len(e['states']))
            for i,state in enumerate(e['states']):
                for j,value in enumerate([i+1,fmt(state[0],6),fmt(state[1],6),fmt(math.degrees(state[2]),6),e['per_die_labels'][i]]):
                    self.state_table.setItem(i,j,W.QTableWidgetItem(str(value)))
            self.state_table.resizeColumnsToContents()
        self.request_preview()
    def fill_issues(self):
        entries=self.current_frame['contact']['issues'] if self.current_frame else (self.result or {}).get('issues',[])
        stamp=(id(entries),self.issue_scheme.currentData(),id(self.result))
        if stamp==self._issue_table_stamp:return
        self._issue_table_stamp=stamp
        self.filtered_issues=[q for q in entries if q['scheme']==self.issue_scheme.currentData()]
        self.issue_table.blockSignals(True)
        self.issue_table.setRowCount(len(self.filtered_issues))
        for i,q in enumerate(self.filtered_issues):
            point=q['point'];probability='型变后概率待细化' if q.get('adaptive_unresolved') else f"{q['probability_lower']:.3g} ～ {q['probability_upper']:.3g}" if 'probability_lower' in q else '100%（当前接触）'
            allowed=f"{q['threshold']:.3g}" if 'threshold' in q else '二值'
            values=[q['kind'],q['die'],probability,allowed,fmt(q['need']),fmt(q['allow']),f'{fmt(point[0])}, {fmt(point[1])}']
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(str(value));item.setToolTip(q.get('detail',''));self.issue_table.setItem(i,j,item)
        self.issue_table.resizeColumnsToContents();self.issue_table.blockSignals(False)
        status=self.result['schemes'][self.issue_scheme.currentData()-1]['status'] if self.result else '尚未评估'
        scope='全局型变：代表状态已迭代；连续概率待细化' if self.current_frame and self.current_frame.get('nominal_global') and self.current_frame.get('global_deformation') else '当前预演' if self.current_frame and not self.current_frame.get('nominal_global') else '评估包络'
        if self.current_frame and self.current_frame.get('deformation'):
            d=self.current_frame['deformation'];summary=f'{scope}：{len(self.filtered_issues)}项 · {len(d.get("iterations",[]))}轮 · 释放{d.get("released_count",0)}个'
        else:summary=f'{scope}：{len(self.filtered_issues)}处'+('' if self.current_frame else f' · {status}')
        self.issue_summary.setText(summary)
    def issue_filter_changed(self,*_):
        if self.loading:return
        self.plot_layout(preserve=True)
    def issue_row_changed(self,row,column,previous_row,previous_column):
        if row!=previous_row and not self.loading:self.issue_navigation.start()
    def locate_issue(self,row,column,*_):
        if not 0<=row<len(self.filtered_issues):return
        points=np.array([self.anchor_position(a) for a in self.filtered_issues[row]['anchors']]);c=points.mean(axis=0);span=max(120,float(np.ptp(points,axis=0).max())*1.4)
        self.animate_view(c,2*span,2*span)
    def anchor_position(self,anchor):
        point=np.array(anchor['point']);owner=anchor['owner'];mode=self.motions().get(anchor['layer']) if anchor['layer'] else None
        if mode=='fixed':owner=-1
        elif mode and mode.startswith('die:'):owner=int(mode.split(':')[1])
        if owner>=0 and self.shown_states is not None and self.preview_applied:
            point=rigid_transform(point,self.rotation_centers()[owner],self.shown_states[owner])
        return point
    def locate_die(self,row,column):
        if not self.case:return
        d=self.case['dies'][row];self.tabs.setCurrentIndex(0);x0,y0,x1,y1=d.bounds;pad=max(x1-x0,y1-y0)*.35
        self.animate_view(((x0+x1)/2,(y0+y1)/2),x1-x0+2*pad,y1-y0+2*pad)
    def refresh_jobs(self,*_):
        try:items=list_jobs(self.jobs_dir)
        except OSError as exc:self.statusBar().showMessage('Jobs目录不可读：'+str(exc));return
        self.job_combo.blockSignals(True);self.job_combo.clear();self.job_combo.addItem('',None)
        for name,path in items:self.job_combo.addItem(name,str(path))
        index=self.job_combo.findData(str(self.project_path)) if self.project_path else 0
        self.job_combo.setCurrentIndex(max(0,index));self.job_combo.blockSignals(False)
    def job_selected(self,index):
        path=self.job_combo.itemData(index)
        if path:self.load_project(path)
        else:self.project_path=None;self.statusBar().showMessage('当前为未命名工程，保存时输入名称')
    def save_project(self):
        if not self.data:self.error('请先输入GDS');return
        path=self.project_path
        if path is None:
            name,ok=W.QInputDialog.getText(self,'保存工程','工程名称：')
            if not ok:return
            try:
                path=job_path(self.jobs_dir,name)
                if path.exists():raise ValueError('同名工程已存在，请从下拉菜单打开后保存，或使用不同名称')
            except ValueError as exc:self.error(exc);return
        try:self.write_project(path);self.refresh_jobs();self.statusBar().showMessage('工程已保存：'+Path(path).stem)
        except Exception as exc:self.error(exc)
    def write_project(self,path):
        import os
        path=Path(path).resolve();gds=Path(self.data.path).resolve()
        source=os.path.relpath(gds,path.parent) if gds.is_relative_to(installation_root()) else str(gds)
        project=dict(schema_version=5,app_version=__version__,gds_path=source,sha256=self.data.sha256,top_cell=self.data.top_name,
                     roles=self.roles(),parameters=asdict(self.params()),settings=asdict(self.view_settings),layer_styles=self.layer_styles,visibility={k:w.isChecked() for k,w in self.visible_boxes.items()},motions=self.motions(),layer_order=self.ordered_layers())
        from .core import clean_json
        atomic_save(path,clean_json(project));self.project_path=path
    def load_project(self,path):
        try:
            path=Path(path).resolve();project=normalize_project(json.loads(path.read_text(encoding='utf-8')));gds=Path(project['gds_path'])
            if not gds.is_absolute():gds=path.parent/gds
            self.load_gds(gds,project.get('top_cell'),project,project_path=path)
        except Exception as exc:self.refresh_jobs();self.error(exc)
    def closeEvent(self,event):
        self.stop_animation()
        if hasattr(self,'mouse_die'):self.mouse_die.pending=False;self.mouse_die.full_pending=False;self.mouse_die.timer.stop();self.mouse_die.idle.stop()
        running=[w for w in (self.worker,self.preview_worker,self.motion_worker,self.hardware_worker) if w is not None and w.isRunning()]
        if running:
            self._closing=True
            self.preview_pending=None;self.preview_ticket+=1;self.motion_pending=None;self.motion_generation+=1;self.coverage_timer.stop();self._coverage_pending=False
            for w in running:w.cancelled.set()
            event.ignore();return
        self.session_values.close()
        for engine in self.session_engines.values():engine.close()
        self.cleanup_retired_sessions();self.runtime_capture.close();event.accept()


def launch(project=None):
    app=W.QApplication.instance() or W.QApplication([]);app.setApplicationName('Die Shift Analyze')
    window=MainWindow();window.show()
    if project:QtCore.QTimer.singleShot(0,lambda:window.load_project(project))
    return app.exec() if hasattr(app,'exec') else app.exec_()
