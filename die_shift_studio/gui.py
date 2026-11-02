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
from .safe_range_plot import draw_safe_ranges
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


from .gds_shapes import edited_snapshot
from .project import normalize_project, project_overlays, OVERLAY_DEFAULTS
from .width_display import WidthDisplayEditor,tint
from .width_overlay import ChannelTable, INTRO as WIDTH_INTRO
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
        self.setAttribute(getattr(Qt,'WidgetAttribute',Qt).WA_DeleteOnClose)
        self.jobs_dir=Path(jobs_dir) if jobs_dir is not None else installation_root()/'Jobs'
        self.jobs_dir.mkdir(parents=True,exist_ok=True)
        self.wheel_filter=NoWheelValues(self);W.QApplication.instance().installEventFilter(self.wheel_filter)
        self.resize(1540,1000);self.setMinimumSize(1120,740);self.setStyleSheet(STYLE)
        self.data=None;self.result=None;self.case=None;self.worker=None;self.project_path=None
        from .runtime_log import LogBus,RuntimeCapture
        self.log_bus=LogBus(self,self.jobs_dir.parent/'logs'/'runtime.log');self.runtime_capture=RuntimeCapture(self.log_bus);self._tasks={};self._closing=False
        self.per_die_values=[];self.per_die_pivots=[];self.reference_nsigma=6.;self._per_die_initialized=False;self.hardware_worker=None
        from .session_cache import ProjectCacheRegistry
        self.cache_registry=ProjectCacheRegistry();self.cache_project_key="empty";self.cache_group=self.cache_registry.get("empty")
        self.session_values=self.cache_group.values;self.session_engines=self.cache_group.engines;self.retired_sessions=[];self.parked_groups=[];self.cache_clear_worker=None
        self.fields={};self.role_boxes={};self.stage_hints={};self.visible_boxes={};self.motion_boxes={}
        self.loading=False;self.revision=0;self.extremes=None;self.active_preview=None;self.owner_cache=None;self.auto_values={}
        self.preview_page=0;self.page_items=[];self.state_result=None;self.issue_artists=[];self.shift_artist=None
        self.rendered_layers={};self.layer_artists={};self._layer_keys=[];self.geometry_cache=None;self.connections=None
        self.preview_worker=None;self.preview_workers=[];self.preview_pending=None;self.preview_ticket=0;self.engine=None;self.current_frame=None;self.shown_states=None
        self.die_labels=[];self._default_view=None;self._artist_frame=None;self.report_dirty=True;self._reordering=False
        self.space_data=None;self.repair_data=None;self.space_artists=[];self.space_stamp=None;self.space_colorbar=None;self._resizing=False
        self._issue_table_stamp=None;self.dirty=False;self.manual_states=None;self.evaluation_motions={};self.probability_data=None;self.probability_artists=[];self.probability_stamp=None;self.legend=None;self.zone_artists=[];self.invalid_artists=[]
        self.view_settings=ViewSettings();self.setting_fields={};self.layer_styles={};self.histogram_dirty=False
        self.coverage_timer=QtCore.QTimer(self);self.coverage_timer.setSingleShot(True);self.coverage_timer.setInterval(180);self.coverage_timer.timeout.connect(self.refresh_coverage)
        self._coverage_pending=False;self._evaluation_requested=False;self._motion_intent=None;self.motion_worker=None;self.motion_pending=None;self.motion_generation=0;self.deformation_artists={};self._plotting=False;self.viewport_index=None;self._viewport_stamp=None
        self.issue_navigation=QtCore.QTimer(self);self.issue_navigation.setSingleShot(True);self.issue_navigation.setInterval(0);self.issue_navigation.timeout.connect(lambda:self.locate_issue(self.issue_table.currentRow(),0))
        self.viewport_timer=QtCore.QTimer(self);self.viewport_timer.setSingleShot(True);self.viewport_timer.setInterval(16);self.viewport_timer.timeout.connect(self.viewport_changed)
        self.transition=Transition(self);self._animation_cleanup=None;self._legend_stamp=None
        self._build()
        shortcut=QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut
        self.properties_shortcut=shortcut(QtGui.QKeySequence('Q'),self.canvas)
        self.properties_shortcut.setContext(getattr(Qt,'ShortcutContext',Qt).WidgetWithChildrenShortcut)
        self.properties_shortcut.activated.connect(self.show_properties)
        self.selection=GeometrySelection(self);self.escape_shortcut=(QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut)(QtGui.QKeySequence('Escape'),self);self.escape_shortcut.activated.connect(self.escape_action);self.scene_renderer=SceneRenderer(self.canvas,self.ax);self.refresh_jobs()
        for name in ('button_press_event','scroll_event'):self.canvas.mpl_connect(name,lambda e:self.stop_animation())
        from .mouse_die import MouseDieMode
        self.mouse_die=MouseDieMode(self)
        from .width_overlay import WidthOverlay
        self.width_overlay=WidthOverlay(self)
        self.repair_data=None
        from .repair_overlay import RepairOverlay
        self.repair_overlay=RepairOverlay(self)
        from .reconstruction_overlay import ReconstructionOverlay
        self.reconstruction_overlay=ReconstructionOverlay(self)
        self.job_watcher=QtCore.QFileSystemWatcher([str(self.jobs_dir)],self);self.job_watcher.directoryChanged.connect(self.refresh_jobs)
        self.cache_status_timer=QtCore.QTimer(self);self.cache_status_timer.setInterval(500);self.cache_status_timer.timeout.connect(self.refresh_cache_status);self.cache_status_timer.start()
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
        self.shortcut_label=label('快捷键（鼠标位于图中）：F 恢复视图，M 手动偏移，Q 查看或编辑选中图形节点，ESC 退出/取消选择。\n普通模式：滚轮缩放，中键平移，右键单击居中、拖框缩放。\n手动偏移：右键拖动芯片，滚轮旋转，Ctrl＋滚轮缩放。');page.addWidget(self.shortcut_label)
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
        target=W.QGroupBox('良率要求');form=W.QFormLayout(target)
        self.target_sigma=W.QComboBox();self.target_sigma.setEditable(False);self.target_sigma.addItems([str(i) for i in range(0,13)]+['∞']);self.target_sigma.setCurrentText('6')
        self.target_sigma.setToolTip('有限非零k：最大幅度 B 决定截断前 σ=B/k；0保留最近尺度并检查全部有界概率。∞或具体预演按当前接触判断。')
        self.target_sigma.currentTextChanged.connect(self.coverage_changed);form.addRow('良率级别',self.target_sigma);page.addWidget(target)
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
        self.deformation_toggle.toggled.connect(self.deformation_changed)
        self.width_toggle=W.QCheckBox('宽度分析');self.width_toggle.setToolTip('按实际线路穿越数量检查局部通道容量：矩形颜色显示宽度余量，轮廓高亮风险线路；不代表全局可布通证明。');corner_layout.addWidget(self.width_toggle);self.width_toggle.toggled.connect(self.width_changed)
        self.repair_toggle=W.QCheckBox('修复补块');self.repair_toggle.setToolTip('设备方修复模型：过孔偏移后脱离固定RDL时，在固定RDL层按偏移量拉直线补块，并对补块后的图形做DRC；补块是简化模型，不代表设备实际修复合格。');corner_layout.addWidget(self.repair_toggle);self.repair_toggle.toggled.connect(self.repair_changed)
        self.reconstruction_toggle=W.QCheckBox('重构预演');self.reconstruction_toggle.setToolTip('重构预演：软件自动把运动过孔下方的固定RDL铜划为移动区——同一网络、彼此相距不远的多个落盘合成一条串，整条串只切一刀，切口线垂直于这条串的走线（与走线两侧各成 90°），位置从串里最远的落盘再往外量 2.5 个落盘直径（0＝自动，可用“切口距离 D”改）；同一块固定RDL跨多个芯片时，移动区只取本 DIE 芯片边框（128/0）以内的铜，边框外的铜留在原位归相邻 DIE。移动区按该 DIE 的刚体变换移动，并用两面切口的 4 个端点围成的多边形补上裂隙（不论是否短路都补）。自动划分可能与人工划分不同，仅作预演。');corner_layout.addWidget(self.reconstruction_toggle);self.reconstruction_toggle.toggled.connect(self.reconstruction_changed)
        self.preview_tabs.setCornerWidget(corner)
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
        self.die_table=table(['芯片','长 / μm','宽 / μm','接口过孔','边界接口','角点包络 / μm','方案1 U','方案2 U','方案3 U'])
        self.die_table.cellDoubleClicked.connect(self.locate_die)
        info=W.QSplitter(HORIZONTAL)
        for caption,widget in [('图层随动设置',self.motion_table),('当前芯片状态',self.state_table),('逐芯片结果',self.die_table)]:
            pane=W.QWidget();pl=W.QVBoxLayout(pane);pl.setContentsMargins(2,2,2,2);pl.addWidget(W.QLabel(caption));pl.addWidget(widget);info.addWidget(pane)
        info.setSizes([360,330,340]);self.info_split=info;self.preview_tabs.addTab(info,'信息')
        self.deformation_table=table(['预演层','源图形','未解原因（保留原图）']);self.preview_tabs.addTab(self.deformation_table,'型变校验');self.deformation_table.currentCellChanged.connect(lambda row,*_:self.locate_deformation(row))
        repair_page=W.QWidget();rv=W.QVBoxLayout(repair_page);rv.setContentsMargins(3,3,3,3)
        self.repair_summary=label('勾选“修复补块”后，按当前姿态显示设备方在固定RDL层的直线补块，以及补块后的DRC。')
        rv.addWidget(self.repair_summary)
        self.repair_table=table(['固定RDL层','图形','运动过孔','所属','过孔偏移 / μm','补块直径 / μm','补块面积 / μm²','补块新增违规'])
        self.repair_table.currentCellChanged.connect(lambda row,*_:self.repair_overlay.locate_patch(row))
        rv.addWidget(self.repair_table)
        rv.addWidget(W.QLabel('补块新增违规明细（点击可跳到该区域；视图定位随当前姿态）'))
        self.repair_violation_table=table(['种类','面积 / μm²','关联补块','区域中心 / μm'])
        self.repair_violation_table.setMaximumHeight(150)
        self.repair_violation_table.currentCellChanged.connect(lambda row,*_:self.repair_overlay.locate_violation(row))
        rv.addWidget(self.repair_violation_table)
        self.preview_tabs.addTab(repair_page,'修复补块')
        width_page=W.QWidget();wv=W.QVBoxLayout(width_page);wv.setContentsMargins(3,3,3,3)
        self.width_summary=label(WIDTH_INTRO);wv.addWidget(self.width_summary)
        self.width_sufficient_toggle=W.QCheckBox('显示宽度足够');wv.addWidget(self.width_sufficient_toggle)
        self.width_sufficient_toggle.toggled.connect(self.width_filter_changed)
        self.width_table=ChannelTable();wv.addWidget(self.width_table)
        self.width_table.selectionModel().currentRowChanged.connect(lambda index,_:self.width_overlay.locate(index.row()));self.preview_tabs.addTab(width_page,'宽度分析')
        self.reconstruction_page=W.QWidget();cv=W.QVBoxLayout(self.reconstruction_page);cv.setContentsMargins(3,3,3,3)
        self.reconstruction_summary=label('勾选“重构预演”后，软件按运动过孔自动划分固定RDL的移动区（同一网络的落盘合为一条串、只切一刀，切口垂直于这条串的走线；同一块固定RDL跨多个芯片时只取本 DIE 芯片边框以内的铜），按当前姿态移动，并用两个切口面的 4 个端点围成的多边形补上裂隙。');cv.addWidget(self.reconstruction_summary)
        self.reconstruction_table=table(['固定RDL层','所属','网络','移动区面积 / μm²','位移 / μm','裂隙 / μm','补线面积 / μm²','切口缝长 / μm','状态']);cv.addWidget(self.reconstruction_table)
        self.reconstruction_table.currentCellChanged.connect(lambda row,*_:self.reconstruction_overlay.locate(row));self.preview_tabs.addTab(self.reconstruction_page,'重构预演')
        right_split.addWidget(bottom);right_split.setSizes([560,240]);self.tabs.addTab(page,'结果图形')
        self.canvas._managed_viewport=True;self.navigation=Navigation(self.canvas,self.ax)
        self.canvas.mpl_connect('resize_event',self.resize_plot)
        self.ax.callbacks.connect('xlim_changed',self.update_die_label_visibility);self.ax.callbacks.connect('ylim_changed',self.update_die_label_visibility)
        self.ax.callbacks.connect('xlim_changed',self.queue_viewport);self.ax.callbacks.connect('ylim_changed',self.queue_viewport)
        self.scheme_figure=Figure(figsize=(9,5));self.scheme_figure.subplots_adjust(left=.065,right=.97,bottom=.31,top=.83);self.scheme_canvas=FigureCanvasQTAgg(self.scheme_figure);self.scheme_canvas.setMinimumHeight(80)
        page=W.QWidget();v=W.QVBoxLayout(page)
        self.analysis_page=page;upper=W.QWidget();uv=W.QVBoxLayout(upper);uv.setContentsMargins(0,0,0,0);uv.addWidget(self.scheme_canvas,1)
        self.scheme_table=table(['方案','状态','U','选择理由']);self.scheme_table.setMaximumHeight(160);uv.addWidget(self.scheme_table)
        self.analysis_split=W.QSplitter(VERTICAL);self.analysis_split.addWidget(upper);v.addWidget(self.analysis_split,1);self.tabs.addTab(page,'结果分析')
        safe_page=W.QWidget();sv=W.QVBoxLayout(safe_page);sv.setContentsMargins(0,0,0,0)
        self.safe_range_figure=Figure(figsize=(10,3.6));self.safe_range_canvas=FigureCanvasQTAgg(self.safe_range_figure)
        self.safe_range_canvas.setMinimumHeight(240);self.safe_range_plot_data=draw_safe_ranges(self.safe_range_figure,{})
        self.safe_range_scroll=W.QScrollArea();self.safe_range_scroll.setWidgetResizable(True);self.safe_range_scroll.setWidget(self.safe_range_canvas)
        self.safe_range_scroll.setMinimumHeight(90);sv.addWidget(self.safe_range_scroll,1);self.analysis_split.addWidget(safe_page)
        chart_page=W.QWidget();cv=W.QVBoxLayout(chart_page);self.chart_status=label('选择极端情况后显示参与计算的运动过孔的位移统计。');cv.addWidget(self.chart_status)
        self.distribution_figure=Figure(figsize=(9,6));self.distribution_figure.subplots_adjust(left=.07,right=.98,bottom=.26,top=.83,wspace=.25);self.distribution_canvas=FigureCanvasQTAgg(self.distribution_figure);self.distribution_canvas.setMinimumHeight(80);cv.addWidget(self.distribution_canvas,1)
        self.analysis_split.addWidget(chart_page);self.analysis_split.setSizes([330,400,0]);self.histogram_data=None;self._histogram_revealed=False
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
        accel.setToolTip('可用CUDA用于概率卷积及大型型变坐标批次；小任务和多进程工作器使用CPU。所有拓扑与最终DRC仍用CPU检查；不会联网安装依赖。')
        probability=group('过孔实体覆盖概率')
        choices={'probability_method':[('Sobol准蒙特卡洛','sobol'),('蒙特卡洛','monte_carlo'),('确定性积分（无随机，全网格）','quadrature')],
                 'space_cmap':[('浅色 · 青绿紫','pastel'),('黄绿蓝','YlGnBu'),('蓝紫','BuPu'),('Viridis','viridis'),('Cividis','cividis')],
                 'space_interpolation':[('无平滑','nearest'),('双线性','bilinear'),('双三次','bicubic')]}
        choices['probability_cmap']=[('清晰 · 蓝青','blue'),('青绿','teal'),('暖橙','amber'),('玫紫','rose')]+choices['space_cmap'][1:]+[('等感知 · 紫橙','plasma'),('深紫 · 暖白','magma'),('多色 · 高对比','turbo')]
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
        caches=group('缓存管理')
        spin(caches,'cache_max_entries','最大缓存个数 / 工程',10,100000)
        self.setting_fields['cache_max_entries'].setToolTip('普通结果超过上限后按最近使用淘汰；无位移状态固定保留。包括状态、动画、概率与Space Map。关闭软件后清理本次运行缓存。')
        cache_row=W.QWidget();cache_layout=W.QHBoxLayout(cache_row);cache_layout.setContentsMargins(0,0,0,0)
        self.clear_cache_button=W.QPushButton('清除缓存');menu=W.QMenu(self.clear_cache_button)
        menu.addAction('清除当前工程',lambda:self.clear_cached_results(False));menu.addAction('清除所有工程',lambda:self.clear_cached_results(True));self.clear_cache_button.setMenu(menu)
        self.cache_size_label=label('当前工程：0 B · 全部工程：0 B');cache_layout.addWidget(self.clear_cache_button);cache_layout.addWidget(self.cache_size_label,1);caches.addRow(cache_row)
        choice(probability,'probability_method','计算方法')
        spin(probability,'sample_exponent','样本数 = 2 的指数',8,18)
        spin(probability,'random_seed','随机种子',0,2147483647)
        spin(probability,'quadrature_order','确定性角度积分阶数',5,81)
        spin(probability,'probability_cell_um','网格 / μm（0自动）',0,1000,3)
        spin(probability,'angle_bins','实体角度分组上限',4,128)
        choice(probability,'probability_cmap','配色方案');choice(probability,'probability_interpolation','显示平滑')
        check=W.QCheckBox('显示良率级别范围');self.setting_fields['probability_show_ranges']=check;probability.addRow(check);check.toggled.connect(self.settings_changed)
        color=ColorButton();self.setting_fields['probability_target_color']=color;probability.addRow('当前级别虚线颜色',color);color.valueChanged.connect(self.settings_changed)
        ranges=RangeEditor();self.setting_fields['probability_ranges']=ranges;probability.addRow(ranges);ranges.valueChanged.connect(self.settings_changed)
        spin(probability,'probability_alpha','叠加不透明度',.05,.95,2)
        spin(probability,'probability_gamma','低概率增强 γ（越小越明显）',.1,1.,2)
        self.setting_fields['probability_gamma'].setToolTip('只调整颜色和透明度，不改变概率数值、边界或良率判定。0概率保持透明。')
        spin(probability,'contact_box_budget','接触概率自适应积分预算',128,32768)
        page.addWidget(label('实体概率按所选方法计算；虚线包络表示输入幅度全部组合，并非等概率边界。默认接触判定用概率上下界；积分不足时明确标为待细化。∞仅作当前位置二值判断。'))
        space=group('Space Map')
        choice(space,'space_scope','显示范围')
        choice(space,'space_cmap','配色方案');choice(space,'space_interpolation','显示平滑')
        spin(space,'space_resolution','长边网格数',31,401)
        spin(space,'space_alpha','叠加不透明度',.05,.95,2)
        animation=group('视图与预演运动')
        enabled=W.QCheckBox('启用渐变插值运动');self.setting_fields['animation_enabled']=enabled;animation.addRow(enabled);enabled.toggled.connect(self.settings_changed)
        back=W.QCheckBox('返回无偏移时播放渐变');self.setting_fields['animation_return_zero']=back;animation.addRow(back);back.toggled.connect(self.settings_changed)
        choices['animation_strategy']=[('算完连续播放','prepared'),('边算边显示','stream')]
        choice(animation,'animation_strategy','型变动画播放方式')
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
        spin(deformation,'deformation_iterations','最大窗口迭代轮数',1,64)
        spin(deformation,'deformation_joint_rounds','邻线联合修复轮数',0,32)
        spin(deformation,'deformation_local_steps','孔盘搜索级数',1,12)
        spin(deformation,'deformation_growth','搜索跨度递增倍率',1.1,3.,2)
        for key,title in [('deformation_route_enabled','启用三段追溯'),('deformation_general_bands','允许弯曲及分支适配段')]:
            check=W.QCheckBox(title);self.setting_fields[key]=check;deformation.addRow(check);check.toggled.connect(self.settings_changed)
        spin(deformation,'deformation_route_candidates','三段候选预算 / 条线路',1,256)
        spin(deformation,'deformation_min_length','最短适配长度 / 线宽',1,20,1)
        spin(deformation,'deformation_corner_guard','拐角端部预留 / 线宽',0,3,2)
        choices['deformation_execution']=[('自动（按硬件与图纸）','auto'),('单线程','serial'),('多线程','thread'),('多进程','process')]
        choice(deformation,'deformation_execution','搜索并行模式')
        spin(deformation,'deformation_workers','并行数量上限（0自动）',0,16)
        self.setting_fields['deformation_iterations'].setToolTip('达到预算仍未完成者显示未适配；减少搜索不会放宽最终DRC。')
        page.addWidget(label('连续性约束作用于型变位移场。孔盘调整失败后自动追溯三段式适配；未通过则恢复原图并显示琥珀叉号，型变校验列出原因。DRC检查固定RDL的≤5°非相邻边；型变另验证异网最小净距。'))
        safe=group('连续无异常接触范围')
        spin(safe,'safe_box_precision','端点搜索精度 / 输入单边幅度',.0001,.1,4)
        spin(safe,'safe_box_budget','每候选连续验证盒预算',32,32768)
        spin(safe,'safe_box_starts','端点扩张起点数',1,6)
        spin(safe,'safe_box_passes','体积交换轮数',1,8)
        yield_group=group('全输入无接触模型良率')
        choices['yield_method']=[('蒙特卡洛 + 99%置信区间','monte_carlo'),('确定性状态盒积分（不随机采样）','partition')]
        choice(yield_group,'yield_method','计算方法')
        spin(yield_group,'yield_sample_exponent','每DIE样本数 = 2 的指数',8,20)
        spin(yield_group,'yield_box_budget','全输入良率积分预算 / DIE',64,65536)
        spin(yield_group,'yield_tolerance','良率区间宽度目标（概率）',.000001,.1,6)
        self.setting_fields['safe_box_precision'].setToolTip('返回包含输入均值的安全内盒；多个端点顺序比较体积。未定候选拒绝，精度不代表全局最优误差。')
        channel=group('宽度分析 · 元素显示')
        drawing_modes=WidthDisplayEditor();self.setting_fields['width_display']=drawing_modes
        channel.addRow(drawing_modes);drawing_modes.valueChanged.connect(self.settings_changed)
        drc_group=group('DRC检查')
        spin(drc_group,'drc_tolerance_um','线宽/线距容差 ± / μm',-10000,10000,3)
        self.setting_fields['drc_tolerance_um'].setToolTip('有效最小值 = 工艺值 − 容差。正值放宽，负值收紧；默认0。只影响DRC与型变验收，不改变包围量或概率。')
        landing=W.QCheckBox('补块端部按包围量加孔盘');self.setting_fields['repair_landing_pad']=landing;drc_group.addRow(landing);landing.toggled.connect(self.settings_changed)
        landing.setToolTip('“修复补块”的直线补块默认只有固定RDL线宽，通常无法满足最小包围量，DRC会报“孔盘包围不足”；勾选后额外在过孔位置补一个包围量半径的孔盘，用于对比两种模型。')
        spin(drc_group,'repair_patch_width_um','补块直径 / μm（0=与过孔等宽）',0,10000,3)
        self.setting_fields['repair_patch_width_um'].setToolTip('“修复补块”所画直线补块的直径。0 表示每块补块取所连过孔的最小外形尺寸（最小外接矩形的短边），即与孔等宽；填正数则所有补块统一使用该直径。')
        recon=group('固定RDL重构预演')
        spin(recon,'reconstruction_length_um','切口距离 D / μm（0=自动）',0,100000,3)
        self.setting_fields['reconstruction_length_um'].setToolTip('切口线到落盘的距离：同一网络、彼此相距不远的多个落盘合为一条串，整条串只切一刀，这个距离从串里最远的那个落盘再往外量。0 表示自动取 2.5 个落盘直径（落盘直径按等面积圆的直径算），填正数则改用该长度。切口线始终垂直于这条串的走线（与走线两侧各成 90°）。同一块固定RDL跨多个芯片时，移动区只取本 DIE 芯片边框以内的铜，边框外的铜归相邻 DIE。')
        spin(recon,'reconstruction_overlap_um','重构落盘外扩 / μm',0,10000,3)
        spin(recon,'reconstruction_width_um','补线外扩 / μm（0=只覆盖切口）',0,10000,3)
        self.setting_fields['reconstruction_width_um'].setToolTip('补线多边形向外扩张的量；0 表示补线正好是两面切口的 4 个端点围成的多边形。补线不论裂隙大小都会生成，只有两面完全重合或缝隙已被铜盖住时才不重复补。')
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
        for key in ('deformation_general_bands','deformation_route_candidates','deformation_min_length','deformation_corner_guard'):
            self.setting_fields[key].setEnabled(self.setting_fields['deformation_route_enabled'].isChecked())
        self.setting_fields['deformation_workers'].setEnabled(self.setting_fields['deformation_execution'].currentData()!='serial')
        partition=self.setting_fields['yield_method'].currentData()=='partition'
        self.setting_fields['yield_sample_exponent'].setEnabled(not partition)
        for key in ('yield_box_budget','yield_tolerance'):self.setting_fields[key].setEnabled(partition)
        deterministic=self.setting_fields['probability_method'].currentData()=='quadrature'
        for key in ('sample_exponent','random_seed','angle_bins'):self.setting_fields[key].setEnabled(not deterministic)
        for key in ('quadrature_order',):self.setting_fields[key].setEnabled(deterministic)
    def settings_changed(self,*_):
        if self.loading:return
        values={k:(w.currentData() if isinstance(w,W.QComboBox) else w.isChecked() if isinstance(w,W.QCheckBox) else w.value()) for k,w in self.setting_fields.items()}
        updated=ViewSettings.from_dict(values)
        if all(getattr(updated,k)==getattr(self.view_settings,k) for k in values if k not in ('cache_max_entries','width_display')):
            display_change=updated.width_display!=self.view_settings.width_display
            self.view_settings=updated;self.cache_group.limit(updated.cache_max_entries);self.refresh_cache_status()
            if display_change and self.data:self._legend_stamp=None;self.plot_layout(preserve=True)
            return
        intent=self._motion_intent if self.transition.timer.isActive() else None
        self.stop_animation()
        old_settings=self.view_settings;updated=ViewSettings.from_dict(values)
        from .drc_rules import effective_rules
        try:effective_rules(self.params(),updated)
        except ValueError as exc:self.load_view_settings();self.error(str(exc));return
        deform=updated.deformation_key()!=old_settings.deformation_key();numeric=(updated.numeric_key()!=self.view_settings.numeric_key() or (updated.probability_show_ranges!=old_settings.probability_show_ranges and self.target_sigma.currentText()=='∞'));self.view_settings=updated;self.enable_settings();self.refresh_extreme_colors();self.cache_group.limit(updated.cache_max_entries);self.refresh_cache_status()
        live_motion=any(getattr(updated,k)!=getattr(old_settings,k) for k in ('animation_enabled','animation_return_zero','animation_duration_ms','animation_fps','animation_speed','animation_strategy'))
        repair_shape=(updated.repair_landing_pad!=old_settings.repair_landing_pad or updated.repair_patch_width_um!=old_settings.repair_patch_width_um)
        reconstruction_shape=any(getattr(updated,k)!=getattr(old_settings,k) for k in ('reconstruction_length_um','reconstruction_width_um','reconstruction_overlap_um'))
        if numeric:
            self.dirty=self.result is not None
            self.revision+=1;self.probability_data=None;self.space_data=None;self.repair_data=None
            if self.worker and self._evaluation_requested:self._coverage_pending=True;self.coverage_timer.start()
            if self.data:self.request_preview()
        elif live_motion and self.data and (self.preview_workers or intent or self.current_frame is not None):self.request_preview()
        elif (deform or repair_shape or reconstruction_shape) and (self.deformation_toggle.isChecked() or self.drc_toggle.isChecked() or self.width_toggle.isChecked() or self.repair_toggle.isChecked() or self.reconstruction_toggle.isChecked()):self.request_preview()
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
        data.update(input_nsigma=data['target_nsigma'],fixed_via_assignment=self.fixed_assignment);return Parameters.from_dict(data,require_rdl=False)
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
    def refresh_cache_status(self):
        size,total,count,pinned=self.cache_registry.statistics(self.cache_project_key)
        def fmt_size(n):
            return f'{n/1024**3:.2f} GiB' if n>=1024**3 else f'{n/1024**2:.2f} MiB' if n>=1024**2 else f'{n/1024:.1f} KiB' if n>=1024 else f'{n} B'
        self.cache_size_label.setText(f'当前工程：{fmt_size(size)}\n全部工程：{fmt_size(total)}'+(' · 清理中' if self.cache_clear_worker is not None else ''))
        self.cache_size_label.setToolTip(f'当前普通结果 {count-pinned} 个；固定保留 {pinned} 个。统计本次运行中所有打开过的工程缓存文件，关闭软件后清理。')
    def _attach_cache_group(self,key,count):
        self.cache_project_key=key;self.cache_group=self.cache_registry.get(key,count)
        self.session_values=self.cache_group.values;self.session_engines=self.cache_group.engines;self.engine=None
    def switch_project_cache(self,key,count):
        self.stop_animation();self._prepared_motion=None;self._prepared_package=None
        self.cancel_previews()
        if self.motion_worker:self.motion_worker.cancelled.set()
        if self.cache_group.key!=key:self.parked_groups.append(self.cache_group)
        self._attach_cache_group(key,count);self.cleanup_retired_sessions();self.refresh_cache_status()
    def reset_session_cache(self):
        self.stop_animation();self._prepared_motion=None;self._prepared_package=None
        self.cancel_previews()
        if self.motion_worker:self.motion_worker.cancelled.set()
        old=self.cache_registry.retire(self.cache_project_key)
        if old is not None:old.enabled=False;self.retired_sessions.append(old)
        self._attach_cache_group(self.cache_project_key,self.view_settings.cache_max_entries)
        self.cleanup_retired_sessions();self.refresh_cache_status()
    def clear_cached_results(self,all_projects=False):
        if self.cache_clear_worker is not None:return
        self.stop_animation();self._prepared_motion=None;self._prepared_package=None
        self.preview_ticket+=1;self.preview_pending=None;self.motion_pending=None;self.motion_generation+=1
        for worker in [self.worker,self.motion_worker,*self.preview_workers]:
            if worker:worker.cancelled.set()
        keys=list(self.cache_registry.groups) if all_projects else [self.cache_project_key]
        groups=[self.cache_registry.retire(key) for key in keys if key in self.cache_registry.groups]
        self._clear_native_when_idle=True
        for group_ in groups:group_.enabled=False
        self.retired_sessions.extend(groups);self._attach_cache_group(self.cache_project_key,self.view_settings.cache_max_entries)
        self.cache_size_label.setText('正在清除缓存文件…');self.clear_cache_button.setEnabled(False)
        def work(progress):
            for i,group_ in enumerate(groups):
                progress(round(100*i/max(1,len(groups))),'清除工程缓存');group_.clear()
            progress(100,'缓存文件已清除')
        worker=Worker(work,self);self.cache_clear_worker=worker;self.watch_worker(worker,'清理缓存')
        def finish():
            self.cache_clear_worker=None;self.clear_cache_button.setEnabled(True);self.refresh_cache_status();self.cleanup_retired_sessions();worker.deleteLater()
        worker.finished.connect(finish);worker.start()
    def cleanup_retired_sessions(self):
        if self._tasks:return
        for group_ in self.retired_sessions:
            group_.close()
            if group_ in self.cache_registry.retired:self.cache_registry.retired.remove(group_)
        self.retired_sessions.clear()
        if getattr(self,'_clear_native_when_idle',False):
            from . import native_backend
            native_backend.close();self._clear_native_when_idle=False
        for group_ in self.parked_groups:
            if group_ is self.cache_group:continue
            for engine in group_.engines.values():
                for deformer in engine.deformers.values():deformer.release_workers()
        self.parked_groups.clear()
    def assessment_identity(self,data,roles,order,p,settings):
        from .session_cache import logical_fingerprint
        return ('assessment',logical_fingerprint((data.geometry_id,data.top_name,roles,order,asdict(p),settings.numeric_key(),settings.drc_tolerance_um)))
    def preview_identity(self,data,roles,order,motions,params,result):
        from .session_cache import logical_fingerprint
        return logical_fingerprint((data.geometry_id,data.top_name,roles,order,motions,asdict(params),result is not None,(result or {}).get('parameters'),(result or {}).get('contact_analysis'),(result or {}).get('issues')))
    def clear_preview(self):
        self.extremes=None;self.active_preview=None;self.owner_cache=None;self.page_items=[];self.preview_page=0;self.state_result=None
        self.preview_ticket+=1;self.preview_pending=None;self._prepared_motion=None;self.engine=None;self.current_frame=None;self.shown_states=None;self.report_dirty=True;self.repair_data=None
        self.extreme_table.blockSignals(True);self.extreme_table.clearContents();self.extreme_table.setRowCount(1);self.extreme_table.setCurrentCell(0,0);self.extreme_table.blockSignals(False)
        self.state_table.setRowCount(0);self.issue_table.setRowCount(0);self._issue_table_stamp=None;self.page_summary.setText('尚未评估')
    def coverage_changed(self,*_):
        if self.loading:return
        if self.target_sigma.currentText() not in ('0','∞'):self.reference_nsigma=float(self.target_sigma.currentText())
        self.stop_animation();self.revision+=1;self.engine=None;self.connections=None
        self.probability_data=None;self.space_data=None;self.repair_data=None;self.preview_ticket+=1;self.preview_pending=None
        self.cancel_previews()
        self._coverage_pending=True;self.coverage_timer.start()
        self.statusBar().showMessage('良率级别已改变，正在更新概率、Space Map和窗口判定…')
    def refresh_coverage(self):
        if not self._coverage_pending or not self.data:return
        if self.worker is not None:self.coverage_timer.start();return
        self._coverage_pending=False
        if self.result is not None or self._evaluation_requested:self.run_evaluation(automatic=True)
        else:self.request_preview()
    def invalidate(self,*_):
        if self.loading:return
        self.revision+=1;self.dirty=self.result is not None;self.refresh_extreme_colors();self.engine=None;self.connections=None;self.probability_data=None;self.space_data=None;self.repair_data=None
        self.statusBar().showMessage('参数已修改；保留上次计算结果。预演控制区使用当前工艺，重新评估后更新统计结论。')
        if self.data and (self.active_preview is not None or self.manual_states is not None or self.probability_toggle.isChecked() or self.space_toggle.isChecked() or self.deformation_toggle.isChecked() or self.drc_toggle.isChecked() or self.width_toggle.isChecked() or self.repair_toggle.isChecked() or self.reconstruction_toggle.isChecked()):
            try:self.request_preview()
            except ValueError:pass
    def drop_result(self):
        self.result=None;self.clear_preview();self.manual_states=None;self.dirty=False;self.probability_data=None;self.space_data=None;self.repair_data=None
        self.scheme_table.setRowCount(0);self.die_table.setRowCount(0)
        self.safe_range_plot_data=draw_safe_ranges(self.safe_range_figure,{});self.safe_range_canvas.setMinimumHeight(240);self.safe_range_canvas.draw_idle()
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
        pending=getattr(self,'_queued_node_edit',None)
        if pending:
            self._queued_node_edit=None
            if pending[0] is self.data and not getattr(self,'_closing',False):
                QtCore.QTimer.singleShot(0,lambda:self.apply_node_edits(pending[1]) if self.data is pending[0] else None)
            else:self.log_bus.emit('图纸已切换，旧图纸的排队节点编辑已取消。')
        if self._coverage_pending:self.coverage_timer.start()
    def open_gds(self):
        path,_=W.QFileDialog.getOpenFileName(self,'输入GDS','','GDSII (*.gds *.gdsii *.GDS)')
        if path:self.load_gds(path)
    def load_gds(self,path,top=None,project=None,project_path=None):
        def read_and_prepare(progress):
            data=read_gds(path,top,progress)
            if isinstance(data,dict):return data
            settings=project if project and (not project.get('sha256') or project['sha256']==data.sha256) else {}
            data=edited_snapshot(data,settings.get('node_edits',[]))
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
            self.set_data(data,settings,project_path=project_path)
            self.project_path=Path(project_path).resolve() if settings and project_path and Path(project_path).resolve().parent==self.jobs_dir.resolve() else None
            self.refresh_jobs()
        self.start_worker(read_and_prepare,accepted)
    def set_data(self,data,project=None,project_path=None):
        from .session_cache import logical_fingerprint
        project=normalize_project(project) if project else {}
        if data.node_edits!=project.get('node_edits',[]):
            data=edited_snapshot(data,project.get('node_edits',[]))
        for message in data.import_warnings:self.log_bus.emit(message,'WARNING')
        cache_settings=ViewSettings.from_dict(project.get('settings'))
        cache_id=logical_fingerprint((str(Path(project_path or data.path).resolve()),data.sha256,data.top_name))
        self.switch_project_cache(cache_id,cache_settings.cache_max_entries)
        pars=Parameters.from_dict(project.get('parameters',{}),require_rdl=False);self.manual_states=None;self.probability_data=None;self.space_data=None;self.repair_data=None;self.dirty=False
        self.coverage_timer.stop();self._coverage_pending=False;self._evaluation_requested=False
        self.loading=True
        try:
            self.data=data;self.gds_label.setText(Path(data.path).name+'\n'+str(Path(data.path).resolve()));self.gds_label.setToolTip(str(Path(data.path).resolve()))
            self.view_settings=ViewSettings.from_dict(project.get('settings'));self.load_view_settings();self.layer_styles=project.get('layer_styles',{})
            for name,value in project_overlays(project).items():
                toggle=getattr(self,name+'_toggle')
                toggle.blockSignals(True)
                toggle.setChecked(value)
                toggle.blockSignals(False)
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
        self.drop_result();self.invalidate();self.plot_layout()
        remembered=self.cache_group.last_assessment
        if remembered is not None:
            key=self.assessment_identity(data,self.roles(),self.ordered_layers(),self.params(),self.view_settings)
            if remembered[0]==key:self.display_result(remembered[1]);self.log_bus.emit('已恢复本次运行中此工程的评估缓存')
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
        connection=(self.case or {}).get('connections')
        positional=(connection.summary().get('positional_ownership_by_layer') or {}) if connection else {}
        for i,key in enumerate(self.ordered_layers()):
            role=self.roles()[key];owners=(self.case or {}).get('ownership',{}).get(key,[])
            if role in ('运动RDL','运动过孔'):
                counts=[f'DIE{owner+1}: {int(np.sum(owners==owner))}个' for owner in sorted(set(owners)) if owner>=0]
                unresolved=int(np.sum(np.asarray(owners)<0))
                if unresolved:counts.append(f'{unresolved}个归属待确认')
                if positional.get(key):counts.append(f'{positional[key]}个按位置归属（无单一连接归属）')
                detail='；'.join(counts) or '设置边框与上下层序后追溯'
                motion='按下层连接随芯片'
            else:motion='随对应芯片' if role=='芯片边框' else '固定';detail='由用途确定'
            for j,value in enumerate((key,role,motion,detail)):
                item=W.QTableWidgetItem(value);item.setToolTip(value);self.motion_table.setItem(i,j,item)
        for j,width in enumerate((70,100,150)):self.motion_table.setColumnWidth(j,width)
        self.motion_table.horizontalHeader().setStretchLastSection(True)
        if not self.loading and self.active_preview is None and self.manual_states is None:self.plot_layout(preserve=True)
    def visibility_changed(self,key):
        if self.loading:return
        self.plot_layout(preserve=True)
    def top_changed(self,name):
        if not self.loading and self.data and name!=self.data.top_name:self.load_gds(self.data.path,name)
    def validate_geometry(self):
        if not self.data:self.error('请先打开GDS');return
        try:
            self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers());self.refresh_motion_table(self.motions());self.plot_layout()
            self.statusBar().showMessage(f"几何有效：{len(self.case['dies'])}颗芯片；运动过孔{sum(map(len,self.case['pins']))}个；固定过孔{len(self.case['fixed_via'])}个")
        except Exception as e:self.error(e)
    def reset_plot(self):
        if hasattr(self,'navigation'):self.navigation.cancel_zoom()
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
        if frame and frame.get('reconstruction') and self.reconstruction_toggle.isChecked() and self.result is not None:
            source=frame['reconstruction'];keep=('die','layer','index','net','movable_area_um2','shift_um','gap_um','bridge_length_um','bridge_area_um2','cut_distance_um','orphan')
            self.result['preview_reconstruction']=dict(states=frame['states'].tolist(),scope=source['scope'],summary=source['summary'],parameters=source.get('parameters',{}),pieces=[{k:q[k] for k in keep if k in q} for q in source['pieces']]);self.report_dirty=True
        self.update_width_report(frame)
        if frame and frame.get('drc_rules') and self.result is not None:
            self.result['preview_drc_rules']=frame['drc_rules'];self.report_dirty=True
        message=('预演已更新' if frame is not None else '名义位置')+f' · 当前所选方案 {len(self.filtered_issues)} 处接触或风险'
        if frame is not None:
            from .statistics import mean_and_bounds
            params=self.params();mu,_=mean_and_bounds(params,len(frame['states']));bounds=np.array([mean_and_bounds(params.for_die(i),len(frame['states']))[1] for i in range(len(frame['states']))])
            if np.any(abs(frame['states']-mu)>bounds+1e-10):message+=' · 手动状态超出输入最大幅度；概率云仍为配置分布'
        if frame and self.deformation_toggle.isChecked() and 'deformation' in frame:
            q=frame['deformation'];message+=f" · 型变 {q['accepted_count']} 个铜连通区，未解 {len(q['diagnostics'])} 个"
        if frame and frame.get('deformation',{}).get('search_quality')=='interactive':message+=' · 交互候选已复核，停止操作后完整搜索'
        if frame and frame.get('motion_issues'):message+=f" · {len(frame['motion_issues'])}个运动图形归属待确认，已用橙色虚线标记"
        if frame and frame.get('width_analysis') and self.width_toggle.isChecked():
            q=frame['width_analysis']['counts'];message+=f" · 保宽足够 {q['宽度足够']}，需收窄 {q['需局部收窄']}，宽度不足 {q['宽度不足']}"
        self.statusBar().showMessage(message)
        self.refresh_extreme_colors(frame)
    def present_frame(self,frame):
        self.stop_animation()
        self._motion_intent=('frame',frame)
        def complete():
            self._motion_intent=None;self.transition.stop();self.motion_generation+=1;self.motion_pending=None
            if self._animation_cleanup:
                cleanup=self._animation_cleanup;self._animation_cleanup=None;cleanup()
            self._interpolated_states=None;self.current_frame=frame;self.plot_layout(preserve=True);self.update_histograms()
            if frame is not None:self.update_state_table(frame['states'])
            elif self.case:self.update_state_table(np.zeros((len(self.case['dies']),3)))
            self.show_frame_status(frame)
        if (not self.view_settings.animation_return_zero and (frame is None or not np.any(frame['states']))) or not self.view_settings.animation_enabled or self.engine is None or self.case is None or (self.deformation_toggle.isChecked() and frame is not None):
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
            if (self.deformation_toggle.isChecked() and self.result) or self.drc_toggle.isChecked() or self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked():
                self.motion_pending=(self.motion_generation,self.revision,self.engine,states.copy());self.launch_motion_frame()
            else:self.show_motion_frame(self.engine.frame(states))
        self.statusBar().showMessage('插值运动中；控制区与接触标记随当前帧实时更新。')
        self.transition.start(duration,steps,update,complete)
    def play_prepared_motion(self,frame,motion):
        self.stop_animation();self._motion_intent=('frame',frame)
        replay=motion['frames'];fractions=np.array([q[0] for q in replay]);last=[-1]
        def update(t):
            i=int(np.searchsorted(fractions,t,side='right'))-1
            if i>=0 and i!=last[0]:last[0]=i;self.show_motion_frame(replay[i][1])
        def complete():
            self._motion_intent=None;self._interpolated_states=None;self.current_frame=frame
            self.plot_layout(preserve=True);self.update_histograms();self.show_frame_status(frame)
            if frame is not None:self.update_state_table(frame['states'])
            elif self.case:self.update_state_table(np.zeros((len(self.case['dies']),3)))
        if not self.view_settings.animation_enabled or (not self.view_settings.animation_return_zero and (frame is None or not np.any(frame['states']))):complete();return
        self.statusBar().showMessage(('播放缓存型变动画' if motion['cached'] else '播放已校验型变动画')+f" · {len(replay)} 个中间帧")
        if replay and (frame is None or not np.any(frame['states'])):
            self.show_motion_frame(replay[0][1]);last[0]=0
        self.transition.start(motion['duration'],motion['steps'],update,complete)

    def show_motion_frame(self,frame):
        self._interpolated_states=frame['states'];self.current_frame=frame;self._in_animation=True
        try:self.plot_layout(preserve=True);self.update_state_table(frame['states'])
        finally:self._in_animation=False
    def update_state_table(self,states):
        if self.state_table.rowCount()!=len(states):self.state_table.setRowCount(len(states))
        kind='手动输入' if self.manual_states is not None else '代表极端' if self.active_preview is not None else '名义位置'
        for i,z in enumerate(states):
            for j,value in enumerate([i+1,fmt(z[0],6),fmt(z[1],6),fmt(math.degrees(z[2]),6),kind]):
                text=str(value);item=self.state_table.item(i,j)
                if item is None:self.state_table.setItem(i,j,W.QTableWidgetItem(text))
                elif item.text()!=text:item.setText(text)

    def launch_motion_frame(self):
        if self.motion_worker is not None or self.motion_pending is None or self.preview_workers:return
        generation,revision,engine,states=self.motion_pending;self.motion_pending=None
        worker=Worker(lambda progress:engine.frame(states,progress,quick=True),self);self.motion_worker=worker;self.watch_worker(worker,'插值帧')
        def accepted(frame):
            if generation==self.motion_generation and revision==self.revision and self.transition.timer.isActive():self.show_motion_frame(frame)
        def finished():
            self.motion_worker=None;worker.deleteLater();self.launch_motion_frame();self.launch_preview()
        worker.done.connect(accepted);worker.finished.connect(finished)
        worker.failed.connect(lambda message:self.statusBar().showMessage('型变中间帧跳过：'+message.split('\n')[0]));worker.start()
    def update_width_report(self,frame):
        data=(frame or {}).get('width_analysis')
        if self.result is None or not data or not self.width_toggle.isChecked():return
        show=self.width_sufficient_toggle.isChecked()
        visible=[r for r in data['rows'] if show or r['status']!='宽度足够']
        keys=('status','pair','refs','original','required','current','point','plane','gap_um','nominal_gap_um','slots_um','widths_um','minimum_um','keep_um','split','locked','unresolved','narrowing_um')
        self.result['preview_width_analysis']=dict(states=frame['states'].tolist(),counts=data['counts'],scope=data['scope'],parameters=data['parameters'],examined=data['examined'],
                                                   displayed=len(visible),edge_violations=[{k:e[k] for k in ('layer','indices','point','gap_um','required_um')} for e in data.get('edge_violations',[])],rows=[{k:r[k] for k in keys} for r in visible[:100]])
        self.report_dirty=True

    def width_filter_changed(self,*_):
        if not self.loading and self.data:
            self.update_width_report(self.current_frame)
            self.plot_layout(preserve=True)
    def width_changed(self,*_):
        if self.loading:return
        if self.width_toggle.isChecked():self.request_preview()
        else:self.plot_layout(preserve=True)
    def repair_changed(self,*_):
        if self.loading:return
        if self.repair_toggle.isChecked():
            self.statusBar().showMessage('修复补块：按过孔偏移在固定RDL层生成直线补块，并重做补块后的DRC…')
            self.request_preview()
        else:
            self.repair_data=None;self.plot_layout(preserve=True)
    def reconstruction_changed(self,*_):
        if self.loading:return
        if self.reconstruction_toggle.isChecked():
            self.statusBar().showMessage('重构预演：按串联落盘自动划分固定RDL移动区（跨芯片时限定在本 DIE 芯片边框内）、按姿态移动、用切口 4 端点多边形补上裂隙…')
            self.request_preview()
        else:
            # Refresh the displayed frame after removing the reconstruction overlay.
            if self.width_toggle.isChecked():self.request_preview()
            else:self.plot_layout(preserve=True)
    def deformation_changed(self,*_):
        if self.loading:return
        if self.deformation_toggle.isChecked() and self.result is None:
            self.statusBar().showMessage('预演分析已启用；先运行方案评估，再选择极端情况。');return
        self.request_preview()
    def escape_action(self):
        if hasattr(self,'navigation'):self.navigation.cancel_zoom()
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
        if self._resizing or not self.data or self.tabs.currentIndex()!=0:return
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
        if not self._plotting and self.data and self.tabs.currentIndex()==0:self.viewport_timer.start()
    def viewport_changed(self,force=False):
        if self._plotting or not self.data:return
        self._in_viewport=True
        try:self.plot_layout(preserve=True)
        finally:self._in_viewport=False
    def plot_layout(self,preserve=False):
        if self._plotting:return
        if self.tabs.currentIndex()!=0:
            # Hidden layout canvases need no paths, raster compositing or Qt tables.
            # Keep the numerical pose/connector data current for analysis charts.
            self._layout_pending=True;frame=self.current_frame
            self.preview_applied=frame is not None;self.shown_states=frame['states'] if frame else None
            self.update_connector_segments();return
        self._layout_pending=False
        self._plotting=True
        try:self._plot_layout(preserve)
        finally:self._plotting=False
    def update_connector_segments(self):
        frame=self.current_frame
        roles=self.roles();motions=self.motions();segments=[];display=[]
        if frame:
            for key in self.ordered_layers():
                if roles[key]!='运动过孔':continue
                q=self.geometry_cache.layers[key]
                if motions.get(key)=='fixed':continue
                mask=np.zeros(len(q['centers']),bool)
                for region in frame['contact']['regions']:
                    if region['key']==key:mask[region['index']]=True
                segments.append(np.stack((q['centers'][mask],frame['layers'][key]['centers'][mask]),axis=1))
                if self.visible_boxes[key].isChecked():display.append(segments[-1])
        self.all_connector_segments=np.concatenate(segments) if segments else np.empty((0,2,2))
        self.connector_segments=np.concatenate(display) if display else np.empty((0,2,2))

    def geometry_patch(self,geometry):
        if not hasattr(self,'_path_cache'):self._path_cache={}
        key=id(geometry)
        if key not in self._path_cache:self._path_cache[key]=(geometry,geometry_path(geometry))
        return PathPatch(self._path_cache[key][1])

    def _plot_layout(self,preserve=False):
        if not getattr(self,'_axes_configured',False):
            self.ax.set(xlabel='',ylabel='');self.ax.set_aspect('equal',adjustable='datalim');self.ax.grid(alpha=.12)
            self.ax.ticklabel_format(style='plain',useOffset=False,axis='both')
            self.ax.xaxis.set_minor_locator(DistanceMinorLocator());self.ax.yaxis.set_minor_locator(DistanceMinorLocator())
            self.ax.tick_params(which='major',labelsize=8,pad=2,length=4);self.ax.tick_params(which='minor',length=2,width=.55)
            self._axes_configured=True
        if not self.data:self.canvas.draw_idle();return
        roles=self.roles();motions=self.motions();order=self.ordered_layers();frame=self.current_frame
        if getattr(self,'_path_frame',None) is not frame:self._path_frame=frame;self._path_cache={}
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
        analysis=frame.get('width_analysis') if frame and self.width_toggle.isChecked() else None
        modes=self.view_settings.width_display if analysis is not None else {}
        self.width_hidden_sources={k for k,r in roles.items() if modes.get(r if r in modes else '其他')=='hide'}
        for rank,key in enumerate(order):
            values=frame['layers'][key]['polygons'] if frame and key in frame['layers'] else self.data.layers[key]
            role=roles[key];sty=self.style_for(key);visible=self.visible_boxes[key].isChecked()
            polys=values
            ids=self.viewport_index.query(key,self.view_bounds,states) if visible else np.empty(0,int)
            deformation=frame.get('deformation',{}) if frame and self.deformation_toggle.isChecked() else {}
            replacements=deformation.get('replacements',{}).get(key,[])
            hidden=set() if analysis is not None else {i for q in replacements for i in q['indices']}
            ids=np.array([i for i in ids if i not in hidden],int)
            mode=modes.get(role,modes.get('其他','show'))
            if mode!='show':
                rgb=tint(sty['color'],mode,sty['alpha']);sty=dict(sty,color=rgb[:3],alpha=rgb[3])
            visible=visible and mode!='hide'
            drawn=[polys[i] for i in ids] if visible else []
            if key not in self.layer_artists:
                self.layer_artists[key]=PolyCollection([]);self.ax.add_collection(self.layer_artists[key])
            artist=self.layer_artists[key]
            geometry_stamp=(id(values),ids.tobytes(),visible)
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

                path_stamp=tuple(id(q[part]) for q in visible_replacements)
                if getattr(overlay,'_path_stamp',None)!=path_stamp:overlay.set_paths([self.geometry_patch(q[part]) for q in visible_replacements]);overlay._path_stamp=path_stamp
                overlay.set_visible(visible and bool(visible_replacements));overlay.set_zorder(artist.get_zorder()+(.002 if part=='dark' else .001))
                color=np.array(matplotlib.colors.to_rgb(sty['color']))
                overlay.set_facecolor(color if part=='geometry' else color*.70);overlay.set_alpha(sty['alpha'])
            adapt.set_alpha(sty['alpha']);adapt.set_paths([self.geometry_patch(q['adaptation']) for q in visible_replacements if q.get('adaptation') is not None and not q['adaptation'].is_empty]);adapt.set_visible(visible and bool(visible_replacements));adapt.set_zorder(artist.get_zorder()+.003)
        self._artist_frame=stamp
        for artist in self.invalid_artists:artist.remove()
        self.invalid_artists=[]
        for q in getattr(self.data,'ignored_geometry',[]):
            if not self.visible_boxes[q['layer']].isChecked():continue
            x0,y0,x1,y1=q['bounds'];pad=max(1.,self.data.dbu_um*2)
            path=np.array([[x0-pad,y0-pad],[x1+pad,y0-pad],[x1+pad,y1+pad],[x0-pad,y1+pad],[x0-pad,y0-pad]])
            artist=LineCollection([path],colors='#d72b91',linestyles=(0,(3,3)),linewidths=.7,zorder=13)
            self.ax.add_collection(artist);self.invalid_artists.append(artist)
        self.update_connector_segments()
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
        patches=[self.geometry_patch(q['geometry']) for q in frame['contact'].get('intrusions',[]) if self.visible_boxes[q['key']].isChecked() and self.visible_boxes[q['target_layer']].isChecked() and overlaps(q['geometry'].bounds,self.view_bounds)] if frame else []
        area.set_paths(patches);area.set_alpha(self.view_settings.intrusion_alpha);area.set_visible(self.show_issues.isChecked() and bool(patches))
        # Tables retain every issue; only in-view markers/connectors are drawn.
        marker_area=self.marker_size('contact')
        marker_radius=math.sqrt(marker_area)*self.figure.dpi/144*np.ptp(self.ax.get_xlim())/max(1,self.ax.bbox.width)
        self.rendered_issues=[q for q in self.display_issues if overlaps((*np.asarray(q['point'])-marker_radius,*np.asarray(q['point'])+marker_radius),self.view_bounds)]
        segments=[np.array([self.issue_origin(q),q['point']]) for q in self.display_issues]
        segments=[a for a in segments if overlaps((*a.min(axis=0),*a.max(axis=0)),self.view_bounds)]
        circles.set_sizes([marker_area]);circles.set_edgecolor(self.view_settings.contact_marker_color);circles.set_offsets(np.asarray([q['point'] for q in self.rendered_issues]).reshape(-1,2));circles.set_visible(self.show_issues.isChecked() and bool(self.rendered_issues))
        lines.set_segments(segments);lines.set_visible(self.show_issues.isChecked() and bool(segments))
        if not hasattr(self,'violation_artist') or self.violation_artist.axes is None:
            self.violation_artist=PatchCollection([],facecolor='#e956b5',edgecolor='#ca398f',linewidth=.5,alpha=.48,zorder=10.2);self.ax.add_collection(self.violation_artist)
        # Repair mode replaces the DRC source, so the two never disagree on screen.
        if self.repair_toggle.isChecked() and self.repair_data is not None:
            violations=self.repair_data['violations']
        else:
            violations=frame.get('drc',frame.get('deformation',{}).get('violations',[])) if frame else []
        vp=[self.geometry_patch(q['geometry']) for q in violations if any(self.visible_boxes[k].isChecked() for k in q.get('layers',(q['layer'],))) and overlaps(q['geometry'].bounds,self.view_bounds)]
        self.violation_artist.set_paths(vp);self.violation_artist.set_visible((self.drc_toggle.isChecked() or self.repair_toggle.isChecked()) and bool(vp))
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
        if hasattr(self,'width_overlay'):self.width_overlay.draw()
        if hasattr(self,'repair_overlay'):self.repair_overlay.draw()
        if hasattr(self,'reconstruction_overlay'):self.reconstruction_overlay.draw()
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
        visible=zoom>=.995 and any(self.visible_boxes[k].isChecked() and k not in getattr(self,'width_hidden_sources',()) for k,r in self.roles().items() if r=='芯片边框')
        for label_ in self.die_labels:label_.set_visible(visible)
        if self.legend is not None:self.legend.set_visible(visible)
    def issue_origin(self,issue):
        origin=np.asarray(issue['origin'],float)
        if self.current_frame is not None:
            owner=int(issue['die'])-1
            if owner>=0:
                origin=rigid_transform(origin,self.rotation_centers()[owner],self.current_frame['states'][owner])
        return origin
    def refresh_legend(self):
        stamp=(tuple((k,self.visible_boxes[k].isChecked(),str(self.style_for(k))) for k in self.ordered_layers()),
               self.current_frame is not None,bool(getattr(self,'deformation_diagnostics',[])),self.probability_toggle.isChecked(),id(self.probability_data),self.view_settings.probability_cmap,self.view_settings.probability_alpha,self.view_settings.probability_gamma,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.view_settings.contact_marker_color,self.view_settings.unadapted_marker_color,self.width_toggle.isChecked(),self.width_sufficient_toggle.isChecked(),self.repair_toggle.isChecked(),self.reconstruction_toggle.isChecked(),bool(self.current_frame and self.current_frame.get('motion_issues')))
        if stamp==self._legend_stamp:return
        self._legend_stamp=stamp
        if self.legend is not None:self.legend.remove()
        entries={}
        for key,role in self.roles().items():
            if not self.visible_boxes[key].isChecked() or key in getattr(self,'width_hidden_sources',()):continue
            sty=self.style_for(key)
            name=(key+' '+sty['label']) if key in self.layer_styles else sty['label']
            entries[name]=Patch(facecolor=sty['color'] if sty['fill'] else 'none',edgecolor=sty['color'],alpha=sty['alpha'],label=name)
        handles=list(entries.values())
        if getattr(self,'deformation_diagnostics',[]):handles.append(Line2D([],[],marker='x',ls='none',color=self.view_settings.unadapted_marker_color,label='未适配：保留原图'))
        if self.repair_toggle.isChecked():
            handles += [Patch(facecolor='#d98a2b',edgecolor='#8a5a14',alpha=.45,label='固定RDL直线补块'),
                        Patch(facecolor='#c0392b',edgecolor='#8e2b21',alpha=.5,hatch='///',label='补块新增违规')]
        if self.width_toggle.isChecked():
            modes=self.view_settings.width_display
            for key,label,color in [('运动过孔孔盘','运动孔盘','#589881'),('固定过孔孔盘','固定孔盘','#6985a5'),('固定障碍','固定障碍','#85848e'),('边距','封装边距不足','#c44956')]:
                if modes[key]!='hide':handles.append(Patch(facecolor=tint(color,modes[key],.65),label=label))
            if modes['截面']!='hide':
                for title,color in [('宽度不足','#c44956'),('需局部收窄','#bd8127'),('宽度足够','#498d79')]:
                    if title!='宽度足够' or self.width_sufficient_toggle.isChecked():
                        handles.append(Patch(facecolor='none',edgecolor=tint(color,modes['截面']),label=title))
        if self.reconstruction_toggle.isChecked():
            handles += [Patch(facecolor='#9fb3c8',edgecolor='#5d7894',alpha=.20,hatch='//',label='重构：移动区原位（冷色，移走后空出）'),
                        Patch(facecolor='#e07a2f',edgecolor='#8f4712',alpha=.78,label='重构：重构后的铜（暖色）'),
                        Patch(facecolor='#f5b955',edgecolor='#a86f16',alpha=.80,label='重构：补线（切口 4 端点围成）'),
                        Line2D([],[],color='#8a4b3a',ls=(0,(6,3)),label='重构：切口线')]
        if self.current_frame and self.current_frame.get('motion_issues'):handles.append(Line2D([],[],color='#bf862c',ls='--',label='运动归属未定：保持名义位置'))
        if getattr(self.data,'ignored_geometry',[]):handles.append(Line2D([],[],color='#d72b91',ls='--',lw=.7,label='忽略的无效图形'))
        if self.current_frame is not None:
            handles += [Line2D([],[],color='#3c9995',ls='--',label='最小包围区域'),Line2D([],[],color='#aa78ab',ls='--',label='线距控制区域'),Line2D([],[],color='#92979e',ls='--',label='当前孔中心→接触位置')]
        if self.probability_toggle.isChecked() and self.probability_data:
            maximum=max((float(q['probability'].max()) for q in self.probability_data['maps']),default=0)
            norm=matplotlib.colors.PowerNorm(self.view_settings.probability_gamma,0,1)
            for value,title in ((.001,'覆盖概率0.1%（低概率已增强）'),(maximum,f'覆盖概率最大{maximum:.1%}')):
                rgba=list(self.probability_colormap()(norm(value)));rgba[3]*=self.view_settings.probability_alpha
                handles.append(Patch(facecolor=rgba,label=title))
        if self.probability_toggle.isChecked() and self.probability_data and self.view_settings.probability_show_ranges:
            levels=dict(self.view_settings.probability_ranges);levels[self.params().target_nsigma]=self.view_settings.probability_target_color
            for level,color in levels.items():
                name='全部输入范围' if level==0 else '∞ 名义位置' if math.isinf(level) else f'{level:g}σ 有界扫掠包络'
                handles.append(Line2D([],[],color=color,ls='--',lw=1.25,label=name))
        self.legend=self.ax.legend(handles=handles,loc='upper right',fontsize=7,framealpha=1,ncol=1)
    def draw_probability(self):
        active=self.probability_toggle.isChecked() and self.probability_data is not None
        stamp=(id(self.probability_data),bool(active),self.view_settings.probability_alpha,self.view_settings.probability_cmap,self.view_settings.probability_gamma,self.view_settings.probability_interpolation,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.target_sigma.currentText())
        if stamp!=self.probability_stamp:
            for a in self.probability_artists:a.remove()
            self.probability_artists=[];self.probability_stamp=stamp
            if active:
                cmap=self.probability_colormap()
                for q in self.probability_data['maps']:
                    values=np.ma.masked_where(q['probability']==0,q['probability'])
                    a=self.ax.imshow(values,extent=q['extent'],origin='lower',cmap=cmap,norm=matplotlib.colors.PowerNorm(self.view_settings.probability_gamma,0,1),interpolation=self.view_settings.probability_interpolation,alpha=self.view_settings.probability_alpha,zorder=4)
                    a._via_layer=q['key'];e=q['extent'];a._world_bounds=(e[0],e[2],e[1],e[3]);self.probability_artists.append(a)
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
        name=self.view_settings.probability_cmap
        if getattr(self,'_probability_cmap_name',None)!=name:
            palettes={'blue':['#afd2e5','#559cbb','#19557a'],'teal':['#b1d7c9','#55a997','#185f59'],'amber':['#f3d49d','#dea34b','#934513'],'rose':['#dcb4cf','#b979ad','#713874']}
            base=LinearSegmentedColormap.from_list('via_probability' if name=='blue' else name,palettes[name]) if name in palettes else matplotlib.colormaps[name]
            x=np.linspace(0,1,4096);colors=base(x);colors[:,3]=np.sqrt(x)
            from matplotlib.colors import ListedColormap
            self._probability_cmap=ListedColormap(colors,name=base.name);self._probability_cmap.set_bad((0,0,0,0));self._probability_cmap_name=name
        return self._probability_cmap
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
                    artist._source_bounds=q['domain'].bounds
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
                from .space_map import package_map,package_display
                visible={k for k,b in self.visible_boxes.items() if b.isChecked()}
                resolution=min(31,cfg.space_resolution) if getattr(self,'_in_animation',False) else cfg.space_resolution
                key=(id(self.space_data),None if self.shown_states is None else self.shown_states.tobytes(),tuple(sorted(visible)),resolution)
                if getattr(self,'_package_field_key',None)!=key:
                    centers=self.rotation_centers()
                    prepared=getattr(self,'_prepared_package',None)
                    full_key=(key[0],key[1],resolution)
                    cache_key=('package',getattr(self.engine,'signature',id(self.space_data)),key[1],resolution)
                    full=prepared[1] if prepared is not None and prepared[0]==full_key else self.session_values.get_or_compute(cache_key,lambda:package_map(self.space_data,self.case['board'],centers,self.shown_states,None,resolution))
                    self._package_field=package_display(full,visible);self._package_field_key=key
                q=self._package_field
                if getattr(artist,'_field_key',None)!=key:
                    values=np.full((2,2),self.space_data['base_spacing_um']) if artist._owner==-1 else np.where(np.isinf(q['display_spacing']),artist.norm.vmax,q['display_spacing'])
                    artist.set_data(values);artist.set_extent(q['extent']);artist._field_key=key
                    domain=self.case['board'].difference(q['domain']) if artist._owner==-1 else q['domain']
                    artist.set_clip_path(geometry_path(domain),self.ax.transData);artist._has_domain=not domain.is_empty
                artist.set_visible(active and artist._has_domain and any(k in visible for k in artist._source_layers))
                artist._static_preview=('space',stamp,artist._owner,key,artist.get_visible());continue
            transform=Affine2D()
            if self.shown_states is not None:
                center=self.rotation_centers()[artist._owner];state=self.shown_states[artist._owner]
                transform.rotate_around(*center,state[2]).translate(*state[:2])
            trans=transform+self.ax.transData;artist.set_transform(trans);artist.set_clip_path(artist._space_domain,trans)
            x0,y0,x1,y1=artist._source_bounds
            corners=transform.transform([[x0,y0],[x0,y1],[x1,y0],[x1,y1]])
            from .viewport import overlaps
            in_view=overlaps((*corners.min(axis=0),*corners.max(axis=0)),self.view_bounds)
            artist.set_visible(active and in_view and any(self.visible_boxes[k].isChecked() for k in artist._source_layers))
            artist._static_preview=('space',stamp,artist._owner,transform.get_matrix().tobytes(),artist.get_visible())
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
    def cancel_previews(self):
        for worker in self.preview_workers:worker.cancelled.set()
    def repair_lookup(self,signature,state,deform):
        """Adopt the cached post-patch result for one pose, or ask for a new one.

        Repair data is cached per pose, so a cache hit lets the fast display
        paths stay fast; a miss must fall through to the worker, otherwise the
        panel would keep showing the patches of a previously displayed pose.
        """
        if not self.repair_toggle.isChecked():
            self.repair_data=None;return True
        key=('repair',signature,np.asarray(state,float).tobytes(),self.view_settings.drc_tolerance_um,
             bool(self.view_settings.repair_landing_pad),bool(deform and self.result is not None),
             float(self.view_settings.repair_patch_width_um))
        self.repair_data=self.session_values.get(key)
        return self.repair_data is not None
    def request_preview(self,direct=False,settle=False):
        if not self.data:return
        self.stop_animation()
        self.preview_ticket+=1;ticket=self.preview_ticket;self._prepared_motion=None
        # Committed extreme poses finish into cache even if a later pose wins
        # the display ticket. Only transient mouse-drag refinements are stale.
        for running in self.preview_workers:
            if getattr(running,'transient',False):running.cancelled.set()
        state=self.manual_states if self.manual_states is not None else (self.extremes[self.active_preview]['states'].copy() if self.active_preview is not None and self.extremes else None)
        overlays=(self.probability_toggle.isChecked() or self.space_toggle.isChecked() or self.drc_toggle.isChecked() or self.width_toggle.isChecked()
                  or self.repair_toggle.isChecked() or self.reconstruction_toggle.isChecked() or (self.deformation_toggle.isChecked() and self.result is not None))
        shifted=self.shown_states is not None and np.any(self.shown_states)
        animate_back=bool(shifted and not direct and self.view_settings.animation_enabled and self.view_settings.animation_return_zero)
        if state is None and not overlays and not animate_back:
            self.preview_pending=None;self.present_frame(None);return
        try:p=self.params();roles=self.roles();case=self.case or build_case(self.data,roles,layer_order=self.ordered_layers())
        except ValueError as exc:self.error(exc);return
        origin=self.shown_states.copy() if self.shown_states is not None else np.zeros((len(case['dies']),3))
        show_frame=state is not None
        state=np.zeros_like(origin) if state is None else np.array(state,float,copy=True)
        state[state==0]=0.
        settings=self.view_settings
        signature=self.preview_identity(self.data,roles,self.ordered_layers(),self.motions(),p,self.result)
        deform=self.deformation_toggle.isChecked();drc=self.drc_toggle.isChecked()
        width=settings.width_key() if self.width_toggle.isChecked() else (False,)
        recon=settings.reconstruction_key() if self.reconstruction_toggle.isChecked() else (False,)
        expected=((bool(deform and self.result is not None),*settings.deformation_key()),(drc,settings.deformation_min_angle_deg,settings.drc_tolerance_um),width,recon)
        if show_frame:
            store=self.cache_group.frame_stores.get(signature)
            cached=store.get((expected,state.tobytes(),False)) if store is not None else None
        else:
            key=(signature,deform,settings.deformation_key(),drc,settings.numeric_key(),width,recon)
            cached=self.cache_group.nominal.get(key)
        ready=(not self.probability_toggle.isChecked() or self.probability_data is not None) and (not self.space_toggle.isChecked() or self.space_data is not None)
        global_ready=show_frame or not (deform and self.result and math.isfinite(p.target_nsigma)) or bool(cached and cached.get('global_deformation'))
        matches=[e for e in self.session_engines.values() if getattr(e,'signature',None)==signature]
        actual=next((e for e in matches if not getattr(e,'busy',False) and e.cache_key()==expected),next(iter(matches),None))
        if cached is not None and actual is not None and ready and global_ready and self.repair_lookup(signature,state,deform):
            self.engine=actual
            animate=settings.animation_enabled and not direct and not np.array_equal(origin,state)
            prepared=animate and ((not np.any(state) and animate_back) or (np.any(state) and (deform or width[0] or recon[0])))
            target=cached if show_frame or overlays else None
            if prepared:
                from .prepared_motion import motion_packet
                from types import SimpleNamespace
                view=SimpleNamespace(signature=signature,cache_key=lambda:expected,bound=actual.bound)
                _,_,duration,steps,requested,_,packet=motion_packet(view,origin,state,settings,max(np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim())),self.session_values)
                if packet and packet['complete']:
                    motion=dict(prepared=True,streamed=False,frames=packet['frames'],duration=duration,steps=steps,requested_steps=requested,cached=True,count=len(packet['frames']))
                    self._prepared_motion=motion;self.preview_pending=None;self.play_prepared_motion(target,motion);return
                self.statusBar().showMessage('目标状态已缓存，正在准备首次过渡动画。')
            else:
                self.preview_pending=None
                if not show_frame or getattr(actual,'busy',False) or actual.cache_key()!=expected:
                    self._interpolated_states=None;self.current_frame=target;self.plot_layout(preserve=True)
                    self.update_state_table(state);self.update_histograms();self.show_frame_status(target)
                else:self.present_frame(target)
                return
        self.preview_pending=(self.revision,self.view_settings,ticket,state.copy(),self.motions(),roles,p,self.result,case,self.geometry_cache,self.connections,self.ordered_layers(),show_frame,self.probability_toggle.isChecked(),self.space_toggle.isChecked())
        self.preview_pending+=(self.deformation_toggle.isChecked(),self.drc_toggle.isChecked(),direct,settle,origin,self.width_toggle.isChecked(),self.repair_toggle.isChecked(),self.reconstruction_toggle.isChecked())
        self.statusBar().showMessage('正在后台生成预演…保留已显示状态。');self.launch_preview()
    def launch_preview(self):
        if self._closing or self.motion_worker is not None or self.preview_pending is None:return
        request=self.preview_pending
        request_key=(self.preview_identity(self.data,request[5],request[11],request[4],request[6],request[7]),request[1].deformation_key(),request[1].numeric_key(),request[3].tobytes(),request[12:19],request[19].tobytes(),request[20:],request[1].animation_enabled,request[1].animation_return_zero,request[1].animation_strategy,request[1].animation_speed,request[1].animation_fps,request[1].animation_duration_ms)
        if any(getattr(w,'request_key',None)==request_key and not w.cancelled.is_set() for w in self.preview_workers):return
        from .scheduling import admission
        estimate=max(256,getattr(self.geometry_cache,'bytes',0)/1024**2*16)
        admitted=admission(len(self.preview_workers)+(1 if self.worker is not None else 0),estimate)
        if not admitted['start']:
            if not getattr(self,'_preview_retry',False):
                self._preview_retry=True
                def retry():
                    self._preview_retry=False;self.launch_preview()
                QtCore.QTimer.singleShot(400,retry)
            return
        revision,settings,ticket,states,motions,roles,params,result,case,cache,connections,order,show_frame,heat,space,deform,drc,direct,settle,origin,width_analysis,repair_view,reconstruction_view=self.preview_pending;self.preview_pending=None;data=self.data
        session_values=self.session_values;engines=self.session_engines;cache_group=self.cache_group
        start_states=origin.copy()
        view_span=max(np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim()))
        signature=self.preview_identity(data,roles,order,motions,params,result)
        # Mutable solver engines are leased exclusively. Their completed frame
        # store is shared, while working arrays and geometry searches are not.
        actual=next((e for e in list(engines.values()) if getattr(e,'signature',None)==signature and not getattr(e,'busy',False)),None)
        if actual is not None:actual.busy=True
        def solve(progress):
            nonlocal actual
            from .probability_overlay import probability_maps
            from .space_map import space_maps
            actual_cache=cache or GeometryCache(data);con=connections or Connections(data,roles,order,actual_cache.geometries)
            if actual is None:
                actual=PreviewEngine(actual_cache,case,roles,motions,params,result,con,cache_group=cache_group,cache_identity=signature)
                actual.busy=True;actual.signature=signature
                with cache_group.lock:engines[(signature,id(actual))]=actual
            actual.signature=signature
            actual.configure_deformation(deform,settings);actual.configure_drc(drc,settings);actual.configure_width(width_analysis,settings);actual.configure_reconstruction(reconstruction_view,settings)
            distribution=None;field=None
            if heat:distribution=session_values.get_or_compute(('probability',signature,settings.numeric_key(),bool(settings.probability_show_ranges and math.isinf(params.target_nsigma))),lambda:probability_maps(actual_cache,actual.bound,roles,params,masks=actual.contact.interface_masks,settings=settings,progress=progress))
            if space:field=session_values.get_or_compute(('space',signature,settings.space_resolution),lambda:space_maps(actual.contact,params,resolution=settings.space_resolution))
            prepared=None
            if space and field is not None and settings.space_scope=='package':
                from .space_map import package_map
                key=(id(field),states.tobytes() if show_frame else None,settings.space_resolution)
                prepared=(key,session_values.get_or_compute(('package',signature,key[1],settings.space_resolution),lambda:package_map(field,case['board'],actual.bound.centers,states,None,settings.space_resolution)))
            motion=None
            back=bool(not direct and settings.animation_enabled and settings.animation_return_zero and not np.any(states) and np.any(start_states))
            nominal_key=(signature,deform,settings.deformation_key(),drc,settings.numeric_key(),settings.width_key() if width_analysis else (False,),settings.reconstruction_key() if reconstruction_view else (False,))
            frame=None if show_frame else cache_group.nominal.get(nominal_key)
            if frame is None and (show_frame or drc or width_analysis or reconstruction_view or repair_view or back or (deform and result is not None)):
                frame=actual.frame(states,progress,quick=direct and not settle)
            if frame is not None and not show_frame and not frame.get('nominal_global'):
                frame=dict(frame,nominal_global=True)
                if deform and result is not None and math.isfinite(params.target_nsigma):
                    frame=actual.global_frame(frame,settings,progress)
                elif math.isfinite(params.target_nsigma):
                    frame['contact']=dict(frame['contact'],issues=(result or {}).get('issues',[]),intrusions=[])
                cache_group.nominal[nominal_key]=frame
            repair=None
            if repair_view:
                # A separate cached value, like the Space Map: the shared frame
                # cache key is unchanged and the DRC instance stays local.
                from .repair_patch import repair_frame
                deform_replacements=(frame or {}).get('deformation',{}).get('replacements',{}) if frame else {}
                repair_key=('repair',signature,states.tobytes(),settings.drc_tolerance_um,
                            bool(settings.repair_landing_pad),bool(deform and result is not None),
                            float(settings.repair_patch_width_um))
                repair=session_values.get_or_compute(repair_key,lambda:repair_frame(
                    actual.contact,states,settings,params,replacements=deform_replacements,progress=progress))
            if back or ((width_analysis or reconstruction_view or (deform and result is not None)) and settings.animation_enabled and not direct and np.any(states) and not np.array_equal(start_states,states)):
                from .prepared_motion import prepare_motion
                publish=(lambda intermediate:worker.publish((actual,intermediate,distribution,con,actual_cache,field))) if settings.animation_strategy=='stream' else None
                motion=prepare_motion(actual,start_states,states,settings,view_span,session_values,progress,publish,worker.cancelled)
            if direct:motion=dict(direct=True,settled=settle)
            if not show_frame and not (drc or width_analysis or repair_view or heat or space or reconstruction_view or (deform and result is not None)):frame=None
            return actual,frame,distribution,con,actual_cache,field,prepared,motion,repair
        def work(progress):
            from .scheduling import job_budget
            with job_budget(admitted['threads'],admitted['processes']):return solve(progress)
        worker=Worker(work,self);worker.interactive_refinement=bool(direct and settle);worker.transient=bool(direct);worker.request_key=request_key
        self.preview_workers.append(worker);self.preview_worker=worker;self.watch_worker(worker,'预演计算')
        self.log_bus.emit(admitted['reason']+f" · 活跃预演 {len(self.preview_workers)} · CPU {admitted['load'].get('cpu_percent')}% · 可用内存 {admitted['load']['available_memory_mb']} MiB")
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
            self.engine,frame,self.probability_data,self.connections,self.geometry_cache,self.space_data,self._prepared_package,self._prepared_motion,self.repair_data=value
            if ticket!=self.preview_ticket:return
            self.case=case;self.refresh_motion_table()
            if frame and frame.get('global_deformation') and self.result:
                self.result['adaptive_preview']=frame['global_deformation'];self.report_dirty=True
            if self._prepared_motion and (self._prepared_motion.get('streamed') or self._prepared_motion.get('direct')):
                self._interpolated_states=None;self.current_frame=frame;self.plot_layout(preserve=True);self.update_histograms();self.show_frame_status(frame);self.log_bus.emit('交互帧已更新' if self._prepared_motion.get('direct') else '型变流式帧完成：'+str(self._prepared_motion['count']))
            elif self._prepared_motion and self._prepared_motion.get('prepared'):
                self.play_prepared_motion(frame,self._prepared_motion)
            else:self.present_frame(frame)
            if self.current_frame is not None and not self.transition.timer.isActive():self.update_state_table(self.current_frame['states'])
        def rejected(message):
            if ticket!=self.preview_ticket:return
            self.statusBar().showMessage('预演未应用：'+message.split('Traceback')[0]);self.error(message)
        worker.progress.connect(lambda n,t:self.statusBar().showMessage(t) if ticket==self.preview_ticket else None)
        worker.done.connect(accepted);worker.failed.connect(rejected)
        def finished():
            if actual is not None:actual.busy=False
            if worker in self.preview_workers:self.preview_workers.remove(worker)
            self.preview_worker=self.preview_workers[-1] if self.preview_workers else None
            worker.deleteLater();self.launch_preview()
        worker.finished.connect(finished);worker.start()
    def update_histograms(self,clear=False):
        if not hasattr(self,'distribution_figure'):return
        self.histogram_data=None
        if clear or self.current_frame is None:
            self.chart_status.setText('选择极端情况后显示全部参与运动过孔的位移统计。');self.distribution_figure.clear();self.distribution_canvas.draw_idle();return
        seg=self.all_connector_segments;delta=seg[:,1]-seg[:,0];magnitude=np.linalg.norm(delta,axis=1);nonzero=magnitude>1e-9
        directions=(np.degrees(np.arctan2(delta[nonzero,1],delta[nonzero,0]))+360)%360
        bins=min(24,max(5,int(np.sqrt(len(magnitude)))))
        mag_counts,mag_edges=np.histogram(magnitude,bins=bins,range=(0,max(1e-6,float(magnitude.max())*1.001))) if len(magnitude) else (np.zeros(5,int),np.linspace(0,1,6))
        dir_counts,dir_edges=np.histogram(directions,bins=np.linspace(0,360,13))
        self.histogram_data=dict(magnitude=magnitude,direction=directions,magnitude_counts=mag_counts,direction_counts=dir_counts,magnitude_edges=mag_edges,direction_edges=dir_edges,zero_count=int((~nonzero).sum()))
        self.chart_status.setText(f'参与计算的运动过孔 {len(seg):,} 个；零位移 {(~nonzero).sum():,} 个（无方向）。方向：+X为0°，逆时针增加。')
        self.histogram_dirty=True
        if self.tabs.currentWidget() is self.analysis_page:self.render_histograms()
    def render_histograms(self):
        if not self.histogram_data or not self.histogram_dirty:return
        if not self._histogram_revealed and (self.active_preview is not None or self.manual_states is not None):
            self.analysis_split.setSizes([320,320,220]);self._histogram_revealed=True
        self.histogram_dirty=False;q=self.histogram_data
        mag_edges=q['magnitude_edges'];mag_counts=q['magnitude_counts'];dir_edges=q['direction_edges'];dir_counts=q['direction_counts']
        sizes=(len(mag_counts),len(dir_counts))
        if getattr(self,'_histogram_bar_sizes',None)!=sizes or not self.distribution_figure.axes:
            self.distribution_figure.clear();axes=self.distribution_figure.subplots(1,2)
            self._histogram_axes=axes;self._histogram_bar_sizes=sizes
            self._histogram_bars=[axes[0].bar(mag_edges[:-1],mag_counts,width=np.diff(mag_edges)*.92,align='edge',color='#abc7df',edgecolor='#718fa7',linewidth=.5),
                                  axes[1].bar(dir_edges[:-1],dir_counts,width=27,align='edge',color='#bdd6c8',edgecolor='#719783',linewidth=.5)]
            axes[0].set(xlabel='偏移幅值 / μm',ylabel='运动过孔数量',title='偏移数值分布')
            axes[1].set(xlabel='位移方向 / °',ylabel='运动过孔数量',title='偏移方向分布',xlim=(0,360),xticks=np.arange(0,361,60))
            from matplotlib.ticker import MaxNLocator
            for ax in axes:
                ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True);ax.yaxis.set_major_locator(MaxNLocator(integer=True))
                ax.tick_params(labelsize=8)
        axes=self._histogram_axes
        for bars,counts,edges in zip(self._histogram_bars,(mag_counts,dir_counts),(mag_edges,dir_edges)):
            for bar,height,left,width in zip(bars,counts,edges[:-1],np.diff(edges)):
                bar.set_height(height);bar.set_x(left);bar.set_width(width*.92)
        axes[0].set_xlim(0,max(1e-6,mag_edges[-1])*1.02)
        for ax,counts in zip(axes,(mag_counts,dir_counts)):ax.set_ylim(0,max(1,float(max(counts,default=0)))*1.15)
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
            key=self.assessment_identity(data,roles,order,p,settings)
            r=values.get_or_compute(key,lambda:evaluate(data,roles,p,progress=lambda n,t:progress(round(n*.92),t),layer_order=order,connections=connections,motions=motions,settings=settings,safe_range_cache=values));r=dict(r,motions=motions,view_settings=asdict(settings))
            catalog=RepresentativeExtremes(case,p);r['preview_count']=catalog.total
            from .extreme_validation import evaluate_states
            def check_extremes():
                validator=PreviewEngine(actual_cache,case,roles,motions,p,r,connections)
                try:return evaluate_states(validator,catalog,settings,progress)
                finally:validator.close()
            try:r['extreme_validation']=values.get_or_compute(('extreme-validation',key,settings.deformation_min_angle_deg,settings.drc_tolerance_um),check_extremes)
            except InterruptedError:raise
            except Exception:
                message=traceback.format_exc();self.log_bus.emit('极端状态完整校验未完成，未校验行不会标绿：'+message,'ERROR')
                r['extreme_validation']=[dict(safe=None,checked=False,reason=message) for _ in catalog]
            progress(100,'方案评估与极端状态校验完成')
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
        self.cache_group.last_assessment=(self.assessment_identity(self.data,self.roles(),self.ordered_layers(),self.params(),self.view_settings),r)
        self.result=r;self.dirty=False;self.evaluation_motions=r.get('motions',self.motions());self.manual_states=None
        if self.case is None:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        self.extremes=catalog or RepresentativeExtremes(self.case,self.params());self.active_preview=None;self.owner_cache=None;self.engine=None;self.current_frame=None;self.report_dirty=True
        self.show_extreme_page(0,redraw=False)
        first=next((s['scheme'] for s in r['schemes'] if any(q['scheme']==s['scheme'] for q in r.get('issues',[]))),r['recommendation'].get('scheme') or 1)
        self.issue_scheme.blockSignals(True);self.issue_scheme.setCurrentIndex(first-1);self.issue_scheme.blockSignals(False);self.fill_issues()
        rec=r['recommendation']
        self.scheme_table.setRowCount(3)
        for i,s in enumerate(r['schemes']):
            for j,v in enumerate([f"{s['scheme']} {s['name']}",s['status'],fmt(s['gamma']),'; '.join(s['reasons'])]):self.scheme_table.setItem(i,j,W.QTableWidgetItem(str(v)))
            if s['scheme']==rec.get('scheme'):
                for j in range(self.scheme_table.columnCount()):self.scheme_table.item(i,j).setBackground(QtGui.QColor('#fff0d9'))
        self.scheme_table.setColumnWidth(0,230);self.scheme_table.setColumnWidth(1,120);self.scheme_table.setColumnWidth(2,65);self.scheme_table.horizontalHeader().setStretchLastSection(True);self.scheme_table.resizeRowsToContents();self.scheme_table.setMinimumHeight(64)
        self.scheme_figure.clear();ax=self.scheme_figure.add_subplot(111);values=[s['gamma'] for s in r['schemes']];finite=[x for x in values if math.isfinite(x)]
        cap=max(1.4,min(8,max(finite or [1])*1.2));display=[min(x,cap) for x in values];ax.barh([0,1,2],display,color=['#a8c5de','#accfbd','#d6bedf'],height=.48)
        ax.axvline(1,color='#708395',ls='--',lw=1);ax.set_yticks([0,1,2],['方案1','方案2','方案3']);ax.invert_yaxis();ax.set_xlim(0,cap*1.15)
        for i,s in enumerate(r['schemes']):ax.text(display[i]+cap*.02,i,fmt(s['gamma']),va='center',color='#42536b')
        ax.set(xlabel='空间需求 / 可用空间 U（≤1为初筛候选）',title='三方案比较');ax.spines[['top','right']].set_visible(False);self.scheme_canvas.draw_idle()
        ranges=r.get('safe_ranges',{});safe_rows=ranges.get('rows',[])
        self.safe_range_canvas.setMinimumHeight(max(330,195+len(safe_rows)*89))
        self.safe_range_plot_data=draw_safe_ranges(self.safe_range_figure,ranges,r.get('model_yield'));self.safe_range_canvas.draw_idle()
        from .safe_range_plot import format_yield
        yr=r.get('model_yield',{});yr_text=(f"全输入无接触模型概率：{format_yield(yr['estimate'])}；{yr.get('interval_kind','区间')} [{yr['lower']:.6%}, {yr['upper']:.6%}]\n"+yr['scope']) if yr else ''
        self.safe_range_canvas.setToolTip(yr_text+'\n同一横轴以各DIE各轴的输入单边幅度归一化；0表示输入均值。右侧为实际安全端点，X/Y单位μm、O单位°；灰线是输入全范围。\n'+ranges.get('guarantee',''))
        if not preserve:

            if not getattr(self,'_analysis_initialized',False):
                self.analysis_split.setSizes([290,max(320,105+len(safe_rows)*89),0]);self._analysis_initialized=True
            self._histogram_revealed=False
        self.die_table.setRowCount(len(r['dies']))
        for i,d in enumerate(r['dies']):
            vals=[d['die'],d['width'],d['height'],d['pin_count'],d['output_count'],d['corner_bound_um'],d['screening']['u1'],d['screening']['u2'],d['screening']['u3']]
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
        if self.tabs.currentIndex()==0 and self.data:
            self.resize_plot();self.plot_layout(preserve=True)
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
        self.refresh_extreme_colors()
        if redraw:self.plot_layout(preserve=True)
    def refresh_extreme_colors(self,frame=None):
        from .extreme_validation import frame_status
        baseline=(self.result or {}).get('extreme_validation',[])
        if (self.result or {}).get('view_settings',{}).get('drc_tolerance_um',0)!=self.view_settings.drc_tolerance_um:baseline=[]
        for row,(index,item) in enumerate(self.page_items,1):
            status=baseline[index] if index<len(baseline) and not self.dirty else {}
            if frame is not None and not frame.get('nominal_global') and np.array_equal(frame['states'],item['states']):
                current=frame_status(frame)
                if current.get('checked') or current.get('safe') is False:status=current
                elif self.deformation_toggle.isChecked() or self.dirty:status=current
            safe=status.get('safe') is True
            brush=QtGui.QBrush(QtGui.QColor('#e3f2e5')) if safe else QtGui.QBrush()
            for col in range(self.extreme_table.columnCount()):
                cell=self.extreme_table.item(row,col)
                if cell:
                    cell.setBackground(brush)
                    detail='已完成接口接触、机械净距/禁布区及固定RDL DRC检查；无接触或失效。' if safe else '存在已检查的问题。' if status.get('safe') is False else '完整检查尚未完成；不标记为通过。'
                    cell.setToolTip(cell.toolTip().split('\n校验：')[0]+'\n校验：'+detail)
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
    def project_settings(self):
        return dict(schema_version=5,app_version=__version__,sha256=self.data.sha256,top_cell=self.data.top_name,
                    roles=self.roles(),parameters=asdict(self.params()),settings=asdict(self.view_settings),node_edits=list(self.data.node_edits),
                    overlays={name:getattr(self,name+'_toggle').isChecked() for name in OVERLAY_DEFAULTS},layer_styles=self.layer_styles,
                    visibility={k:w.isChecked() for k,w in self.visible_boxes.items()},motions=self.motions(),layer_order=self.ordered_layers())

    def show_properties(self):
        if not self.data:return
        if getattr(self,'_queued_node_edit',None):
            self.statusBar().showMessage('节点编辑已排队，将在当前计算结束后应用。');return
        from .shape_properties import ShapeProperties
        from .qt_compat import run_dialog,ACCEPTED
        self.stop_animation()
        dialog=ShapeProperties(self)
        if run_dialog(dialog)==ACCEPTED and dialog.data is self.data and dialog.changes!=self.data.node_edits:
            self.apply_node_edits(dialog.changes)

    def apply_node_edits(self,edits):
        if self.worker is not None:
            self._queued_node_edit=(self.data,edits);self.log_bus.emit('节点编辑已排队，当前任务完成后重建几何。');return
        original=self.data;project=self.project_settings();project['node_edits']=edits
        pose=self.shown_states.copy() if self.shown_states is not None and np.any(self.shown_states) else None
        was_assessed=self.result is not None;valid_case=self.case is not None;view=(self.ax.get_xlim(),self.ax.get_ylim())
        selected=list(self.selection.selected)
        def prepare(progress):
            progress(5,'校验工程节点');updated=edited_snapshot(original,edits)
            progress(35,'重建几何和金属连接');cache=GeometryCache(updated)
            try:case=build_case(updated,project['roles'],layer_order=project['layer_order'])
            except ValueError:
                if valid_case:raise
                case=None
            return updated,cache,case
        def accepted(product):
            if self.data is not original:return
            updated,cache,case=product
            updated.geometry_cache=cache;updated.prepared_case=case;updated.prepared_roles=project['roles'];updated.prepared_auto=automatic_parameters(updated,project['roles'])
            self.set_data(updated,project,project_path=self.project_path)
            self.ax.set_xlim(view[0]);self.ax.set_ylim(view[1])
            self.manual_states=pose;self.selection.selected=selected
            self.plot_layout(preserve=True)
            if pose is not None:self.request_preview()
            if was_assessed:
                self._evaluation_requested=True;self._coverage_pending=True;self.coverage_timer.start()
            self.log_bus.emit('工程节点已更新。几何、连接和缓存按新版本计算；保存工程可保留本次编辑。')
        self.start_worker(prepare,accepted)

    def write_project(self,path):
        import os
        path=Path(path).resolve();gds=Path(self.data.path).resolve()
        source=os.path.relpath(gds,path.parent) if gds.is_relative_to(installation_root()) else str(gds)
        project=dict(self.project_settings(),gds_path=source)
        from .core import clean_json
        atomic_save(path,clean_json(project));self.project_path=path
        from .session_cache import logical_fingerprint
        cache_id=logical_fingerprint((str(path),self.data.sha256,self.data.top_name))
        if cache_id!=self.cache_project_key:
            # First save names the same engineering session; keep solved poses.
            previous=self.cache_registry.retire(cache_id)
            if previous is not None:self.retired_sessions.append(previous)
            self.cache_registry.rekey(self.cache_project_key,cache_id);self.cache_project_key=cache_id
            self.cleanup_retired_sessions();self.refresh_cache_status()
    def load_project(self,path):
        try:
            path=Path(path).resolve();project=normalize_project(json.loads(path.read_text(encoding='utf-8')));gds=Path(project['gds_path'])
            if not gds.is_absolute():gds=path.parent/gds
            self.load_gds(gds,project.get('top_cell'),project,project_path=path)
        except Exception as exc:self.refresh_jobs();self.error(exc)
    def closeEvent(self,event):
        self.stop_animation()
        if hasattr(self,'mouse_die'):self.mouse_die.pending=False;self.mouse_die.full_pending=False;self.mouse_die.timer.stop();self.mouse_die.idle.stop()
        running=[w for w in [self.worker,self.motion_worker,self.hardware_worker,self.cache_clear_worker,*self.preview_workers] if w is not None and w.isRunning()]
        if running:
            self._closing=True
            self.preview_pending=None;self.preview_ticket+=1;self.motion_pending=None;self.motion_generation+=1;self.coverage_timer.stop();self._coverage_pending=False
            for w in running:w.cancelled.set()
            event.ignore();return
        self._closing=True;self._queued_node_edit=None
        for timer in (self.cache_status_timer,self.viewport_timer,self.coverage_timer,self.issue_navigation):timer.stop()
        W.QApplication.instance().removeEventFilter(self.wheel_filter)
        paths=self.job_watcher.directories()+self.job_watcher.files()
        if paths:self.job_watcher.removePaths(paths)
        self.cache_registry.close();self.cleanup_retired_sessions()
        self.runtime_capture.close();event.accept()


def launch(project=None):
    app=W.QApplication.instance() or W.QApplication([]);app.setApplicationName('Die Shift Analyze')
    window=MainWindow();window.show()
    if project:QtCore.QTimer.singleShot(0,lambda:window.load_project(project))
    code=app.exec() if hasattr(app,'exec') else app.exec_()
    QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete)
    return code
