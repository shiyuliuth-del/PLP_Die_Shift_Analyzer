from __future__ import annotations

import json
import math
import threading
import traceback
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from .qt_compat import QtCore, QtGui, QtWidgets as W, Signal, HORIZONTAL, VERTICAL, Qt, TOOLTIP
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
from .layer_controls import LayerCheckBox,LayerDrag,LayerStyleDialog
from .layer_roles import NON_PARTICIPATING
from .qt_compat import run_dialog,ACCEPTED
from matplotlib.colors import LinearSegmentedColormap,Normalize

from shapely.geometry import Polygon

ROOT=Path(__file__).resolve().parent.parent

SPECS={
 'panel_layers':('固定RDL层数',0,32,0), 'wafer_layers':('运动RDL层数',0,32,0),
 'line_width_um':('固定线宽 / μm',.01,10000,3),'line_space_um':('固定线距 / μm',.01,10000,3),
 'wafer_line_width_um':('运动线宽 / μm',.01,10000,3),'wafer_line_space_um':('运动线距 / μm',.01,10000,3),
 'minimum_edge_um':('最短边长 / μm',0,10000,6),'minimum_area_um2':('最小图形面积 / μm²',0,1e12,6),'package_clearance_um':('图形距封装边框 / μm',0,10000,3),'via_diameter_um':('连接孔直径 / μm',.01,10000,3),'annulus_um':('固定RDL包围量 / μm',0,10000,3),'moving_annulus_um':('运动RDL包围量 / μm',0,10000,3),
   'min_die_gap_um':('芯片边框最小间距 / μm',0,10000,3),
 'min_package_clearance_um':('芯片到封装最小间距 / μm',0,10000,3),
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
        super().__init__(parent);self.fn=fn;self.cancelled=threading.Event();self.report_progress=True;self._last_progress=0.;self._partial_lock=threading.Lock();self._partial_value=None;self._partial_queued=False
    def update(self,n,text):
        if self.cancelled.is_set():raise InterruptedError('用户取消')
        import time
        now=time.monotonic()
        if not self._last_progress or now-self._last_progress>=.1:
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
            from .resources import cancellation_scope
            with cancellation_scope(self.cancelled):result=self.fn(self.update if self.report_progress else None)
            if not self.cancelled.is_set():self.done.emit(result)
        except InterruptedError:pass
        except ValueError as exc:
            if not self.cancelled.is_set():self.failed.emit(str(exc)+'\n'+traceback.format_exc())
        except Exception as exc:
            if not self.cancelled.is_set():self.failed.emit(str(exc)+'\n'+traceback.format_exc())


def label(text):
    x=W.QLabel(text);x.setWordWrap(True);return x


def table(headers,widget=W.QTableWidget):
    x=widget(0,len(headers));x.setHorizontalHeaderLabels(headers)
    x.setAlternatingRowColors(True);x.verticalHeader().hide()
    x.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers if hasattr(W.QAbstractItemView,'EditTrigger') else W.QAbstractItemView.NoEditTriggers)
    x.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows if hasattr(W.QAbstractItemView,'SelectionBehavior') else W.QAbstractItemView.SelectRows)
    return x


COMPACT_PREVIEW_STYLE='''
QWidget {font-size: 11px;}
QHeaderView::section {padding: 2px 3px;}
QPushButton, QComboBox, QLineEdit, QDoubleSpinBox {padding: 1px 4px; min-height: 16px;}
'''


class ExtremeRowDelegate(W.QStyledItemDelegate):
    """Keep cache colours visible even while Qt's current row is selected."""
    def paint(self,painter,option,index):
        selected=getattr(W.QStyle,'StateFlag',W.QStyle).State_Selected
        if not option.state & selected:
            super().paint(painter,option,index);return
        flags=getattr(W.QStyle,'StateFlag',W.QStyle)
        neutral=W.QStyleOptionViewItem(option);neutral.state &= ~(selected|flags.State_HasFocus)
        super().paint(painter,neutral,index)


class ExtremeTable(W.QTableWidget):
    """One continuous border for the current row, over the native cell grid."""
    def paintEvent(self,event):
        super().paintEvent(event)
        row=self.currentRow()
        if row<0 or not self.selectionModel().isSelected(self.currentIndex()):return
        cols=[i for i in range(self.columnCount()) if not self.isColumnHidden(i)]
        if not cols:return
        left=max(0,min(self.columnViewportPosition(i) for i in cols))
        right=min(self.viewport().width(),max(self.columnViewportPosition(i)+self.columnWidth(i) for i in cols))
        y=self.rowViewportPosition(row)
        rect=QtCore.QRectF(left+1,y+1,right-left-2,self.rowHeight(row)-2)
        if not rect.isValid() or not rect.intersects(QtCore.QRectF(event.rect())):return
        painter=QtGui.QPainter(self.viewport())
        painter.setPen(QtGui.QPen(QtGui.QColor('#6185a4'),1.5))
        painter.drawRect(rect);painter.end()


from .gds_shapes import edited_snapshot
from .project import normalize_project, project_overlays, OVERLAY_DEFAULTS
from .evaluation_scope import SCOPE_NAMES
from .width_display import WidthDisplayEditor,tint,low_ink
from .preview import default_motion,extreme_cases,layer_owners,transformed_layers,PREVIEW_SCOPE
from .navigation import Navigation
from .layer_style import layer_style
from .preview import rigid_transform
from .fast_preview import GeometryCache,PreviewEngine
from .stack import stack_order,resolve_stages,Connections,PHYSICAL
from .statistics import ExtremeCatalog
from .representatives import RepresentativeExtremes
from .jobs import installation_root,list_jobs,job_path,atomic_save
from .contact_ranges import RANGES,ContactRanges
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
    def __init__(self,jobs_dir=None,*,measured=False):
        super().__init__();self.setWindowTitle(f'Die Shift Analyze {__version__}')
        from .app_icon import window_icon
        self.setWindowIcon(window_icon())
        self.setAttribute(getattr(Qt,'WidgetAttribute',Qt).WA_DeleteOnClose)
        self.jobs_dir=Path(jobs_dir) if jobs_dir is not None else installation_root()/'Jobs'
        self.jobs_dir.mkdir(parents=True,exist_ok=True)
        self.wheel_filter=NoWheelValues(self);W.QApplication.instance().installEventFilter(self.wheel_filter)
        self.resize(1540,1000);self.setMinimumSize(1120,740);self.setStyleSheet(STYLE)
        self.remote_locked=False;self.data=None;self.result=None;self.case=None;self.worker=None;self.project_path=None
        from .runtime_log import LogBus,RuntimeCapture
        self.requested_measured=bool(measured)
        self.log_bus=LogBus(self.jobs_dir.parent/'logs'/'runtime.log',enabled=not measured)
        self.runtime_capture=RuntimeCapture(self.log_bus);self._tasks={};self._closing=False
        self.per_die_values=[];self.per_die_pivots=[];self.reference_nsigma=6.;self._per_die_initialized=False;self.hardware_worker=None
        from .session_cache import ProjectCacheRegistry
        self.cache_registry=ProjectCacheRegistry();self.cache_project_key="empty";self.cache_group=self.cache_registry.get("empty")
        from .session_cache import EphemeralCache
        self.space_motion_fields=EphemeralCache()
        self.session_values=self.cache_group.values;self.session_engines=self.cache_group.engines;self.retired_sessions=[];self.parked_groups=[];self.cache_clear_worker=None
        self.fields={};self.role_boxes={};self.stage_hints={};self.visible_boxes={};self.selectable_boxes={}
        self.loading=False;self.revision=0;self.extremes=None;self.active_preview=None;self.owner_cache=None;self.auto_values={}
        self.preview_page=0;self._initial_extreme_page=0;self.page_items=[];self._extreme_paint={};self.state_result=None;self.issue_artists=[];self.shift_artist=None
        self.rendered_layers={};self.layer_artists={};self._layer_keys=[];self.geometry_cache=None;self.connections=None
        self.preview_worker=None;self.preview_workers=[];self.preview_pending=None;self.preview_ticket=0;self.engine=None;self.current_frame=None;self.shown_states=None
        self.die_labels=[];self._default_view=None;self._artist_frame=None;self._reordering=False
        self.space_data=None;self.space_artists=[];self.space_stamp=None;self.space_colorbar=None;self._resizing=False
        self._issue_table_stamp=None;self.dirty=False;self.manual_states=None;self.evaluation_motions={};self.probability_data=None;self.probability_artists=[];self.probability_stamp=None;self.legend=None;self.zone_artists=[];self.invalid_artists=[]
        from .dxf_settings import DxfSettings
        self.dxf_settings=DxfSettings();self.ignored_width_lines=frozenset();self.dxf_directory='';self.apd_options={};self.view_settings=ViewSettings(reconstruction_auto=False);self.setting_fields={};self.layer_styles={};self.histogram_dirty=False
        self.coverage_timer=QtCore.QTimer(self);self.coverage_timer.setSingleShot(True);self.coverage_timer.setInterval(180);self.coverage_timer.timeout.connect(self.refresh_coverage)
        self._coverage_pending=False;self._evaluation_requested=False;self._motion_intent=None;self.motion_worker=None;self.motion_pending=None;self.motion_generation=0;self.replacement_artists={};self._plotting=False;self.viewport_index=None;self._viewport_stamp=None
        self.issue_navigation=QtCore.QTimer(self);self.issue_navigation.setSingleShot(True);self.issue_navigation.setInterval(0);self.issue_navigation.timeout.connect(lambda:self.locate_issue(self.issue_table.currentRow(),0))
        self.viewport_timer=QtCore.QTimer(self);self.viewport_timer.setSingleShot(True);self.viewport_timer.setInterval(16);self.viewport_timer.timeout.connect(self.viewport_changed)
        self.transition=Transition(self);self._animation_cleanup=None;self._legend_stamp=None
        self._build()
        from .analysis_ui import ResultAnalysis
        self.analysis=ResultAnalysis(self)
        shortcut=QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut
        self.properties_shortcut=shortcut(QtGui.QKeySequence('Q'),self.canvas)
        self.properties_shortcut.setContext(getattr(Qt,'ShortcutContext',Qt).WidgetWithChildrenShortcut)
        self.properties_shortcut.activated.connect(self.show_properties)
        self.selection=GeometrySelection(self);self.escape_shortcut=(QtGui.QShortcut if hasattr(QtGui,'QShortcut') else W.QShortcut)(QtGui.QKeySequence('Escape'),self);self.escape_shortcut.activated.connect(self.escape_action);self.scene_renderer=SceneRenderer(self.canvas,self.ax);self.refresh_jobs()
        from .edit_history import EditHistory
        self.edit_history=EditHistory(self);self.pending_deleted_shapes={};self.pending_restored_shapes={}
        self.ignored_drc={}
        self._staged_deletions=None;self.pending_layout_ops=[];self._staged_restored_edits=None
        self._staged_node_edits=None;self.pending_node_shapes={}
        self.edit_shortcuts=[]
        for key,fn,parent in (('Delete',self.delete_selected_shapes,self.canvas),('U',self.undo_edit,self),
                              ('Shift+U',self.redo_edit,self),('Ctrl+U',self.redo_edit,self)):
            action=shortcut(QtGui.QKeySequence(key),parent)
            action.setContext(getattr(Qt,'ShortcutContext',Qt).WidgetWithChildrenShortcut)
            action.activated.connect(fn);self.edit_shortcuts.append(action)
        for name in ('button_press_event','scroll_event'):self.canvas.mpl_connect(name,lambda e:self.stop_animation())
        from .mouse_die import MouseDieMode
        self.mouse_die=MouseDieMode(self)
        from .width_overlay import WidthOverlay
        self.width_overlay=WidthOverlay(self)
        from .drc_overlay import DRCOverlay
        self.drc_overlay=DRCOverlay(self)
        from .reconstruction_overlay import ReconstructionOverlay, CutDrag, RegionSelect
        self.reconstruction_overlay=ReconstructionOverlay(self)
        self.cut_drag=CutDrag(self)
        self.region_select=RegionSelect(self)
        from .ruler import Rulers
        self.rulers=Rulers(self)
        from .reconstruction_status import ReconstructionYieldDisplay,ConstraintFeedback
        self.reconstruction_yield=ReconstructionYieldDisplay(self);self.constraint_feedback=ConstraintFeedback(self)
        self.canvas.mpl_connect('key_press_event',lambda e:self.exit_region_mode() if str(e.key).lower()=='m' else None)
        self.job_watcher=QtCore.QFileSystemWatcher([str(self.jobs_dir)],self);self.job_watcher.directoryChanged.connect(self.refresh_jobs)
        self.cache_status_timer=QtCore.QTimer(self);self.cache_status_timer.setInterval(500);self.cache_status_timer.timeout.connect(self.refresh_cache_status);self.cache_status_timer.start()
        QtCore.QTimer.singleShot(0,self.discover_hardware)
        self.statusBar().showMessage('打开GDS/OAS → 指定图层 → 设置偏移/工艺 → 方案评估 → 预演与结果分析')
    def _page(self,title):
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);page=W.QWidget();lay=W.QVBoxLayout(page)
        lay.setContentsMargins(10,10,10,10);lay.setSpacing(10);scroll.setWidget(page);self.controls.addTab(scroll,title);return lay
    def _group(self,parent,title,keys,collapsed=False):
        if collapsed:
            header,box,form=self.collapsible(parent,title,self.process_sections)
        else:
            box=W.QGroupBox(title);form=W.QFormLayout(box);parent.addWidget(box)
        form.setVerticalSpacing(7)
        for key in keys:self.parameter_field(form,key)
        return form

    def parameter_field(self,form,key):
        name,low,high,decimals=SPECS[key]
        spin=W.QSpinBox() if not decimals else W.QDoubleSpinBox()
        spin.setRange(low,high)
        if decimals:spin.setDecimals(decimals)
        spin.setValue(getattr(Parameters(),key));spin.setKeyboardTracking(False)
        spin.setToolTip(name);spin.valueChanged.connect(self.area_changed if key in ('minimum_area_um2','minimum_edge_um') else self.invalidate)
        form.addRow(name,spin);self.fields[key]=spin

    def collapsible(self,parent,title,registry):
        header=W.QToolButton();header.setText(title);header.setCheckable(True)
        header.setToolButtonStyle(getattr(Qt,'ToolButtonStyle',Qt).ToolButtonTextBesideIcon)
        header.setArrowType(getattr(Qt,'ArrowType',Qt).RightArrow)
        policy=getattr(W.QSizePolicy,'Policy',W.QSizePolicy);header.setSizePolicy(policy.Expanding,policy.Fixed)
        header.setStyleSheet('QToolButton {text-align:left; padding:5px; background:white; border:1px solid #dae1eb; border-radius:4px; font-weight:bold;}')
        body=W.QWidget();form=W.QFormLayout(body);body.hide()
        def expand(opened):
            body.setVisible(opened);header.setArrowType(getattr(Qt,'ArrowType',Qt).DownArrow if opened else getattr(Qt,'ArrowType',Qt).RightArrow)
        header.toggled.connect(expand);parent.addWidget(header);parent.addWidget(body);registry[title]=header
        return header,body,form
    def _build(self):
        central=W.QWidget();root=W.QVBoxLayout(central);self.setCentralWidget(central)
        root.setContentsMargins(6,4,6,4);root.setSpacing(4)
        self.header_widget=W.QWidget();header=W.QHBoxLayout(self.header_widget);header.setContentsMargins(0,0,0,0);header.setSpacing(4)
        self.progress=W.QProgressBar();self.progress.setMinimumWidth(90);self.progress.setMaximumWidth(200);self.progress.setMaximumHeight(23);self.progress.setTextVisible(True)
        policy=self.progress.sizePolicy();policy.setRetainSizeWhenHidden(True);self.progress.setSizePolicy(policy);header.addWidget(self.progress,1);self.progress.hide()
        from .task_control import TaskControl
        self.task_control=TaskControl(self,header)
        self.job_combo=W.QComboBox();self.job_combo.setMinimumWidth(100);self.job_combo.setMaximumWidth(220)
        self.job_combo.setToolTip('Jobs工程；空白项表示未命名的新工程');self.job_combo.currentIndexChanged.connect(self.job_selected);header.addWidget(self.job_combo)
        self.save_button=W.QPushButton('保存工程');self.save_button.clicked.connect(self.save_project);header.addWidget(self.save_button)
        self.runtime_toggle=W.QCheckBox('运行信息');header.addWidget(self.runtime_toggle)
        for widget in (self.progress,self.task_control.button,self.job_combo,self.save_button,self.runtime_toggle):
            widget.setFixedHeight(24);widget.setStyleSheet(widget.styleSheet()+'padding:1px 3px;font-size:11px;')
        split=W.QSplitter(HORIZONTAL);root.addWidget(split,1)
        left=W.QWidget();left.setMinimumWidth(410);left.setMaximumWidth(530);lay=W.QVBoxLayout(left);lay.setContentsMargins(0,0,0,0)
        self.controls=W.QTabWidget();lay.addWidget(self.controls,1)
        page=self._page('偏移')
        self.gds_label=W.QLineEdit();self.gds_label.setReadOnly(True);self.gds_label.setPlaceholderText('尚未打开GDS/OAS');self.gds_label.setFixedHeight(25);page.addWidget(self.gds_label)
        row=W.QHBoxLayout();row.addWidget(W.QLabel('顶层Cell'));self.top=W.QComboBox();self.top.currentTextChanged.connect(self.top_changed);row.addWidget(self.top,1);page.addLayout(row)
        self.open_gds_button=W.QPushButton('打开GDS/OAS');self.open_gds_button.setStyleSheet('QPushButton {background:#e5f2ee;color:#245e52;border:1px solid #a5c9bd;border-radius:4px;padding:5px;} QPushButton:hover {background:#d3e9e1;}');self.open_gds_button.clicked.connect(self.open_gds);page.addWidget(self.open_gds_button)
        self.layer_split=W.QSplitter(VERTICAL);self.layer_split.setChildrenCollapsible(False);page.addWidget(self.layer_split)
        self.layer_table=table(['显示','选中','层 / 类型','用途','补偿']);self.layer_table.setMinimumHeight(110);self.layer_split.addWidget(self.layer_table)
        self.compensation_boxes={}
        below=W.QWidget();below_layout=W.QVBoxLayout(below);below_layout.setContentsMargins(0,0,0,0);self.layer_split.addWidget(below)
        page=below_layout;self.layer_split.setSizes([300,300])
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
        page.addStretch()
        page=self._page('工艺');self.process_layout=page;self.process_sections={}
        self._group(page,'线宽线距',['panel_layers','wafer_layers','line_width_um','line_space_um','wafer_line_width_um','wafer_line_space_um','package_clearance_um'],collapsed=True)
        self._group(page,'过孔包围量',['moving_annulus_um','annulus_um'],collapsed=True)
        self._group(page,'芯片距离',['min_die_gap_um','min_package_clearance_um'],collapsed=True)
        self.fixed_assignment='all'
        page.addStretch()
        self.build_settings(self._page('设置'))
        output=self._page('输出')
        from .layout_export import build_controls
        def section(title):
            box=W.QGroupBox(title);layout=W.QVBoxLayout(box);layout.setSpacing(4);output.addWidget(box);return layout
        build_controls(self,section('GDS/OAS'))
        dxf=section('DXF')
        self.dxf_button=W.QPushButton('DXF输出');dxf.addWidget(self.dxf_button)
        regions_row=W.QHBoxLayout()
        self.regions_dxf_button=W.QPushButton('动静区输出');regions_row.addWidget(self.regions_dxf_button)
        self.regions_import_button=W.QPushButton('动静区输入');regions_row.addWidget(self.regions_import_button)
        dxf.addLayout(regions_row)
        output.setSpacing(4)
        from .dxf_controls import build as build_dxf_controls
        build_dxf_controls(self,dxf)
        from .measured_ui import MeasuredOutput
        self.measured_output=MeasuredOutput(self,section('实测输出'));output.addStretch()
        self.dxf_button.setToolTip('按下方参数展开顶层并输出当前完成的几何；默认合并全部图层，不输出风险标记或恢复圆弧。')
        from .export_ui import export_dxf,import_regions
        self.dxf_button.clicked.connect(lambda:export_dxf(self))
        self.regions_dxf_button.clicked.connect(lambda:export_dxf(self,regions=True))
        self.regions_import_button.clicked.connect(lambda:import_regions(self))
        self.run_button=W.QPushButton('方案评估');self.run_button.setObjectName('Primary');self.run_button.clicked.connect(lambda:self.run_evaluation());lay.addWidget(self.run_button);split.addWidget(left)
        self.tabs=W.QTabWidget();self.tabs.setCornerWidget(self.header_widget);split.addWidget(self.tabs);split.setSizes([445,1095])
        self.figure=Figure(figsize=(9,5));self.figure.subplots_adjust(left=.043,right=.993,bottom=.095,top=.99);self.canvas=FigureCanvasQTAgg(self.figure);self.ax=self.figure.add_subplot(111)
        page=W.QWidget();v=W.QVBoxLayout(page);v.setContentsMargins(0,0,0,0);self.toolbar=NavigationToolbar2QT(self.canvas,self)
        from .width_yield_display import WidthYieldDisplay
        self.width_yield=WidthYieldDisplay(self)
        from .plot_header import PlotHeader
        self.plot_header=PlotHeader(self.toolbar,self.width_yield.label);v.addWidget(self.plot_header)
        right_split=W.QSplitter(VERTICAL);right_split.addWidget(self.canvas);v.addWidget(right_split,1)
        bottom=W.QWidget();bv=W.QVBoxLayout(bottom);bv.setContentsMargins(5,3,5,3)
        self.preview_tabs=W.QTabWidget();bv.addWidget(self.preview_tabs)
        from .extreme_footprints import FootprintOverlay
        self.footprint_overlay=FootprintOverlay(self);self.footprint_data=None
        self.footprint_toggle=W.QCheckBox('极端足迹')
        self.footprint_toggle.setToolTip('热图表示过孔实体覆盖概率，浅色虚线表示离散极端足迹；二者共用显示开关。∞仅绘当前位置实体。')
        # Both layers share the footprint control and one refresh request.
        self.probability_toggle=self.footprint_toggle  # one saved control for cloud and discrete outline
        corner=W.QWidget();corner_layout=W.QHBoxLayout(corner);corner_layout.setContentsMargins(4,0,4,0)
        corner_layout.addWidget(self.footprint_toggle)
        self.space_toggle=W.QCheckBox('避让空间');self.space_toggle.setToolTip('整面所需线距 = 固定线距 + 目标σ下的保守偏移补偿量（μm）；随接口过孔图层显示/隐藏')
        corner_layout.addWidget(self.space_toggle)
        self._width_grid_summary=None;self._problem_tag=None
        self.filter_job=None;self._coupled_key=None;self._coupled_data=None;self._coupled_error=False
        self.width_toggle=W.QCheckBox('宽度分析');self.width_toggle.setToolTip('按实际线路穿越数量检查局部通道容量：矩形颜色显示宽度余量，轮廓高亮风险线路；不代表全局可布通证明。');corner_layout.addWidget(self.width_toggle);self.width_toggle.toggled.connect(self.width_changed)
        self.reconstruction_toggle=W.QCheckBox('重构预演');self.reconstruction_toggle.setToolTip('沿原始走线划分运动侧、固定侧和动区；所有偏移状态共用端点。拖动蓝色边界可分别调整，动区轮廓可输出DXF。');corner_layout.addWidget(self.reconstruction_toggle);self.reconstruction_toggle.toggled.connect(self.reconstruction_changed)
        self.preview_tabs.setCornerWidget(corner)
        self.probability_toggle.toggled.connect(self.footprint_changed);self.space_toggle.toggled.connect(self.space_changed)
        extreme_page=W.QWidget();ep=W.QVBoxLayout(extreme_page);ep.setContentsMargins(0,0,0,0)
        self.page_summary=W.QLabel('尚未评估')
        policy=getattr(W.QSizePolicy,'Policy',W.QSizePolicy);self.page_summary.setSizePolicy(policy.Ignored,policy.Preferred);self.page_summary.setMinimumWidth(0)
        nav=W.QHBoxLayout();self.grid_mode=W.QComboBox()
        for mode,name in SCOPE_NAMES.items():self.grid_mode.addItem(name,mode)
        self.grid_mode.setCurrentIndex(self.grid_mode.findData('risk'))
        self.page_prev=W.QPushButton('上一页');self.page_next=W.QPushButton('下一页');self.page_prev.setEnabled(False);self.page_next.setEnabled(False)
        self.page_edit=W.QLineEdit('1');self.page_edit.setMaximumWidth(54);self.page_edit.setToolTip('输入页码并按回车')
        for widget in (self.grid_mode,self.page_prev,self.page_edit,self.page_next,self.page_summary):nav.addWidget(widget)
        nav.setSpacing(2);nav.setStretch(nav.indexOf(self.page_summary),1)
        self.grid_mode.setMaximumWidth(72);self.page_edit.setMaximumWidth(38)
        for button in (self.page_prev,self.page_next):button.setMaximumWidth(42);button.setStyleSheet('padding:1px;')
        self.problem_filter=W.QComboBox();self.problem_filter.addItems(['全部显示','DRC问题','连线失败']);self.problem_filter.setCurrentIndex(1)
        self.problem_filter.setToolTip('重构开启时按重构后的线宽、线距与连接等规则筛选；否则按宽度红色风险或当前整层DRC筛选。空白首行始终保留。')
        nav.addWidget(self.problem_filter);self.manual_button=W.QPushButton('手动偏移');nav.addWidget(self.manual_button);self.manual_button.clicked.connect(lambda:self.manual_preview.open_editor());nav.addStretch();ep.addLayout(nav)
        self.problem_filter.currentIndexChanged.connect(lambda *_:self.filter_changed())
        self.grid_mode.currentIndexChanged.connect(self.evaluation_scope_changed)
        self.view_settings=replace(self.view_settings,evaluation_scope='risk')
        self.page_prev.clicked.connect(lambda:self.show_extreme_page(self.preview_page-1))
        self.page_next.clicked.connect(lambda:self.show_extreme_page(self.preview_page+1))
        self.page_edit.returnPressed.connect(self.jump_extreme_page)
        self.extreme_table=table(['DX / μm','DY / μm','转角 / °','极值定义'],ExtremeTable);self.extreme_table.setRowCount(1);self.extreme_table.verticalHeader().setVisible(True)
        self.extreme_table.setItemDelegate(ExtremeRowDelegate(self.extreme_table))
        self.extreme_table.currentCellChanged.connect(self.select_preview);ep.addWidget(self.extreme_table)
        from .manual_preview import ManualPreview
        self.manual_preview=ManualPreview(self);self.restore_button=self.manual_preview.restore_button
        self.state_table=table(['芯片','DX / μm','DY / μm','转角 / °','组合方向'])
        issue_page=W.QWidget();iv=W.QVBoxLayout(issue_page);iv.setContentsMargins(0,0,0,0);ir=W.QHBoxLayout()
        self.show_issues=W.QCheckBox('显示DRC问题');self.show_issues.setChecked(ViewSettings().show_contact_markers)
        ir.addWidget(self.show_issues);self.issue_prev=W.QPushButton('上一页');self.issue_next=W.QPushButton('下一页');ir.addWidget(self.issue_prev);ir.addWidget(self.issue_next);self.issue_summary=label('第 1/1 页');ir.addWidget(self.issue_summary,1);iv.addLayout(ir)
        self.issue_page=0
        self.issue_prev.clicked.connect(lambda:self.show_issue_page(self.issue_page-1));self.issue_next.clicked.connect(lambda:self.show_issue_page(self.issue_page+1))
        self.issue_table=table(['规则问题','图层','要求','实际','X / μm','Y / μm']);self.issue_table.verticalHeader().setVisible(True);iv.addWidget(self.issue_table)
        for col,width in enumerate((142,76,90,100,100)):self.issue_table.setColumnWidth(col,width)
        self.issue_table.horizontalHeader().setStretchLastSection(True)
        self.issue_table.currentCellChanged.connect(self.issue_row_changed);self.issue_table.cellClicked.connect(lambda *_:self.issue_navigation.start())
        self.issue_table.setContextMenuPolicy(getattr(getattr(Qt,'ContextMenuPolicy',Qt),'CustomContextMenu'))
        self.issue_table.customContextMenuRequested.connect(self.issue_menu)
        self.show_issues.toggled.connect(self.contact_markers_changed)
        for panel,grid,layout in ((extreme_page,self.extreme_table,ep),(issue_page,self.issue_table,iv)):
            panel.setStyleSheet(COMPACT_PREVIEW_STYLE);layout.setSpacing(2)
            grid.verticalHeader().setMinimumSectionSize(18);grid.verticalHeader().setDefaultSectionSize(21)
        for line in (nav,ir):line.setSpacing(3)
        self.extreme_split=W.QSplitter(HORIZONTAL);self.extreme_split.addWidget(extreme_page);self.extreme_split.addWidget(issue_page);self.extreme_split.setSizes([520,500])
        self.preview_tabs.addTab(self.extreme_split,'极端情况')
        info=W.QWidget();info_layout=W.QVBoxLayout(info);info_layout.setContentsMargins(2,2,2,2);info_layout.addWidget(self.state_table);self.preview_tabs.addTab(info,'信息')
        right_split.addWidget(bottom);right_split.setSizes([560,240]);self.tabs.addTab(page,'结果图形')
        self.canvas._managed_viewport=True;self.navigation=Navigation(self.canvas,self.ax,self.show_shape_menu)
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
        self.tabs.currentChanged.connect(self.tab_changed)
        self.runtime_view=W.QPlainTextEdit();self.runtime_view.setReadOnly(True);self.runtime_view.setMaximumBlockCount(10000);self.runtime_view.setToolTip('界面保留最近10000行；完整日志：'+str(self.log_bus.path));self.runtime_view.setMaximumHeight(170);self.runtime_view.hide();root.addWidget(self.runtime_view)
        self.runtime_sequence=0
        self.runtime_timer=QtCore.QTimer(self);self.runtime_timer.setInterval(125);self.runtime_timer.timeout.connect(self.refresh_runtime)
        self.runtime_toggle.toggled.connect(self.show_runtime)
        self.runtime_toggle.setEnabled(self.log_bus.enabled)
        self.log_bus.emit('启动 '+__version__+'；硬件资源 '+str(resource_profile()))
        self.plot_layout()
    def refresh_runtime(self):
        if not self.runtime_toggle.isChecked() or not self.log_bus.enabled:return
        self.runtime_sequence,lines=self.log_bus.read_after(self.runtime_sequence)
        if lines:self.runtime_view.appendPlainText('\n'.join(lines))
    def show_runtime(self,on):
        visible=on and self.log_bus.enabled
        self.runtime_view.setVisible(visible)
        if visible:self.refresh_runtime();self.runtime_timer.start()
        else:self.runtime_timer.stop()
    def set_runtime_recording(self,enabled):
        self.log_bus.set_enabled(enabled)
        self.runtime_toggle.setEnabled(enabled)
        self.show_runtime(self.runtime_toggle.isChecked())
    def build_settings(self,page):
        self.setting_sections={}
        def group(title):
            return self.collapsible(page,title,self.setting_sections)[2]
        general=group('通用')
        summary=label('方案1：动静区之间局部重构RDL。\n方案2：RDL整体随动，固定RDL窗口捕获。\n方案3：预制运动RDL，动静接口适配。');summary.setObjectName('Notice');general.addRow(summary)
        self.shortcut_label=label('快捷键（鼠标位于图中）：F 恢复视图，M 手动偏移，K 两次单击测量（吸附图形；Ctrl八方向、Shift水平垂直；Shift/Ctrl+K清空），Shift＋点选/框选追加，Ctrl＋点选/框选扣除，Q 查看或编辑选中图形节点，ESC 退出/取消选择。\n画布获得焦点后：方向键平移当前视野的20%；测量时中键仍可平移。\n普通模式：滚轮缩放，中键平移，右键单击查看选中图形、拖框缩放。\n手动偏移：右键拖动芯片，滚轮旋转，Ctrl＋滚轮缩放。');general.addRow(self.shortcut_label)
        biases=group('系统偏差与相关性')
        for key in ('mean_x_um','mean_y_um','mean_angle_deg','rho_xy'):
            name,low,high,decimals=SPECS[key]
            widget=W.QDoubleSpinBox();widget.setRange(low,high);widget.setDecimals(decimals)
            widget.setValue(getattr(Parameters(),key));widget.setKeyboardTracking(False)
            widget.valueChanged.connect(self.invalidate);biases.addRow(name,widget);self.fields[key]=widget
        hardware=group('硬件与计算资源')
        saved=W.QCheckBox('保存工程时保存计算结果');self.setting_fields['save_project_results']=saved;hardware.addRow(saved);saved.toggled.connect(self.settings_changed)
        hardware.addRow(label('计算结果与运行缓存：当前项目文件夹 / Cache'))
        profile=resource_profile()
        self.hardware_label=label(f"{profile['cpu']} 逻辑CPU · 可用内存 {profile['available_memory_mb']} MB · 最多 {profile['workers']} 线程 / {profile['process_workers']} 进程\n显卡检测中…");hardware.addRow(self.hardware_label)
        accel=W.QComboBox();accel.addItem('自动（按任务大小）','auto');accel.addItem('CPU','cpu');accel.addItem('CUDA（等待设备自检）','cuda')
        self.setting_fields['acceleration']=accel;hardware.addRow('显卡加速',accel);accel.currentIndexChanged.connect(self.settings_changed)
        accel.setToolTip('可用CUDA用于概率卷积、良率采样、大型刚体与型变坐标批次；小批次优先使用CPU。所有拓扑与最终DRC仍用CPU检查；不会联网安装依赖。')
        probability=group('极端足迹')
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
            widget.setKeyboardTracking(False);self.setting_fields[key]=widget
            if form is not None:form.addRow(title,widget)
            widget.valueChanged.connect(self.settings_changed);return widget
        spin(general,'table_page_limit','表格每页最大行数',2,2000)
        undo_steps=spin(None,'max_undo_steps','',0,1000)
        undo_steps.setToolTip('仅记录图形、测量与分析编辑。0 表示关闭记录。')
        history_row=W.QWidget();history_box=W.QHBoxLayout(history_row);history_box.setContentsMargins(0,0,0,0)
        history_box.addWidget(undo_steps,1)
        self.undo_button=W.QToolButton();self.redo_button=W.QToolButton()
        for button,arrow,tip,slot in ((self.undo_button,'LeftArrow','撤销（U）',self.undo_edit),
                                      (self.redo_button,'RightArrow','取消撤销（Ctrl+U / Shift+U）',self.redo_edit)):
            button.setArrowType(getattr(getattr(Qt,'ArrowType',Qt),arrow))
            button.setToolTip(tip);button.setAccessibleName(tip);button.setEnabled(False)
            button.clicked.connect(slot);history_box.addWidget(button)
        general.addRow('最大撤销步数',history_row)
        spin(hardware,'max_threads','最大并行数',0,1024).setSpecialValueText('0（自动）')
        spin(hardware,'max_memory_mb','最大内存占用 / MB',0,16777216).setSpecialValueText('0（自动）')
        caches=hardware
        spin(caches,'cache_max_entries','最大缓存个数 / 工程',10,100000)
        self.setting_fields['cache_max_entries'].setToolTip('数值结果和动画各自按此上限保留。命中只更新最近使用顺序，正反动画共用一项。无位移状态固定保留。')
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
        space=group('避让空间')
        apd_interval=spin(None,'space_export_interval_um','',.001,10000,3)
        apd_row=W.QWidget();apd_layout=W.QHBoxLayout(apd_row);apd_layout.setContentsMargins(0,0,0,0)
        apd_layout.addWidget(apd_interval,1)
        self.apd_export_button=W.QPushButton('APD规则输出')
        from .apd_export_ui import export as apd_export
        self.apd_export_button.clicked.connect(lambda:apd_export(self));apd_layout.addWidget(self.apd_export_button)
        space.addRow('避让空间输出间隔 / μm',apd_row)
        choice(space,'space_scope','显示范围')
        choice(space,'space_cmap','配色方案');choice(space,'space_interpolation','显示平滑')
        spin(space,'space_resolution','长边网格数',31,401)
        spin(space,'space_alpha','叠加不透明度',.05,.95,2)
        animation=group('动画')
        enabled=W.QCheckBox('启用渐变插值运动');self.setting_fields['animation_enabled']=enabled;animation.addRow(enabled);enabled.toggled.connect(self.settings_changed)
        back=W.QCheckBox('返回无偏移时播放渐变');self.setting_fields['animation_return_zero']=back;animation.addRow(back);back.toggled.connect(self.settings_changed)
        choices['animation_strategy']=[('算完连续播放','prepared'),('边算边显示','stream')]
        choice(animation,'animation_strategy','型变动画播放方式')
        spin(animation,'animation_duration_ms','时长上限 / ms',80,3000)
        spin(animation,'animation_speed','预演速度级别',1,5)
        spin(animation,'animation_fps','FPS（自动计算插值数）',10,120)
        spin(animation,'camera_speed','定位视角速度级别',1,5)
        safe=group('结果分析')
        spin(safe,'safe_box_precision','端点搜索精度 / 输入单边幅度',.0001,.1,4)
        spin(safe,'safe_box_budget','每候选连续验证盒预算',32,32768)
        spin(safe,'safe_box_starts','端点扩张起点数',1,6)
        spin(safe,'safe_box_passes','体积交换轮数',1,8)
        yield_group=safe
        choices['yield_method']=[('蒙特卡洛 + 99%置信区间','monte_carlo'),('确定性积分（不随机采样）','partition')]
        choice(yield_group,'yield_method','计算方法')
        spin(yield_group,'yield_sample_exponent','样本数 = 2 的指数',8,20)
        spin(yield_group,'yield_box_budget','全输入良率积分预算',64,65536)
        spin(yield_group,'yield_tolerance','无接触良率区间宽度目标',.000001,.1,6)
        self.setting_fields['yield_method'].setToolTip('仅控制无接触良率；宽度良率在“宽度分析”单独设置。')
        self.setting_fields['yield_box_budget'].setToolTip('无接触积分按每DIE分配状态盒；与宽度分析的网格节点数独立。')
        self.setting_fields['safe_box_precision'].setToolTip('返回包含输入均值的安全内盒；多个端点顺序比较体积。未定候选拒绝，精度不代表全局最优误差。')
        width_probability=group('宽度分析')
        choices['width_yield_method']=[('尾部增强采样（联合概率）','importance'),('全情况分析（有限网格穷举）','grid')]
        choice(width_probability,'width_yield_method','计算方法')
        spin(width_probability,'width_yield_exponent','采样数 = 2 的指数',8,20)
        spin(width_probability,'width_yield_seed','采样随机种子',0,2147483647)
        spin(width_probability,'width_grid_nodes','每活动轴网格节点数（含端点）',3,21)
        self.setting_fields['width_grid_nodes'].setToolTip('穷举全部有限网格组合及其区间概率，按真实依赖分解共享计算；零幅度轴不增加组合。提高节点数会增加耗时，不是连续空间的有限精确穷举。')
        channel=width_probability
        self.width_sufficient_toggle=W.QCheckBox('显示宽度足够');channel.addRow(self.width_sufficient_toggle);self.width_sufficient_toggle.toggled.connect(self.width_filter_changed)
        box=W.QCheckBox('标签显示问题概率');self.setting_fields['width_show_probability']=box;channel.addRow(box);box.toggled.connect(self.settings_changed)
        box.setToolTip('显示该通道在完整输入范围内出现红色或黄色问题的概率；不是当前姿态的0/100%。小数与良率共用。')
        spin(channel,'width_yield_decimals','良率小数位数',0,12)
        self.setting_fields['width_yield_decimals'].setToolTip('接近100%时以100%减去科学计数法失效概率显示；未采到事件时显示区间界限。')
        drawing_modes=WidthDisplayEditor();self.setting_fields['width_display']=drawing_modes
        drawing_modes.setToolTip('风险线路按完整输入范围下的联合失效概率改变RGB深浅（对数深度）；不随概率改变填充透明度。')
        channel.addRow(drawing_modes);drawing_modes.valueChanged.connect(self.settings_changed)
        text_row=W.QHBoxLayout();text_row.setContentsMargins(0,0,0,0)
        text_row.addWidget(spin(None,'width_label_font_pt','',6,24,1))
        text_row.addWidget(W.QLabel('标签小数位数'))
        text_row.addWidget(spin(None,'width_label_decimals','',0,6))
        channel.addRow('标签字号 / pt',text_row)
        for form,key,title in ((channel,'show_low_width','显示低概率宽度风险'),):
            box=W.QCheckBox(title);self.setting_fields[key]=box;form.addRow(box);box.toggled.connect(self.settings_changed)
            box.setToolTip('有限正良率级别下，仅淡显或隐藏已证明事件概率上界不超过门限的位置。未定位置保留，全量计算结果可查。')
        # Insert the dimensions before the spacer at the end of the process page.
        self.process_layout.takeAt(self.process_layout.count()-1)
        drc_group=self._group(self.process_layout,'其他',[],collapsed=True)
        self.parameter_field(drc_group,'minimum_area_um2')
        self.parameter_field(drc_group,'minimum_edge_um')
        spin(drc_group,'drc_min_angle_deg','最小铜图形内角 / °',1,179,1)
        spin(drc_group,'drc_tolerance_um','尺寸容差 ± / μm',-10000,10000,3)
        self.setting_fields['drc_tolerance_um'].setToolTip('有效最小值 = 工艺值 − 容差。正值放宽，负值收紧；默认0.1 μm。线宽、线距、包围量、机械净距和图形距边框共用；实体偏移分布不变。')
        self.process_layout.addStretch()
        for header in self.process_sections.values():header.setChecked(True)
        recon=group('重构预演')
        choices['reconstruction_die_mode']=[('自动','auto'),('DIE内随动','follow'),('DIE内静止','stationary')]
        choice(recon,'reconstruction_die_mode','DIE内动区判断')
        auto_row=W.QWidget();auto_layout=W.QHBoxLayout(auto_row);auto_layout.setContentsMargins(0,0,0,0)
        auto=W.QCheckBox('自动计算');self.setting_fields['reconstruction_auto']=auto
        self.reconstruction_compute=W.QPushButton('重构计算');auto_layout.addWidget(auto);auto_layout.addWidget(self.reconstruction_compute)
        recon.addRow(auto_row);auto.toggled.connect(self.settings_changed);self.reconstruction_compute.clicked.connect(self.compute_reconstruction)
        row=W.QWidget();rv=W.QHBoxLayout(row);rv.setContentsMargins(0,0,0,0)
        self.region_move=W.QPushButton('框选动区');self.region_move.setCheckable(True)
        self.region_move.toggled.connect(lambda on:self.region_mode('move',on))
        self.region_move.setToolTip('在名义图形上单击起点和终点；滚轮旋转，Esc取消。必要时延伸至源铜边界。')
        clear_cuts=W.QPushButton('清除全部手动切口');clear_cuts.clicked.connect(self.clear_manual_cuts)
        delete_cut=W.QPushButton('删除切口');delete_cut.clicked.connect(self.delete_selected_cut)
        for button in (self.region_move,clear_cuts,delete_cut):rv.addWidget(button)
        recon.addRow(row)
        choices['reconstruction_box_mode']=[('自由','free'),('符合边界角度','aligned')]
        choice(recon,'reconstruction_box_mode','框选边界')
        choices['reconstruction_fixed_via_mode']=[('关闭','off'),('全局模式','global'),('分别处理','individual')]
        choices['reconstruction_between_motion']=[('跟随幅度大的一侧','max'),('跟随幅度小的一侧','min'),('两侧平均','average')]
        follow_row=W.QWidget();follow_layout=W.QHBoxLayout(follow_row);follow_layout.setContentsMargins(0,0,0,0)
        fixed_via_box=W.QComboBox()
        for text,value in choices['reconstruction_fixed_via_mode']:fixed_via_box.addItem(text,value)
        self.setting_fields['reconstruction_fixed_via_mode']=fixed_via_box;fixed_via_box.currentIndexChanged.connect(self.settings_changed)
        between_box=W.QComboBox()
        for text,value in choices['reconstruction_between_motion']:between_box.addItem(text,value)
        between_box.setToolTip('当一个图形存在多个动区时，控制动区之间刚性铜段的默认随动方式；也可单独选中该段右键切换。')
        self.setting_fields['reconstruction_between_motion']=between_box;between_box.currentIndexChanged.connect(self.settings_changed)
        follow_layout.addWidget(W.QLabel('固定过孔随动逻辑'));follow_layout.addWidget(fixed_via_box,1)
        follow_layout.addWidget(W.QLabel('动区间随动逻辑'));follow_layout.addWidget(between_box,1)
        recon.addRow(follow_row)

        choices['reconstruction_style']=[('直线','linear'),('一阶','c1'),('二阶','c2'),('圆弧','arc')]
        choice(recon,'reconstruction_style','连接样式')
        choices['reconstruction_boundary_mode']=[('垂直','perpendicular'),('重构区优先','wide'),('两侧平均','balanced')]
        choice(recon,'reconstruction_boundary_mode','边界角度')
        moving_pad=W.QCheckBox('允许运动孔盘随动');self.setting_fields['reconstruction_allow_moving_pad']=moving_pad
        recon.addRow(moving_pad);moving_pad.toggled.connect(self.settings_changed)
        moving_pad.setToolTip('开启后，运动过孔侧可将整个孔盘纳入动区，并以圆点表示。关闭后只使用直线边界。')
        spin(recon,'reconstruction_pad_trigger_um','整孔盘触发余量 / μm',0,1000,3)
        spin(recon,'reconstruction_grid_nodes','良率积分每轴节点',3,9)
        self.setting_fields['reconstruction_grid_nodes'].setToolTip('完整有限网格，按区间概率质量积分；增大节点可检查连续空间误差。不是等概率极端计数。')
        live=W.QCheckBox('实时反馈动区变化');self.setting_fields['reconstruction_live']=live;recon.addRow(live);live.toggled.connect(self.settings_changed)
        snap_row=W.QHBoxLayout()
        distance=spin(None,'reconstruction_node_snap_um','',-1,1000,3)
        distance.setToolTip('控制轮廓节点与拐角吸附。−1 关闭，0 随当前缩放，正值为实际距离。')
        snap_row.addWidget(distance);snap_row.addWidget(W.QLabel('吸附强度'))
        strength=spin(None,'reconstruction_node_snap_strength','',0,1,2)
        strength.setToolTip('0 无吸引，1 最大吸引。');snap_row.addWidget(strength)
        recon.addRow('拖动吸附 / μm',snap_row)
        waiver_row=W.QHBoxLayout()
        auto_waive=W.QCheckBox('自动忽略低概率DRC')
        self.setting_fields['reconstruction_auto_waive_drc']=auto_waive
        auto_waive.setToolTip('按当前计算范围内同一规则和源对象的联合离散事件概率判断。关闭时清除所有忽略。')
        waiver_row.addWidget(auto_waive)
        mantissa=spin(None,'reconstruction_waiver_mantissa','',1,9)
        mantissa.setAccessibleName('概率系数')
        exponent=spin(None,'reconstruction_waiver_exponent','',-99,0)
        exponent.setAccessibleName('概率指数')
        waiver_row.addWidget(mantissa);waiver_row.addWidget(W.QLabel('× 10^'));waiver_row.addWidget(exponent)
        recon.addRow(waiver_row);auto_waive.toggled.connect(self.settings_changed)
        from .drc_policy import build as build_drc_policy
        drawing=build_drc_policy(self,page)
        markers=W.QCheckBox('显示DRC问题');self.setting_fields['show_contact_markers']=markers;drawing.addRow(markers);markers.toggled.connect(self.settings_changed)
        spin(drawing,'intrusion_alpha','侵入面积不透明度',.05,.85,2)
        for _,key,title,_,_ in RANGES:
            check=W.QCheckBox('显示'+title+'虚线');self.setting_fields[key]=check;drawing.addRow(check)
            check.setToolTip('只控制结果图形中的范围轮廓，接触、侵入面积和评估仍按工艺规则计算。')
            check.toggled.connect(self.settings_changed)
        color=ColorButton();self.setting_fields['contact_marker_color']=color;drawing.addRow('接触空心圆颜色',color);color.valueChanged.connect(self.settings_changed)
        spin(drawing,'contact_marker_size','接触空心圆直径 / pt 或 μm',.1,10000,2)
        check=W.QCheckBox('接触空心圆使用图纸绝对大小 / μm');self.setting_fields['contact_marker_absolute']=check;drawing.addRow(check);check.toggled.connect(self.settings_changed)
        page.addStretch();self.load_view_settings()
    def discover_hardware(self):
        if self._closing:return
        from .acceleration import capabilities,graphics_inventory
        def work(progress):
            if progress:progress(5,'检测显卡与计算运行时')
            cap=capabilities();inventory=graphics_inventory()
            if progress:progress(100,'硬件检测完成')
            return cap,inventory
        worker=Worker(work,self);worker.report_progress=self.progress_enabled();self.hardware_worker=worker;self.watch_worker(worker,'硬件检测')
        def accepted(value):
            if self._closing:return
            cap,adapters=value;profile=resource_profile()
            self.hardware_label.setText(f"{profile['cpu']} 逻辑CPU · 可用内存 {profile['available_memory_mb']} MB · 最多 {profile['workers']} 线程 / {profile['process_workers']} 进程\n"+('; '.join(dict.fromkeys(d['name'] for d in (*adapters,*cap['devices']))) or '未检出显卡')+'\n'+cap['reason'])
            self.log_bus.emit(self.hardware_label.text())
            combo=self.setting_fields['acceleration'];combo.blockSignals(True)
            title='CUDA · '+cap['selected']['name'] if cap['backend']=='cuda' else 'CUDA（未通过自检，使用 CPU）'
            combo.setItemText(combo.findData('cuda'),title)
            combo.setCurrentIndex(max(0,combo.findData(self.view_settings.acceleration)));combo.blockSignals(False)
        def finished():self.hardware_worker=None;worker.deleteLater()
        worker.done.connect(accepted);worker.finished.connect(finished);worker.start()
    def load_view_settings(self):
        from .resources import configure_limits
        configure_limits(self.view_settings.max_threads,self.view_settings.max_memory_mb)
        for key,widget in self.setting_fields.items():
            widget.blockSignals(True);value=getattr(self.view_settings,key)
            if isinstance(widget,W.QComboBox):widget.setCurrentIndex(max(0,widget.findData(value)))
            elif isinstance(widget,W.QCheckBox):widget.setChecked(value)
            elif isinstance(widget,W.QLineEdit):widget.setText(value)
            else:widget.setValue(value)
            widget.blockSignals(False)
        if hasattr(self,'show_issues'):
            self.show_issues.blockSignals(True);self.show_issues.setChecked(self.view_settings.show_contact_markers);self.show_issues.blockSignals(False)
        self.enable_settings()
    def enable_settings(self):
        self.setting_fields['reconstruction_pad_trigger_um'].setEnabled(self.view_settings.reconstruction_allow_moving_pad)
        if not self.setting_fields:return
        grid=self.setting_fields['width_yield_method'].currentData()=='grid'
        full=self.view_settings.evaluation_scope=='all'
        self.setting_fields['width_yield_method'].setEnabled(full)
        self.setting_fields['reconstruction_grid_nodes'].setEnabled(full)
        self.setting_fields['width_grid_nodes'].setEnabled(full and grid)
        for key in ('width_yield_exponent','width_yield_seed'):self.setting_fields[key].setEnabled(full and not grid)
        partition=self.setting_fields['yield_method'].currentData()=='partition'
        self.setting_fields['yield_sample_exponent'].setEnabled(not partition)
        for key in ('yield_box_budget','yield_tolerance'):self.setting_fields[key].setEnabled(partition)
        binary=self.target_sigma.currentText()=='∞'
        for key in ('probability_show_ranges','probability_ranges','probability_target_color','probability_method','show_low_contact','show_low_width','yield_method','contact_box_budget'):
            if key in self.setting_fields:self.setting_fields[key].setEnabled(not binary)
        if binary:
            for key in ('sample_exponent','random_seed','angle_bins','quadrature_order','yield_sample_exponent','yield_box_budget','yield_tolerance'):
                self.setting_fields[key].setEnabled(False)
            return
        deterministic=self.setting_fields['probability_method'].currentData()=='quadrature'
        for key in ('sample_exponent','random_seed','angle_bins'):self.setting_fields[key].setEnabled(not deterministic)
        for key in ('quadrature_order',):self.setting_fields[key].setEnabled(deterministic)
    def settings_changed(self,*_):
        if self.loading:return
        values={k:(w.currentData() if isinstance(w,W.QComboBox) else w.isChecked() if isinstance(w,W.QCheckBox) else w.text() if isinstance(w,W.QLineEdit) else w.value()) for k,w in self.setting_fields.items()}
        for key in ('reconstruction_node_snap_um','reconstruction_waiver_mantissa'):
            if -1 < values[key] < 0:
                value=-1.
            elif key=='reconstruction_waiver_mantissa' and values['reconstruction_waiver_exponent']==0:
                value=1
            else:continue
            field=self.setting_fields[key]
            blocked=field.blockSignals(True);field.setValue(value);field.blockSignals(blocked)
            values[key]=value
        updated=ViewSettings.from_dict(dict(asdict(self.view_settings),**values))
        waiver_fields=('reconstruction_auto_waive_drc','reconstruction_waiver_mantissa','reconstruction_waiver_exponent')
        if any(getattr(updated,key)!=getattr(self.view_settings,key) for key in waiver_fields):
            switching=updated.reconstruction_auto_waive_drc!=self.view_settings.reconstruction_auto_waive_drc
            if switching and not updated.reconstruction_auto_waive_drc:
                updated=replace(updated,drc_waived_refs=(),drc_retained_refs=())
            self.view_settings=updated
            if switching:self.reconstruction_yield.refresh()
            self.replace_drc_waivers({} if switching and not updated.reconstruction_auto_waive_drc else self.ignored_drc)
            return
        if updated.space_export_interval_um!=self.view_settings.space_export_interval_um:
            self.view_settings=updated
            return
        if updated.max_undo_steps!=self.view_settings.max_undo_steps:
            self.view_settings=updated;self.edit_history.trim()
            return
        if updated.reconstruction_allow_moving_pad!=self.view_settings.reconstruction_allow_moving_pad:
            self.change_moving_pad_mode(updated)
            return
        if updated.table_page_limit!=self.view_settings.table_page_limit:
            old=self.view_settings.table_page_limit;self.view_settings=updated
            size=updated.table_page_limit
            if self.extremes is not None:
                self.show_extreme_page(self.preview_page*(old-1)//(size-1),redraw=False,keep_pose=True)
            self.show_issue_page(self.issue_page*old//size)
            return
        if (updated.max_threads,updated.max_memory_mb)!=(self.view_settings.max_threads,self.view_settings.max_memory_mb):
            from .resources import configure_limits
            configure_limits(updated.max_threads,updated.max_memory_mb);p=resource_profile()
            self.hardware_label.setText(f"{p['cpu']} 逻辑CPU · 工作预算 {p['memory_budget_mb']} MB · 最多 {p['workers']} 线程 / {p['process_workers']} 进程")
            self.view_settings=updated;return
        if not updated.reconstruction_auto and self.reconstruction_toggle.isChecked():
            numerical=updated.reconstruction_key()!=self.view_settings.reconstruction_key() or updated.numeric_key()!=self.view_settings.numeric_key()
            if numerical:
                self.view_settings=updated;self.enable_settings();self.defer_reconstruction();return
            if self.view_settings.reconstruction_auto:
                self.view_settings=updated;self.enable_settings();return
        if updated.reconstruction_auto and not self.view_settings.reconstruction_auto:
            self.view_settings=updated;self.enable_settings();self.compute_reconstruction();return
        analysis_inputs=('safe_box_precision','safe_box_budget','safe_box_starts','safe_box_passes','yield_method','yield_sample_exponent','yield_box_budget','yield_tolerance')
        if any(getattr(updated,k)!=getattr(self.view_settings,k) for k in analysis_inputs):
            if self.result is not None:
                self.result['analysis_complete']=False;self.result['analysis_settings']=asdict(updated)
                if self.analysis.worker:self.analysis.worker.cancelled.set()
            self.view_settings=updated;self.enable_settings();self.analysis.activate();return
        width_inputs=('width_yield_method','width_yield_exponent','width_yield_seed','width_grid_nodes')
        if all(getattr(updated,k)==getattr(self.view_settings,k) for k in values if k not in width_inputs):
            self.view_settings=updated;self.enable_settings();self.width_yield.refresh();return
        ranges=('show_annulus_range','show_spacing_range')
        if all(getattr(updated,k)==getattr(self.view_settings,k) for k in values if k not in ranges):
            changed=any(getattr(updated,k)!=getattr(self.view_settings,k) for k in ranges)
            self.view_settings=updated
            if changed and self.data:
                if self.tabs.currentIndex()==0:
                    self.draw_contact_ranges();self.refresh_legend();self.update_die_label_visibility();self.render_scene()
                else:self._layout_pending=True
            return
        presentation=('drc_visible','show_contact_markers','reconstruction_live','width_show_probability','width_display','width_label_font_pt','width_label_decimals','width_yield_decimals','show_low_contact','show_low_width',*ranges)
        interaction=('reconstruction_node_snap_um','reconstruction_node_snap_strength')
        if all(getattr(updated,k)==getattr(self.view_settings,k) for k in values if k not in interaction):
            self.view_settings=updated
            return
        if all(getattr(updated,k)==getattr(self.view_settings,k) for k in values if k not in (*presentation,'cache_max_entries')):
            display_change=any(getattr(updated,k)!=getattr(self.view_settings,k) for k in presentation)
            self.view_settings=updated;self.show_issues.setChecked(updated.show_contact_markers);self.cache_group.limit(updated.cache_max_entries);self.refresh_cache_status()
            if display_change and self.data:self._legend_stamp=None;self.plot_layout(preserve=True);self.width_yield.render()
            return
        intent=self._motion_intent if self.transition.timer.isActive() else None
        self.stop_animation()
        old_settings=self.view_settings;updated=ViewSettings.from_dict(dict(asdict(self.view_settings),**values))
        from .drc_rules import effective_rules
        try:effective_rules(self.params(),updated)
        except ValueError as exc:self.load_view_settings();self.error(str(exc));return
        deform=False;numeric=(updated.numeric_key()!=self.view_settings.numeric_key());self.view_settings=updated;self.enable_settings();self.refresh_extreme_colors();self.cache_group.limit(updated.cache_max_entries);self.refresh_cache_status()
        live_motion=any(getattr(updated,k)!=getattr(old_settings,k) for k in ('animation_enabled','animation_return_zero','animation_duration_ms','animation_fps','animation_speed','animation_strategy'))
        reconstruction_shape=updated.reconstruction_key()!=old_settings.reconstruction_key()
        if reconstruction_shape:self.invalidate_reconstruction_edit()
        if updated.reconstruction_grid_nodes!=old_settings.reconstruction_grid_nodes:self.reconstruction_yield.refresh()
        if numeric or updated.drc_tolerance_um!=old_settings.drc_tolerance_um:self.width_yield.refresh()
        if numeric:
            self.dirty=self.result is not None
            self.revision+=1;self.probability_data=None;self.footprint_data=None;self.space_data=None
            if self.worker and self._evaluation_requested:self._coverage_pending=True;self.coverage_timer.start()
            if self.data:self.request_preview()
        elif live_motion and self.data and (self.preview_workers or intent or self.current_frame is not None):self.request_preview()
        elif (deform or reconstruction_shape) and (False or self.show_issues.isChecked() or self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked()):self.request_preview()
        elif intent:
            if intent[0]=='frame':self.present_frame(intent[1])
            else:self.animate_view(*intent[1:])
        elif self.data:self.plot_layout(preserve=True)
    def style_for(self,key,role=None):
        if role is None:
            from .layer_roles import ALIASES
            role=self.role_boxes[key].currentText() if key in self.role_boxes else '不参与';role=ALIASES.get(role,role)
        style=layer_style(role);saved=self.layer_styles.get(key,{})
        if isinstance(saved,dict):
            if QtGui.QColor(str(saved.get('color',''))).isValid():style['color']=saved['color']
            for field,low,high in [('alpha',0,1),('width',.1,5)]:
                value=saved.get(field)
                if isinstance(value,(int,float)) and math.isfinite(value):style[field]=min(high,max(low,value))
            if isinstance(saved.get('fill'),bool):style['fill']=saved['fill']
            if saved.get('linestyle') in ('-','--',':'):style['linestyle']=saved['linestyle']
        if role=='运动RDL' and getattr(self,'reconstruction_toggle',None) is not None and self.reconstruction_toggle.isChecked():
            style.update(color='#aaffff',alpha=.30,fill=True)
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
        self.apply_automatic();self.invalidate();self.plot_layout(preserve=True)
    def apply_stackup(self,target_roles,target_order,target_compensation=None,*,case=None):
        """Synchronize layer table roles, row order and output compensation without erroring on mismatch."""
        if self.data is None:return
        self.loading=True;self._reordering=True
        try:
            for key,role in target_roles.items():
                if key in self.role_boxes:
                    prev=self.role_boxes[key].currentText()
                    if prev!=role:
                        self.stage_hints.pop(key,None)
                        self.role_boxes[key].setCurrentText(role)
                        if key in self.visible_boxes:self.visible_boxes[key].setChecked(role not in NON_PARTICIPATING)
                        if key in self.selectable_boxes:self.selectable_boxes[key].setChecked(role=='固定RDL')
            if target_compensation:
                for key,val in target_compensation.items():
                    if key in self.compensation_boxes:self.compensation_boxes[key].setValue(float(val))
            header=self.layer_table.verticalHeader()
            for visual_target,key in enumerate(target_order):
                if key not in self._layer_keys:continue
                logical=self._layer_keys.index(key)
                current_visual=header.visualIndex(logical)
                if current_visual!=visual_target:header.moveSection(current_visual,visual_target)
            self.case=case
            if self.case is None:
                try:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
                except ValueError:pass
            self.connections=None;self.engine=None;self.revision+=1
            self.apply_automatic()
        finally:
            self._reordering=False;self.loading=False
        self.plot_layout(preserve=True)
    def overlay_widget(self,name):
        return self.show_issues if name=='drc' else getattr(self,name+'_toggle')
    def motions(self):return {key:default_motion(role) for key,role in self.roles().items()}
    def output_compensation(self):
        from .output_compensation import normalize
        return normalize({key:box.value() for key,box in self.compensation_boxes.items()})
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
        import time
        if not self.progress_enabled():return
        if time.monotonic()<getattr(worker,'_quiet_until',0.):return
        if getattr(worker,'_task_tracked',True) is False or worker.cancelled.is_set():return
        self._tasks[id(worker)]=(n,text);self.progress.show();self.progress.setRange(0,100);self.progress.setValue(n);self.progress.setFormat(f'{n}% · '+text[:36]);self.statusBar().showMessage(text)
        self.log_bus.emit(text)
    def watch_worker(self,worker,name,quiet=False):
        import time
        self.task_control.checkpoint();self.task_control.button.setEnabled(True)
        start=time.perf_counter();worker._task_tracked=True
        worker._quiet_until=time.monotonic()+.25 if quiet else 0.
        self._tasks[id(worker)]=(0,name+'：开始')
        if worker.report_progress:
            self.task_progress(worker,0,name+'：开始')
            if quiet:
                QtCore.QTimer.singleShot(260,lambda:self.task_progress(worker,0,name+'：计算中') if getattr(worker,'_task_tracked',False) and not worker.cancelled.is_set() else None)
            worker.progress.connect(lambda n,t:self.task_progress(worker,n,t))
        worker.failed.connect(lambda text:self.log_bus.emit(name+'：'+text,'ERROR'))
        def finish():
            worker._task_tracked=False;self._tasks.pop(id(worker),None);self.task_control.finished();self.log_bus.emit(f'{name}：'+('已取消' if worker.cancelled.is_set() else '线程退出')+f'，耗时 {time.perf_counter()-start:.3f} s')
            if self._closing and not self._tasks:QtCore.QTimer.singleShot(0,self.close)
            if not self._tasks:
                self.progress.hide()
                self.statusBar().clearMessage()
                self.cleanup_retired_sessions()
            elif self.progress_enabled():
                n,t=next(reversed(self._tasks.values()));self.progress.setValue(n);self.progress.setFormat(f'{n}% · '+t[:36]);self.statusBar().showMessage(t)
        worker.finished.connect(finish)

    def progress_enabled(self):
        return not (self.remote_locked or getattr(self,'requested_measured',False))
    def refresh_cache_status(self):
        size,total,count,pinned=self.cache_registry.statistics(self.cache_project_key)
        def fmt_size(n):
            return f'{n/1024**3:.2f} GiB' if n>=1024**3 else f'{n/1024**2:.2f} MiB' if n>=1024**2 else f'{n/1024:.1f} KiB' if n>=1024 else f'{n} B'
        self.cache_size_label.setText(f'当前工程：{fmt_size(size)}\n全部工程：{fmt_size(total)}'+(' · 清理中' if self.cache_clear_worker is not None else ''))
        self.cache_size_label.setToolTip(f'当前普通结果 {count-pinned} 个；固定保留 {pinned} 个；动画 {len(self.cache_group.replays)} 个。统计本次运行中所有打开过的工程缓存文件，关闭软件后清理。')
        if self.data is not None and self.extremes is not None and self._cache_colors_changed():
            self.refresh_extreme_colors(self.current_frame)
    def preview_settings(self):
        current = self.view_settings
        applied = getattr(self, '_applied_preview_settings', None)
        if not getattr(self, 'reconstruction_pending', False) or applied is None:
            return current
        # Pose changes use committed reconstruction inputs. Keep current
        # display and animation settings while a manual edit is staged.
        names = [name for name in vars(current) if name.startswith('reconstruction_')]
        names += ['drc_checks', 'drc_tolerance_um', 'drc_min_angle_deg']
        return replace(current, **{name: getattr(applied, name) for name in names})

    def active_frame_key(self):
        cfg=self.preview_settings()
        return ((False and self.result is not None,*cfg.deformation_key()),
                (True,cfg.drc_min_angle_deg,cfg.drc_tolerance_um),
                cfg.width_key() if self.width_toggle.isChecked() else (False,),
                cfg.reconstruction_key() if self.reconstruction_toggle.isChecked() else (False,))
    def _cache_colors_changed(self):
        group=self.cache_group
        config=self.active_frame_key()
        context=(id(group),self.revision,id(self.result),id(self.data),config)
        if context!=getattr(self,'_cache_color_context',None):
            self._cache_color_context=context;self._cache_color_epoch=-1;self._cached_pose_counts={};self._cached_row_checks={}
            try:params=self.params()
            except ValueError:
                self._cache_color_signature=None;return True
            self._cache_color_signature=self.preview_identity(self.data,self.roles(),self.ordered_layers(),self.motions(),params,self.result)
        inventory=group.pose_inventory(self._cache_color_signature,bytes(24*len(self.extremes.locals)),self._cache_color_epoch,config)
        if inventory is None:return False
        self._cache_color_epoch,self._cached_pose_counts,self._cached_row_checks=inventory
        return True
    def _attach_cache_group(self,key,count):
        self.cache_project_key=key;self.cache_group=self.cache_registry.get(key,count)
        if getattr(self,'_project_cache_root',None):self.cache_group.relocate(self._project_cache_root)
        self.session_values=self.cache_group.values;self.session_engines=self.cache_group.engines;self.engine=None
        from .session_cache import EphemeralCache
        self.space_motion_fields=EphemeralCache()
        if hasattr(self,'edit_history'):self.edit_history.refresh()
    def switch_project_cache(self,key,count):
        self.stop_animation();self._prepared_motion=None;self._prepared_package=None
        self.cancel_previews()
        if self.motion_worker:self.motion_worker.cancelled.set()
        if self.cache_group.key!=key:self.parked_groups.append(self.cache_group)
        self._attach_cache_group(key,count);self.cleanup_retired_sessions();self.refresh_cache_status()
    def reset_session_cache(self):
        self._coupled_key=None;self._coupled_data=None
        if self.filter_job:self.filter_job.cancelled.set()
        self.stop_animation();self._prepared_motion=None;self._prepared_package=None
        self.cancel_previews()
        if self.motion_worker:self.motion_worker.cancelled.set()
        old=self.cache_registry.retire(self.cache_project_key)
        if old is not None:old.enabled=False;self.retired_sessions.append(old)
        self._attach_cache_group(self.cache_project_key,self.view_settings.cache_max_entries)
        self.cleanup_retired_sessions();self.refresh_cache_status();self.width_yield.refresh()
    def clear_cached_results(self,all_projects=False):
        if self.cache_clear_worker is not None:return
        self.edit_history.clear(all_projects)
        self.stop_animation();self.width_yield.reset();self.reconstruction_yield.reset();self.constraint_feedback.cancel();self._prepared_motion=None;self._prepared_package=None
        self.preview_ticket+=1;self.preview_pending=None;self.motion_pending=None;self.motion_generation+=1
        self._width_grid_summary=None;self._problem_tag=None;self._coupled_key=None;self._coupled_data=None
        for worker in [self.worker,self.motion_worker,self.filter_job,*self.preview_workers]:
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
            self.cache_clear_worker=None;self.clear_cache_button.setEnabled(True);self.refresh_cache_status();self.cleanup_retired_sessions();worker.deleteLater();self.width_yield.refresh()
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
            from .reconstruction_rules import intrinsic
            intrinsic.cache_clear()
            from .reconstruction_enclosure import reachable_enclosure
            reachable_enclosure.cache_clear()
            from .reconstruction_ownership import divide
            divide.cache_clear();self.reconstruction_yield.plan_pool.clear();self.reconstruction_yield.factor_pool.clear();self.reconstruction_yield.geometry_pool.clear();self.constraint_feedback.context=None
        for group_ in self.parked_groups:
            if group_ is self.cache_group:continue
            group_.park_frames()
            for engine in group_.engines.values():
                for deformer in engine.deformers.values():deformer.release_workers()
        self.parked_groups.clear()
    def assessment_identity(self,data,roles,order,p,settings):
        from .session_cache import logical_fingerprint
        return ('assessment-v28',logical_fingerprint((data.geometry_id,data.top_name,roles,order,asdict(p),settings.numeric_key(),settings.drc_tolerance_um,settings.drc_min_angle_deg,settings.evaluation_scope)))
    def preview_identity(self,data,roles,order,motions,params,result):
        from .session_cache import logical_fingerprint
        digest=None
        if result is not None:
            if result is self.result and getattr(self,'_assessment_digest',None) is not None:
                digest=self._assessment_digest
            else:digest=logical_fingerprint((result.get('parameters'),result.get('contact_analysis'),result.get('issues')))
        return logical_fingerprint((data.geometry_id,data.top_name,roles,order,motions,asdict(params),digest))
    def clear_preview(self):
        if hasattr(self,'cut_drag'):self.cut_drag.cancel()
        self._width_grid_summary=None;self._problem_tag=None;self._coupled_key=None;self._coupled_data=None
        if self.filter_job:self.filter_job.cancelled.set()
        self.extremes=None;self.active_preview=None;self.owner_cache=None;self.page_items=[];self._extreme_paint={};self.preview_page=0;self.state_result=None
        self.preview_ticket+=1;self.preview_pending=None;self._prepared_motion=None;self.engine=None;self.current_frame=None;self.shown_states=None
        self.extreme_table.blockSignals(True);self.extreme_table.clearContents();self.extreme_table.setRowCount(1);self.extreme_table.setCurrentCell(0,0);self.extreme_table.blockSignals(False)
        self.state_table.setRowCount(0);self.issue_table.setRowCount(0);self._issue_table_stamp=None;self.page_summary.setText('尚未评估');self.page_prev.setEnabled(False);self.page_next.setEnabled(False);self.page_edit.setText('1')
    def coverage_changed(self,*_):
        if self.loading:return
        if self.target_sigma.currentText() not in ('0','∞'):self.reference_nsigma=float(self.target_sigma.currentText())
        self.width_yield.refresh()
        self._width_grid_summary=None;self._problem_tag=None
        self.enable_settings();self.stop_animation();self.revision+=1;self.engine=None;self.connections=None
        self.probability_data=None;self.footprint_data=None;self.space_data=None;self.preview_ticket+=1;self.preview_pending=None
        self.cancel_previews()
        self._coverage_pending=True;self.coverage_timer.start()
        if self.target_sigma.currentText()=='∞' and self.result:
            self.current_frame=None;self.fill_issues();self.plot_layout(preserve=True)
        self.statusBar().showMessage('按当前姿态更新二值接触与实体覆盖…' if self.target_sigma.currentText()=='∞' else '良率级别已改变，正在更新概率、避让空间和窗口判定…')
    def refresh_coverage(self):
        if not self._coverage_pending or not self.data:return
        if self.worker is not None:self.coverage_timer.start();return
        self._coverage_pending=False
        if self.result is not None or self._evaluation_requested:self.run_evaluation(automatic=True)
        else:self.request_preview()
    def invalidate(self,*_):
        if self.loading:return
        self.width_yield.refresh()
        self._width_grid_summary=None;self._problem_tag=None
        self.revision+=1;self.dirty=self.result is not None;self.refresh_extreme_colors();self.engine=None;self.connections=None;self.probability_data=None;self.footprint_data=None;self.space_data=None
        self.statusBar().showMessage('参数已修改；保留上次计算结果。预演控制区使用当前工艺，重新评估后更新统计结论。')
        if self.data and (self.active_preview is not None or self.manual_states is not None or self.probability_toggle.isChecked() or self.space_toggle.isChecked() or False or self.show_issues.isChecked() or self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked()):
            try:self.request_preview()
            except ValueError:pass
    def drop_result(self):
        if hasattr(self,'analysis'):self.analysis.cancel()
        self.result=None;self.clear_preview();self.manual_states=None;self.dirty=False;self.probability_data=None;self.footprint_data=None;self.space_data=None
        self.scheme_table.setRowCount(0)
        self.safe_range_plot_data=draw_safe_ranges(self.safe_range_figure,{});self.safe_range_canvas.setMinimumHeight(240);self.safe_range_canvas.draw_idle()
        self.scheme_figure.clear();self.scheme_canvas.draw_idle();self.plot_layout(preserve=True);self.update_histograms()
    def error(self,text):
        if getattr(self,'remote_locked',False):
            self.log_bus.emit(str(text),'ERROR');self.measured_output.fail_transition(str(text));return
        self.log_bus.emit(text,'ERROR');W.QMessageBox.warning(self,'操作未完成',str(text))
    def start_worker(self,fn,success,interactive=False):
        if self.worker is not None:self.error('上一项操作尚未完成');return
        if self.progress_enabled():self.progress.show();self.progress.setValue(0)
        self.run_button.setEnabled(False);self.controls.setEnabled(interactive);self.job_combo.setEnabled(False);self.save_button.setEnabled(False)
        worker=Worker(fn,self);self.worker=worker
        worker.report_progress=self.progress_enabled()
        self.watch_worker(worker,'主计算')
        worker.done.connect(lambda result:success(result) if not worker.cancelled.is_set() else None);worker.failed.connect(lambda text:(self.refresh_jobs(),self.error(text)));worker.finished.connect(lambda:self.finish_worker(worker));worker.start()
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
        if self.edit_history.queued:QtCore.QTimer.singleShot(0,self.edit_history.drain)
        if getattr(self,'remote_locked',False):self.measured_output.apply_lock()
    def area_changed(self,*_):
        if self.loading or self.data is None:return
        if self.worker is not None:
            if not getattr(self,'_area_reload_pending',False):
                self._area_reload_pending=True
                def retry():
                    self._area_reload_pending=False
                    if not self._closing:self.area_changed()
                QtCore.QTimer.singleShot(100,retry)
            return
        from .area_filter import canonical_project
        project=canonical_project(self.data,self.project_settings())
        self.load_gds(self.data.path,self.data.top_name,project,self.project_path)
    def open_gds(self):
        path,_=W.QFileDialog.getOpenFileName(self,'输入GDS/OAS','','GDS/OASIS (*.gds *.gdsii *.GDS *.oas *.oasis *.OAS)')
        if path:self.load_gds(path)
    def load_gds(self,path,top=None,project=None,project_path=None):
        from .resources import progress_slice,cancel_check
        self.exit_region_mode();current_area=self.fields['minimum_area_um2'].value();current_edge=self.fields['minimum_edge_um'].value()
        def read_and_prepare(progress):
            data=read_gds(path,top,progress_slice(progress,0,.7))
            if isinstance(data,dict):return data
            settings=project if project and (not project.get('sha256') or project['sha256']==data.sha256) else {'schema_version':5,'parameters':{'minimum_area_um2':current_area,'minimum_edge_um':current_edge},'settings':{'reconstruction_auto':False}}
            from .gds_merge import restore_project_geometry
            data,settings=restore_project_geometry(data,settings);data.restored_project=settings
            roles=settings.get('roles',data.auto_roles)
            cancel_check()
            if progress:progress(73,'构建几何索引与图层缓存')
            data.geometry_cache=GeometryCache(data)
            data.prepared_roles=roles;data.prepared_auto=automatic_parameters(data,roles)
            if progress:progress(91,'构建名义金属连通与芯片归属')
            try:
                from .stack import Connections
                if '封装边框' in roles.values() and '芯片边框' in roles.values():
                    connections=Connections(data,roles,settings.get('layer_order'),data.geometry_cache.geometries)
                    data.prepared_case=build_case(data,roles,connections=connections)
                else:data.prepared_case=None
            except ValueError:data.prepared_case=None
            if progress:progress(100,'导入完成')
            return data
        def accepted(data):
            settings=project
            if isinstance(data,dict):
                if self.remote_locked or getattr(self,'requested_measured',False):
                    self.error('工程须明确指定top_cell；当前有多个顶层：'+', '.join(data['top_names'][:20]));return
                name,ok=W.QInputDialog.getItem(self,'选择顶层Cell','待评估顶层：',data['top_names'],0,False)
                if ok:QtCore.QTimer.singleShot(10,lambda:self.load_gds(path,name,project,project_path))
                return
            if settings and settings.get('sha256') and settings['sha256']!=data.sha256:
                self.error('GDS已改变，请重新设置图层。');settings=None
            settings=getattr(data,'restored_project',settings)
            self.set_data(data,settings,project_path=project_path)
            self.project_path=Path(project_path).resolve() if settings and project_path and True else None
            self.refresh_jobs()
        self.start_worker(read_and_prepare,accepted)
    def set_data(self,data,project=None,project_path=None,*,request_analysis=True):
        self.cut_drag.cancel();self.reconstruction_overlay.hide();self.constraint_feedback.cancel()
        self.pending_deleted_shapes={};self.pending_restored_shapes={}
        self._staged_deletions=None;self.pending_layout_ops=[];self._staged_restored_edits=None
        self._staged_node_edits=None;self.pending_node_shapes={}
        self.reconstruction_pending=False;self._reconstruction_apply_once=False;self._applied_preview_settings=None
        self.exit_region_mode()
        from .session_cache import logical_fingerprint
        project=normalize_project(project) if project else {}
        if project and (project.get('geometry_indexing')!='merged-v39' or tuple(getattr(data,'deleted_shapes',()))!=tuple(tuple(r) for r in project.get('deleted_shapes',()))):
            from .gds_merge import restore_project_geometry
            data,project=restore_project_geometry(data,project)
        if data.node_edits!=project.get('node_edits',[]):
            data=edited_snapshot(data,project.get('node_edits',[]))
        for message in data.import_warnings:self.log_bus.emit(message,'WARNING')
        for message in getattr(data,'merge_notes',()):self.log_bus.emit(message)
        cache_settings=ViewSettings.from_dict(project.get('settings')) if project else ViewSettings(reconstruction_auto=False)
        self._applied_preview_settings=cache_settings
        cache_id=logical_fingerprint((str(Path(project_path or data.path).resolve()),data.sha256,data.top_name))
        self._project_cache_root=Path(project_path).resolve().parent/'Cache'/'runtime' if project_path else None
        self.switch_project_cache(cache_id,cache_settings.cache_max_entries)
        pars=Parameters.from_dict(project.get('parameters',{}),require_rdl=False);self.manual_states=None;self.probability_data=None;self.footprint_data=None;self.space_data=None;self.dirty=False
        self.coverage_timer.stop();self._coverage_pending=False;self._evaluation_requested=False
        self.loading=True
        try:
            from .project import restored_width_ignores
            self.measured_output.restore(project.get('measured_output'))
            self.ignored_width_lines=restored_width_ignores(project,data);self.dxf_directory=project.get('dxf_directory','')
            self.ignored_drc=project.get('ignored_drc',{})
            self._waiver_cache=None
            self.apd_options=project.get('apd_export',{})
            from .dxf_settings import DxfSettings
            from .dxf_controls import restore as restore_dxf_controls
            self.dxf_settings=DxfSettings.from_dict(project.get('dxf_options'));restore_dxf_controls(self)
            from .layout_export import restore_controls
            restore_controls(self,project.get('layout_output'))
            self.data=data;self.gds_label.setText(str(Path(data.path).resolve()));self.gds_label.setToolTip(str(Path(data.path).resolve()))
            view=project.get('extreme_view',{'mode':'risk','page':1})
            self.grid_mode.blockSignals(True);self.grid_mode.setCurrentIndex(self.grid_mode.findData(view['mode']));self.grid_mode.blockSignals(False)
            self.problem_filter.blockSignals(True);self.problem_filter.setCurrentIndex({'all':0,'drc':1,'failed':2}[view['filter_mode']]);self.problem_filter.blockSignals(False)
            self._initial_extreme_page=view['page']-1
            for title,header in self.setting_sections.items():header.setChecked(title in project.get('expanded_settings',[]))
            expanded_process=project.get('expanded_process')
            for title,header in self.process_sections.items():
                header.setChecked(True if not project or expanded_process is None else (title in expanded_process))
            self.view_settings=replace(cache_settings,evaluation_scope=self.grid_mode.currentData(),reconstruction_ignored=tuple(sorted(restored_width_ignores(project,data,'ignored_reconstruction'))));self.load_view_settings();self.layer_styles=project.get('layer_styles',{})
            for name,value in project_overlays(project).items():
                toggle=self.overlay_widget(name)
                toggle.blockSignals(True)
                toggle.setChecked(value)
                toggle.blockSignals(False)
            self.case=None;self.result=None;self.geometry_cache=getattr(data,'geometry_cache',None);self.connections=None;self.reset_plot();self.top.clear();self.top.addItems(data.top_names);self.top.setCurrentText(data.top_name)
            self.layer_split.setSizes([project.get('layer_table_height',300),max(220,self.layer_split.height()-project.get('layer_table_height',300))])
            selected_roles=project.get('roles',data.auto_roles)
            self.stage_hints=project.get('stage_hints',{})
            self.layer_drag.end_drag()
            self.role_boxes={};self.visible_boxes={};self.selectable_boxes={};self.compensation_boxes={}
            self._layer_keys=stack_order(data,selected_roles,project.get('layer_order'))
            self.layer_table.setRowCount(0);self.layer_table.setRowCount(len(self._layer_keys))
            header=self.layer_table.verticalHeader()
            for i in range(header.count()):header.moveSection(header.visualIndex(i),i)
            saved_sel=project.get('selectability',{})
            from .output_compensation import default_amount
            saved_compensation=project.get('layer_compensation',{})
            for i,key in enumerate(self._layer_keys):
                role=selected_roles.get(key,'不参与');check=LayerCheckBox();check.setChecked(project.get('visibility',{}).get(key,role not in NON_PARTICIPATING))
                check.toggled.connect(lambda checked,k=key:self.visibility_changed(k));self.visible_boxes[key]=check;self.layer_table.setCellWidget(i,0,check)
                sel=LayerCheckBox();sel.setChecked(saved_sel.get(key,role=='固定RDL'))
                sel.toggled.connect(lambda checked,k=key:self.selectability_changed(k));self.selectable_boxes[key]=sel;self.layer_table.setCellWidget(i,1,sel)
                self.layer_table.setItem(i,2,W.QTableWidgetItem(key))
                box=W.QComboBox();box.addItems(ROLES);box.setCurrentText(role)
                self.role_boxes[key]=box;self.layer_table.setCellWidget(i,3,box)
                box.currentTextChanged.connect(lambda _,k=key:self.role_changed(k))
                compensation=W.QDoubleSpinBox()
                compensation.setDecimals(3);compensation.setRange(-1000,1000);compensation.setKeyboardTracking(False)
                compensation.setFocusPolicy(getattr(Qt,'FocusPolicy',Qt).ClickFocus)
                default=0. if project else default_amount(role)
                compensation.setValue(saved_compensation.get(key,default))
                compensation.setToolTip('输出层轮廓补偿 / μm。负值内缩，正值外扩。')
                self.compensation_boxes[key]=compensation;self.layer_table.setCellWidget(i,4,compensation)
                for widget in (check,sel,box,compensation):self.layer_drag.watch_context(widget)
            for j,width in enumerate([38,38,80,128,98]):self.layer_table.setColumnWidth(j,width)
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
            if hasattr(self,'rulers'):self.rulers.restore(project.get('rulers',[]))
            self.enable_settings();self.apply_automatic();self.per_die_changed();self.manual_preview.restore_fields(project.get('manual_preview'))
        finally:self.loading=False
        if self.remote_locked or getattr(self,'requested_measured',False):
            self.measured_output.startup_overlays={name:self.overlay_widget(name).isChecked() for name in ('reconstruction','width','drc','space','probability')} if not self.remote_locked else None
            for name in ('reconstruction','width','drc','space','probability'):
                widget=self.overlay_widget(name);widget.blockSignals(True);widget.setChecked(False);widget.blockSignals(False)
            self.drop_result();self.dirty=True;self.plot_layout();return
        self._restoring_project=True
        self.drop_result();self.invalidate();self.plot_layout()
        from .project_results import restore
        try:restored=restore(self,project,project_path)
        finally:self._restoring_project=False
        if restored:return
        remembered=self.cache_group.last_assessment
        if remembered is not None:
            key=self.assessment_identity(data,self.roles(),self.ordered_layers(),self.params(),self.view_settings)
            if remembered[0]==key:self.display_result(remembered[1]);self.log_bus.emit('已恢复本次运行中此工程的评估缓存')
        if request_analysis and self.result is None and (self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked()):self.request_preview()
    def selectability_changed(self,key):
        if self.loading or self.data is None:return
        if not self.selectable_boxes[key].isChecked() and getattr(self,'selection',None):
            kept=[item for item in self.selection.selected if item[0]!=key]
            if len(kept)!=len(self.selection.selected):
                self.selection.selected=kept;self.selection.draw()
    def role_changed(self,key):
        if self.loading:return
        from .output_compensation import default_amount
        self.stage_hints.pop(key,None)
        role=self.role_boxes[key].currentText()
        self.compensation_boxes[key].setValue(default_amount(role))
        self.visible_boxes[key].blockSignals(True);self.visible_boxes[key].setChecked(role not in NON_PARTICIPATING);self.visible_boxes[key].blockSignals(False)
        if key in self.selectable_boxes:
            self.selectable_boxes[key].blockSignals(True);self.selectable_boxes[key].setChecked(role=='固定RDL');self.selectable_boxes[key].blockSignals(False)
        old=self.motions();old[key]=default_motion(self.roles()[key]);self.case=None
        try:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        except ValueError:pass
        self.loading=True
        try:self.apply_automatic()
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
        self.manual_preview.rebuild()
        self.sync_per_die()
        profile=resource_profile()
        skipped=len(getattr(self.data,'ignored_geometry',[]));repaired=len(getattr(self.data,'repaired_geometry',[]))
        self.statusBar().showMessage(f'{Path(self.data.path).name}；{self.data.count:,}个有效图形；修复{repaired}个，忽略{skipped}个异常图形；{profile["cpu"]}逻辑CPU → 最多{profile["workers"]}工作线程')
    def visibility_changed(self,key):
        if self.loading:return
        self.plot_layout(preserve=True)
    def top_changed(self,name):
        if not self.loading and self.data and name!=self.data.top_name:self.load_gds(self.data.path,name)
    def validate_geometry(self):
        if not self.data:self.error('请先打开GDS');return
        try:
            self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers());self.plot_layout()
            self.statusBar().showMessage(f"几何有效：{len(self.case['dies'])}颗芯片；运动过孔{sum(map(len,self.case['pins']))}个；固定过孔{len(self.case['fixed_via'])}个")
        except Exception as e:self.error(e)
    def reset_plot(self):
        if hasattr(self,'navigation'):self.navigation.cancel_zoom()
        self.stop_animation()
        if hasattr(self,'mouse_die'):self.mouse_die.exit();self.mouse_die.pending=False;self.mouse_die.full_pending=False;self.mouse_die.timer.stop();self.mouse_die.idle.stop()
        if hasattr(self,'selection'):self.selection.reset()
        if hasattr(self,'width_overlay'):self.width_overlay.reset_summary()
        self._legend_stamp=None
        self.clear_space_artists()
        if hasattr(self,'reconstruction_overlay'):self.reconstruction_overlay.hide()
        for artist in list(self.ax.collections)+list(self.ax.texts)+list(self.ax.patches)+list(self.ax.lines):artist.remove()
        if hasattr(self,'scene_renderer'):
            self.scene_renderer.external_draw(None);self.scene_renderer.static_cache.clear();self.scene_renderer.pending_key=None
        self.replacement_artists={};self.layer_artists={};self.die_labels=[];self.issue_artists=[];self.shift_artist=None;self.failure_artist=None;self._artist_frame=None;self.invalid_artists=[]
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
        issues=getattr(self,'display_issues',getattr(self,'filtered_issues',()))
        ignored=sum(bool(issue.get('ignored')) for issue in issues)
        message=('预演已更新' if frame is not None else '名义位置')+f' · 当前 DRC {len(issues)-ignored} 处违规'
        if ignored:message+=f' · 已忽略 {ignored} 处'
        if frame is not None:
            from .statistics import mean_and_bounds
            params=self.params();mu,_=mean_and_bounds(params,len(frame['states']));bounds=np.array([mean_and_bounds(params.for_die(i),len(frame['states']))[1] for i in range(len(frame['states']))])
            if np.any(abs(frame['states']-mu)>bounds+1e-10):message+=' · 手动状态超出输入最大幅度；概率云仍为配置分布'
        if frame and False and 'deformation' in frame:
            q=frame['deformation'];message+=f" · 型变 {q['accepted_count']} 个铜连通区，未解 {len(q['diagnostics'])} 个"
        if frame and frame.get('deformation',{}).get('search_quality')=='interactive':message+=' · 交互候选已复核，停止操作后完整搜索'
        if frame and self.reconstruction_toggle.isChecked():
            ambiguous=(frame.get('reconstruction') or {}).get('summary',{}).get('regions_ambiguous',0)
            if ambiguous:
                message+=f' · {ambiguous} 处手动动区归属不明确，未应用；请缩小框选范围，避开多个 DIE 的共同区域'
            is_low,pose_prob,threshold=self.is_low_probability_pose()
            if is_low:
                message+=f' · 低概率极端（P≈{pose_prob:.3g} ≤ 门限 {threshold:.3g}，已低亮显示）'
        if frame and frame.get('motion_issues'):message+=f" · {len(frame['motion_issues'])}个运动图形归属待确认，已用橙色虚线标记"
        if frame and frame.get('width_analysis') and self.width_toggle.isChecked():
            q=frame['width_analysis']['counts'];message+=f" · 保宽足够 {q['宽度足够']}，需收窄 {q['需局部收窄']}，宽度不足 {q['宽度不足']}"
        if frame and frame.get('width_summary') and self.width_toggle.isChecked():
            s=self._coupled_data if self.coupled_view() else frame['width_summary']
            if s:message+=f" · {SCOPE_NAMES[self.view_settings.evaluation_scope]} {s['package_states']:,} 种姿态，{len(s['lines'])} 条风险线路"
            else:message+=' · 正在核实型变后首行汇总'
        self.log_bus.emit(message)
        if not self._tasks:
            self.statusBar().clearMessage()
        self.refresh_extreme_colors(frame)
    def present_frame(self,frame):
        if self.cut_drag.state is not None:return
        if frame and frame.get('diagnostic_only'):
            from .topology_routing import failure_message
            self.log_bus.emit(failure_message(frame['reconstruction']['hard_failures']),'ERROR')
        if not getattr(self,'reconstruction_pending',False):
            self._applied_preview_settings=self.view_settings
            if hasattr(self,'reconstruction_overlay'):self.reconstruction_overlay.staged_overlay=None
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
        if (not self.view_settings.animation_return_zero and (frame is None or not np.any(frame['states']))) or not self.view_settings.animation_enabled or self.engine is None or self.case is None or (False and frame is not None):
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
            if (False and self.result) or self.show_issues.isChecked() or self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked():
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
        if replay:
            self.show_motion_frame(replay[0][1]);last[0]=0
        self.transition.start(motion['duration'],motion['steps'],update,complete)

    def show_motion_frame(self,frame):
        self._interpolated_states=frame['states'];self.current_frame=frame;self._in_animation=True
        try:self.plot_layout(preserve=True);self.update_state_table(frame['states'])
        finally:self._in_animation=False
    def update_state_table(self,states):
        if self.state_table.rowCount()!=len(states):self.state_table.setRowCount(len(states))
        kind='实测指令' if self.remote_locked else '手动输入' if self.manual_states is not None else '代表极端' if self.active_preview is not None else '名义位置'
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

    def width_filter_changed(self,*_):
        if not self.loading and self.data:
            self.plot_layout(preserve=True)
    def width_changed(self,*_):
        if self.loading:return
        self.enable_settings();self.width_yield.refresh()
        self.filter_changed(pending_ok=True)
        if self.width_toggle.isChecked():self.request_preview()
        else:self.plot_layout(preserve=True)
    def region_mode(self,mode,on):
        if self.loading:return
        if on:
            self.mouse_die.exit();self.navigation.cancel_zoom()
            if self.toolbar.mode:
                if 'pan' in str(self.toolbar.mode).lower():self.toolbar.pan()
                else:self.toolbar.zoom()
            self.tabs.setCurrentIndex(0)
            if not self.reconstruction_toggle.isChecked():self.reconstruction_toggle.setChecked(True)
            self.rulers.exit()
            if self.shown_states is not None and np.any(self.shown_states):self.manual_preview.restore()
            self.region_select.set_mode(mode)
            self.statusBar().showMessage('单击确定框选起点和终点；滚轮旋转，按 Esc 取消')
        else:self.region_select.set_mode(None)

    def exit_region_mode(self):
        if not hasattr(self,'region_select'):return
        self.region_select.set_mode(None)
        for button in (self.region_move,):
            button.blockSignals(True);button.setChecked(False);button.blockSignals(False)

    def defer_reconstruction(self):
        self.reconstruction_pending=True;self.stop_animation();self.cancel_previews()
        self.preview_ticket+=1;self.preview_pending=None
        self.constraint_feedback.cancel()
        if hasattr(self,'reconstruction_yield'):self.reconstruction_yield.reset()
        try:
            self.reconstruction_overlay.stage()
            self.reconstruction_overlay.draw();self.selection.draw(redraw=False);self.render_scene()
        except Exception as exc:self.log_bus.emit(f'待计算动区预览未生成：{exc}')
        self.statusBar().showMessage('重构修改待应用；已标记调整图形，点击“重构计算”统一计算。')

    def _effective_deleted_shapes(self):
        if self._staged_deletions is not None:return tuple(self._staged_deletions)
        return tuple(getattr(self.data,'deleted_shapes',()))

    def _effective_node_edits(self):
        base=getattr(self.data,'_area_base',self.data)
        return tuple(getattr(base,'node_edits',())) if self._staged_node_edits is None else self._staged_node_edits

    def _stage_history_nodes(self,rows):
        actual={(q['layer'],q['index']):q for q in getattr(getattr(self.data,'_area_base',self.data),'node_edits',())}
        target={(q['layer'],q['index']):q for q in rows}
        changed={ref for ref in actual.keys() | target.keys() if actual.get(ref)!=target.get(ref)}
        self._staged_node_edits=tuple(rows) if changed else None
        self.pending_node_shapes={ref:self._shape_marker_geometry(*ref) for ref in changed}

    def _can_defer_deletion_edit(self):
        return bool(self.data is not None and self.reconstruction_toggle.isChecked() and not self.view_settings.reconstruction_auto)

    def _has_pending_deletion_changes(self):
        if self._staged_deletions is None:return False
        return tuple(self._staged_deletions)!=tuple(getattr(self.data,'deleted_shapes',())) or bool(self.pending_layout_ops)

    def _clear_pending_layout_markers(self):
        self.pending_deleted_shapes={};self.pending_restored_shapes={}
        self._staged_deletions=None;self.pending_layout_ops=[];self._staged_restored_edits=None
        self._staged_node_edits=None;self.pending_node_shapes={}

    def _shape_marker_geometry(self,layer,raw_index):
        original=self.data
        indices=getattr(original,'area_indices',{}).get(layer,range(len(original.layers.get(layer,()))))
        if raw_index in indices:
            index=indices.index(raw_index)
            moved=next((row['geometry'] for row in self.selection.replacements().get(layer,()) if index in row['indices']),None)
            if moved is None:
                cache=self.geometry_cache or GeometryCache(original)
                moved=cache.layers[layer]['geometry'][index]
                model=getattr(self.engine,'contact',None)
                if model is not None and (node:=model.ids.get((layer,index))) is not None and self.shown_states is not None:
                    moved=model.moved(moved,model.nodes[node]['owner'],self.shown_states)
            return moved
        from .gds_merge import filled_polygon
        source=getattr(original,'_area_base',original)
        layers=source.layers.get(layer,())
        if 0<=raw_index<len(layers):
            return filled_polygon(layers[raw_index],True)
        return shapely.GeometryCollection()

    def _merge_staged_restored_edits(self,project):
        staged=self._staged_restored_edits
        if not staged:return project
        edits,width=staged
        cfg=project.setdefault('settings',{})
        for name,rows in edits.items():
            if not rows:continue
            existing=list(cfg.get(name,()))
            seen=set(existing)
            for row in rows:
                if row not in seen:existing.append(row);seen.add(row)
            cfg[name]=existing
        if width:
            refs=project.setdefault('ignored_width_lines',dict(sha256=self.data.sha256,top_cell=self.data.top_name,refs=[]))
            refs['refs']=sorted({tuple(r) for r in refs.get('refs',())} | {tuple(r) for r in width})
        if 'reconstruction_ignored' in cfg:
            project.setdefault('ignored_reconstruction',dict(sha256=self.data.sha256,top_cell=self.data.top_name,refs=[]))['refs']=[list(r) for r in cfg['reconstruction_ignored']]
        return project

    def _replay_pending_deletions(self):
        """Apply queued delete/restore operations in exact order to verify net target."""
        actual={tuple(q[:2]):q[2] for q in getattr(self.data,'deleted_shapes',())}
        if self._staged_deletions is not None:
            for item in self._staged_deletions:actual.setdefault(tuple(item[:2]),item[2])
        rows={tuple(q[:2]):q[2] for q in getattr(self.data,'deleted_shapes',())}
        for op,items in self.pending_layout_ops:
            if op=='delete':
                for ref,fp in items:rows[tuple(ref)]=fp
            elif op=='restore':
                for ref in items:rows.pop(tuple(ref),None)
        if self._staged_deletions is not None:
            return list(self._staged_deletions)
        return [(*ref,rows[ref]) for ref in sorted(rows)]

    def _sync_staged_deletion_markers(self,target_deletions):
        actual={tuple(q[:2]) for q in getattr(self.data,'deleted_shapes',())}
        target={tuple(q[:2]) for q in target_deletions}
        prev_del=self.pending_deleted_shapes;prev_res=self.pending_restored_shapes
        self.pending_deleted_shapes={ref:(prev_del[ref] if ref in prev_del else self._shape_marker_geometry(*ref)) for ref in sorted(target-actual)}
        self.pending_restored_shapes={ref:(prev_res[ref] if ref in prev_res else self._shape_marker_geometry(*ref)) for ref in sorted(actual-target)}
        self._staged_deletions=tuple(target_deletions) if (target!=actual or self.pending_layout_ops) else None

    def compute_reconstruction(self):
        if self.remote_locked or self.data is None:return
        if self.worker is not None:
            self.statusBar().showMessage('上一项操作尚未完成，请稍候。');return
        if self._has_pending_deletion_changes() or self._staged_node_edits is not None:
            from .area_filter import canonical_project
            target_deletions=self._replay_pending_deletions()
            actual_deletions=list(getattr(self.data,'deleted_shapes',()))
            if tuple(target_deletions)!=tuple(actual_deletions) or self._staged_node_edits is not None:
                project=canonical_project(self.data,self.project_settings())
                project=self._merge_staged_restored_edits(project)
                project['deleted_shapes']=target_deletions
                project['node_edits']=list(self._effective_node_edits())
                project.setdefault('overlays',{})['reconstruction']=True
                pose=None if self.manual_states is None else tuple(map(tuple,self.manual_states))
                extreme=self.active_preview
                self.apply_layout_edit(project,commit=lambda:None,pose=pose,extreme=extreme,force_reconstruct=True)
                return
        self._clear_pending_layout_markers()
        self.reconstruction_pending=False;self._reconstruction_apply_once=True
        if hasattr(self,'reconstruction_overlay'):
            self.reconstruction_overlay.staged_overlay=None
            self.reconstruction_overlay.draft_frame=self.reconstruction_overlay.draft_data=self.reconstruction_overlay.draft_key=None
        if not self.reconstruction_toggle.isChecked():
            self.reconstruction_toggle.blockSignals(True);self.reconstruction_toggle.setChecked(True);self.reconstruction_toggle.blockSignals(False)
        self.invalidate_reconstruction_edit();self.request_preview()

    def change_moving_pad_mode(self,updated):
        # Apply this control immediately while preserving unrelated staged edits.
        from .reconstruction_edits import retire_pad_intervals
        solver=None if getattr(self.engine,'busy',False) else getattr(self.engine,'reconstructor',None)
        pieces=(self.current_frame or {}).get('reconstruction',{}).get('pieces',())
        self.view_settings=retire_pad_intervals(updated,solver,pieces)
        self.enable_settings()
        if not self.reconstruction_toggle.isChecked() or self.data is None:return
        if self.cut_drag.state:self.cut_drag.cancel()
        self.cut_drag.retained=None
        self.reconstruction_overlay.draft_frame=self.reconstruction_overlay.draft_data=self.reconstruction_overlay.draft_key=None
        self.reconstruction_overlay.clear_hover();self.reconstruction_overlay.draw();self.render_scene()
        applied=self._applied_preview_settings
        if self.reconstruction_pending and applied is not None:
            self._applied_preview_settings=retire_pad_intervals(replace(
                applied,reconstruction_allow_moving_pad=updated.reconstruction_allow_moving_pad),solver,pieces)
        self._reconstruction_apply_once=True
        self.invalidate_reconstruction_edit()
        self.request_preview(quiet=True,pose_change=True)

    def invalidate_reconstruction_edit(self):
        """Reject old coupled/extreme evidence after a reconstruction edit."""
        if not self.view_settings.reconstruction_auto and not getattr(self,"_reconstruction_apply_once",False):
            self.defer_reconstruction();return
        self.cancel_previews()
        if self.motion_worker:self.motion_worker.cancelled.set()
        if hasattr(self,"reconstruction_yield"):self.reconstruction_yield.refresh()
        self._coupled_key=None;self._coupled_data=None;self._coupled_error=False
        self._problem_tag=None
        if self.filter_job:self.filter_job.cancelled.set()
        self.refresh_extreme_colors()
        self.filter_changed(pending_ok=True)

    def clear_manual_regions(self,*_):
        self.exit_region_mode()
        if not self.view_settings.reconstruction_regions:return
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,reconstruction_regions=())
        self.invalidate_reconstruction_edit();self.request_preview()

    def nudge_selected_cut(self,step):
        piece=self._selected_reconstruction_piece()
        if piece is None:
            self.statusBar().showMessage('先在图中选择一条重构边界');return
        from .reconstruction_overlay import nudge_cut
        kind=getattr(self.reconstruction_overlay,'selected_kind',None) or 'cut'
        updated=nudge_cut(self.view_settings,piece,step,kind)
        if updated.reconstruction_key()==self.view_settings.reconstruction_key():
            self.statusBar().showMessage('当前切口不能继续移动');return
        self.edit_history.record()
        self.view_settings=updated;self.invalidate_reconstruction_edit();self.request_preview(quiet=True)

    def clear_manual_cuts(self,*_):
        extras=getattr(self.view_settings,'reconstruction_extra_cuts',())
        dropped=getattr(self.view_settings,'reconstruction_dropped_cuts',())
        if not any((self.view_settings.reconstruction_cuts,extras,dropped,self.view_settings.reconstruction_second_cuts,self.view_settings.reconstruction_intervals,self.view_settings.reconstruction_angles,self.view_settings.reconstruction_deleted,self.view_settings.reconstruction_added,self.view_settings.reconstruction_boxes,self.view_settings.reconstruction_angle_boxes,self.view_settings.reconstruction_between_overrides)):return
        from dataclasses import replace
        # Also restores the automatic cuts that were deleted, so one button undoes
        # every hand edit of the cut lines.
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,reconstruction_cuts=(),
                                   reconstruction_extra_cuts=(),reconstruction_second_cuts=(),reconstruction_dropped_cuts=(),reconstruction_intervals=(),reconstruction_angles=(),reconstruction_deleted=(),reconstruction_added=(),reconstruction_forced=(),reconstruction_boxes=(),reconstruction_angle_boxes=(),reconstruction_box_order=(),reconstruction_box_starts=(),reconstruction_box_excluded=(),reconstruction_between_overrides=())
        self.invalidate_reconstruction_edit()
        self.statusBar().showMessage('已清除全部手动切口，并恢复被删除的自动切口')
        self.request_preview()

    def _selected_reconstruction_piece(self):
        """Source-bound piece under the boundary the user last clicked."""
        overlay=getattr(self,'reconstruction_overlay',None)
        piece=None
        if overlay is not None:
            piece=overlay.piece_for(getattr(overlay,'selected',None))
        return piece



    def delete_selected_cut(self,*_):
        """Delete the manual cuts of the selected source-bound chain."""
        piece=self._selected_reconstruction_piece()
        if piece is None:
            self.statusBar().showMessage('先在图中选中重构边界');return
        from .reconstruction import normalize_via_refs,cut_identity_matches
        refs=normalize_via_refs(piece.get('via_refs') or piece['vias'])
        def matches(row):
            if str(row[0])!=str(piece['layer']) or int(row[1])!=int(piece['index']):return False
            return cut_identity_matches(row[2],refs)
        names=('reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts')
        changes={name:tuple(row for row in getattr(self.view_settings,name) if not matches(row)) for name in names}
        manual=any(changes[name]!=getattr(self.view_settings,name) for name in names)
        dropped=tuple(row for row in self.view_settings.reconstruction_dropped_cuts if not matches(row))
        changes['reconstruction_intervals']=tuple(r for r in self.view_settings.reconstruction_intervals if (r[0],r[1],r[2],r[4])!=(piece['layer'],piece['index'],piece['die'],piece.get('route_identity')))
        manual=manual or changes['reconstruction_intervals']!=self.view_settings.reconstruction_intervals
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,**changes,reconstruction_dropped_cuts=dropped)
        self.invalidate_reconstruction_edit()
        self.statusBar().showMessage('已恢复自动切口' if manual else '已删除自动切口；清除全部手动切口可恢复')
        self.request_preview()

    def reconstruction_changed(self,*_):
        if self.loading:return
        self.reconstruction_yield.refresh();self.constraint_feedback.cancel()
        if not self.reconstruction_toggle.isChecked():
            self.exit_region_mode();self.cut_drag.cancel();self.cancel_previews();self.preview_ticket+=1;self.preview_pending=None
            self.reconstruction_overlay.hide()
        self.filter_changed(pending_ok=True)
        if self.reconstruction_toggle.isChecked():
            self.statusBar().showMessage('重构预演：建立原图共用动区，核对全部离散极端；两端可独立拖动…')
            self.request_preview(pose_change=True)
        else:
            # Refresh the displayed frame after removing the reconstruction overlay.
            if self.result is not None:self.request_preview()
            else:self.plot_layout(preserve=True)
    def escape_action(self):
        if hasattr(self,'rulers') and self.rulers.exit():return
        if hasattr(self,'cut_drag') and self.cut_drag.cancel():return
        self.exit_region_mode()
        if hasattr(self,'navigation'):self.navigation.cancel_zoom()
        if hasattr(self,'mouse_die') and self.mouse_die.exit():return
        self.selection.clear()
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
        if not self._plotting and self.data and self.tabs.currentIndex()==0 and not self.viewport_timer.isActive():self.viewport_timer.start()
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

    def draw_contact_ranges(self):
        if not self.zone_artists:
            for _,_,_,color,alpha in RANGES:
                artist=LineCollection([],colors=color,alpha=alpha,linestyles=(0,(4,3)),linewidths=.85,zorder=7)
                self.ax.add_collection(artist,autolim=False);self.zone_artists.append(artist)
            self._contact_ranges=None
        regions=self.current_frame['contact']['regions'] if self.current_frame else None
        if not regions:
            self._contact_ranges=None
            for artist in self.zone_artists:
                if artist.get_visible():artist.set_segments([]);artist.set_visible(False)
            return
        if not (self.view_settings.show_annulus_range or self.view_settings.show_spacing_range):
            for artist in self.zone_artists:artist.set_visible(False)
            return
        if self._contact_ranges is None or self._contact_ranges.regions is not regions:self._contact_ranges=ContactRanges(regions)
        self._contact_ranges.draw(self.zone_artists,self.view_settings,{k for k,c in self.visible_boxes.items() if c.isChecked()},self.view_bounds)

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
        bounds=expanded_bounds(self.ax.get_xlim(),self.ax.get_ylim(),0.)
        previous=getattr(self,'view_bounds',None)
        if not (preserve and previous and previous[0]<=bounds[0]<=bounds[2]<=previous[2]
                and previous[1]<=bounds[1]<=bounds[3]<=previous[3]
                and previous[2]-previous[0]<=2*(bounds[2]-bounds[0])
                and previous[3]-previous[1]<=2*(bounds[3]-bounds[1])):
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
        reconstruction=self.reconstruction_overlay.data() if self.reconstruction_toggle.isChecked() else None
        for rank,key in enumerate(order):
            values=frame['layers'][key]['polygons'] if frame and key in frame['layers'] else self.data.layers[key]
            role=roles[key];sty=self.style_for(key,role);visible=self.visible_boxes[key].isChecked()
            polys=values
            ids=self.viewport_index.query(key,self.view_bounds,states) if visible else np.empty(0,int)
            replacements=(reconstruction or {}).get('replacements',{}).get(key,())
            hidden={i for q in replacements for i in q['indices']}
            if hidden:ids=np.array([i for i in ids if i not in hidden],int)
            mode=modes.get(role,modes.get('其他','show'))
            if mode!='show':
                rgb=tint(sty['color'],mode,sty['alpha']);sty=dict(sty,color=rgb[:3],alpha=rgb[3])
            visible=visible and mode!='hide'
            if key not in self.layer_artists:
                self.layer_artists[key]=PolyCollection([]);self.ax.add_collection(self.layer_artists[key])
            artist=self.layer_artists[key]
            geometry_stamp=(id(values),ids.tobytes(),visible)
            if getattr(artist,'_geometry_stamp',None)!=geometry_stamp:
                artist.set_verts([polys[i] for i in ids] if visible else []);artist._visible_ids=geometry_stamp[1];artist._geometry_stamp=geometry_stamp
            zorder=2+rank/max(1,len(order)) if role in PHYSICAL else sty['z']
            paint=(sty['color'],sty['fill'],sty['alpha'],sty['width'],sty.get('linestyle','-'),visible,zorder)
            if getattr(artist,'_paint_stamp',None)!=paint:
                artist.set_facecolor(sty['color'] if sty['fill'] else 'none');artist.set_edgecolor(sty['color'] if not sty['fill'] else 'none')
                artist.set_alpha(sty['alpha']);artist.set_linewidth(sty['width']);artist.set_linestyle(paint[4]);artist.set_visible(visible)
                artist.set_zorder(zorder);artist._paint_stamp=paint
            if default_motion(role)=='fixed' or frame is None or frame.get('nominal_global'):
                artist._static_preview=(geometry_stamp,str(sty),artist.get_zorder())
            elif hasattr(artist,'_static_preview'):del artist._static_preview
            if visible:self.rendered_layers[key]=polys
        self._artist_frame=stamp
        visible_layers=tuple(key for key,box in self.visible_boxes.items() if box.isChecked())
        overlay_stamp=(id(self.data), id(frame), visible_layers)
        if overlay_stamp!=getattr(self,'_layout_overlay_stamp',None):
            for artist in self.invalid_artists:artist.remove()
            self.invalid_artists=[]
            for q in getattr(self.data,'ignored_geometry',[]):
                if q['layer'] not in visible_layers:continue
                x0,y0,x1,y1=q['bounds'];pad=max(1.,self.data.dbu_um*2)
                path=np.array([[x0-pad,y0-pad],[x1+pad,y0-pad],[x1+pad,y1+pad],[x0-pad,y1+pad],[x0-pad,y0-pad]])
                artist=LineCollection([path],colors='#d72b91',linestyles=(0,(3,3)),linewidths=.7,zorder=13)
                self.ax.add_collection(artist);self.invalid_artists.append(artist)
            self.update_connector_segments()
            self._layout_overlay_stamp=overlay_stamp
        if self.shift_artist is None:self.shift_artist=LineCollection([],colors='#d9893e',linestyles=(0,(4,3)),linewidths=1.05,zorder=8);self.ax.add_collection(self.shift_artist)
        if getattr(self.shift_artist,'_connector_data',None) is not self.connector_segments:
            self.shift_artist.set_segments(self.connector_segments);self.shift_artist._connector_data=self.connector_segments
        self.draw_contact_ranges();self.fill_issues()
        self.drc_overlay.draw()
        self.draw_probability();self.draw_space()
        self.footprint_overlay.draw()
        if hasattr(self,'reconstruction_overlay'):self.reconstruction_overlay.draw()
        if not getattr(self,'_in_animation',False):
            self.fill_issues()
        if self.case:
            if len(self.die_labels)!=len(self.case['dies']):
                for label_ in self.die_labels:label_.remove()
                self.die_labels=[self.ax.text(0,0,f'DIE{i+1}',ha='center',va='center',fontsize=10,color='#3c5169',weight='medium',zorder=12,clip_on=True) for i in range(len(self.case['dies']))]
            for i,(label_,info) in enumerate(zip(self.die_labels,self.case['infos'])):
                point=np.array([info['cx'],info['cy']]);point=rigid_transform(point,self.rotation_centers()[i],self.shown_states[i]) if self.shown_states is not None else point;label_.set_position(point)
        if not preserve or self._default_view is None:self.fit_plot()
        self.refresh_legend();self.update_die_label_visibility()
        if hasattr(self,'width_overlay'):self.width_overlay.draw()
        if not getattr(self,'_in_animation',False) and not getattr(self,'_in_viewport',False):
            for i,key in enumerate(self._layer_keys):
                sty=self.style_for(key);item=self.layer_table.item(i,2)
                if item:
                    background=QtGui.QColor(sty['color']).lighter(125);item.setForeground(QtGui.QColor('#ffffff' if background.lightnessF()<.55 else '#33465a'));item.setBackground(background);item.setToolTip(sty['label'])
                self.role_boxes[key].setToolTip(roles[key])
        if hasattr(self,'selection'):self.selection.draw(redraw=False)
        if hasattr(self,'mouse_die'):self.mouse_die.update_text()
        from .matplotlib_diagnostic import draw as draw_diagnostic
        draw_diagnostic(self)
        if hasattr(self,'rulers'):self.rulers.draw(redraw=False)
        self.render_scene()
    def render_scene(self):
        if hasattr(self,'scene_renderer'):
            key=(tuple(self.ax.get_xlim()),tuple(self.ax.get_ylim()),tuple(self.ax.bbox.bounds),self._legend_stamp,self.space_stamp,
                 self.legend.get_visible() if self.legend is not None else False,tuple(a.get_visible() for a in self.space_artists))
            self.scene_renderer.draw(key,self.legend)
            if not self.selection.selected and self.selection.selected_between is None:
                self.scene_renderer.unselected_key=self.scene_renderer.selection_key(self.legend)
        else:self.canvas.draw_idle()
    def update_die_label_visibility(self,*_):
        if not self._default_view:return
        zoom=min(np.ptp(self.ax.get_xlim())/np.ptp(self._default_view[0]),np.ptp(self.ax.get_ylim())/np.ptp(self._default_view[1]))
        visible=zoom>=.995 and any(self.visible_boxes[k].isChecked() and k not in getattr(self,'width_hidden_sources',()) for k,r in self.roles().items() if r=='芯片边框')
        for label_ in self.die_labels:label_.set_visible(visible)
        if self.legend is not None:self.legend.set_visible(visible)
    def refresh_legend(self):
        stamp=(tuple((k,self.visible_boxes[k].isChecked(),str(self.style_for(k))) for k in self.ordered_layers()),
               self.view_settings.show_annulus_range,self.view_settings.show_spacing_range,self.show_issues.isChecked(),self.current_frame is not None,(self.current_frame or {}).get('width_summary') is not None,self.target_sigma.currentText(),False,self.probability_toggle.isChecked(),id(self.probability_data),self.view_settings.probability_cmap,self.view_settings.probability_alpha,self.view_settings.probability_gamma,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.view_settings.contact_marker_color,self.view_settings.unadapted_marker_color,self.width_toggle.isChecked(),self.width_sufficient_toggle.isChecked(),self.ignored_width_lines,self.reconstruction_toggle.isChecked(),id(self.reconstruction_yield.evidence),bool(self.current_frame and self.current_frame.get('motion_issues')))
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
        if self.width_toggle.isChecked():
            modes=self.view_settings.width_display
            if self.ignored_width_lines and modes['描边']!='hide':handles.append(Patch(facecolor='#95c8a5',edgecolor='#95c8a5',alpha=.6,label='已忽略的宽度问题'))
            for key,label,color in [('运动过孔孔盘','运动孔盘','#589881'),('固定过孔孔盘','固定孔盘','#6985a5'),('固定障碍','固定障碍','#85848e'),('边距','边缘或禁布区违规','#c44956')]:
                if modes[key]!='hide':handles.append(Patch(facecolor=tint(color,modes[key],.65),label=label))
            if (self.current_frame or {}).get('width_summary') is not None and modes['描边']!='hide':
                binary=self.params().target_nsigma in (0.,math.inf)
                handles.extend([Line2D([],[],color='#af7780',lw=1.2,label='宽度不足 · 选中查看工况'),Line2D([],[],color='#bf9c71',lw=1.2,label='需局部收窄')])
                if not binary:handles.extend([Line2D([],[],color='#7c8492',label='已证超限'),Line2D([],[],color='#aab0bb',ls='--',label='概率待定')])
            elif modes['截面']!='hide':
                for title,color in [('宽度不足','#c44956'),('需局部收窄','#bd8127'),('宽度足够','#498d79')]:
                    if title!='宽度足够' or self.width_sufficient_toggle.isChecked():
                        handles.append(Patch(facecolor='none',edgecolor=tint(color,modes['截面']),label=title))
        if self.reconstruction_toggle.isChecked():
            from .reconstruction_overlay import CUT_COLOR,SECOND_CUT_COLOR
            from .reconstruction_events import PALETTE
            handles += [Patch(facecolor=PALETTE[k],alpha=.66,label=title) for k,title in [('red','动区连线失败'),('yellow','动区存在 DRC 违例'),('green','动区通过检查'),('pending','动区待核验')]]
            handles.append(Patch(facecolor='#c9b3eb',alpha=.45,label='忽略重构 · 随动'))
            handles += [Line2D([],[],color=CUT_COLOR,lw=2,label='运动侧边界 / 整孔盘蓝点'),Line2D([],[],color=SECOND_CUT_COLOR,lw=2,label='固定侧边界 / 整孔盘蓝点')]
        if self.current_frame and self.current_frame.get('motion_issues'):handles.append(Line2D([],[],color='#bf862c',ls='--',label='运动归属未定：保持名义位置'))
        if getattr(self.data,'ignored_geometry',[]):handles.append(Line2D([],[],color='#d72b91',ls='--',lw=.7,label='忽略的无效图形'))
        if self.current_frame is not None:
            for _,option,title,color,alpha in RANGES:
                if getattr(self.view_settings,option):handles.append(Line2D([],[],color=color,alpha=alpha,ls='--',label=title))
            if self.show_issues.isChecked():handles.append(Line2D([],[],color=self.view_settings.contact_marker_color,marker='o',markerfacecolor='none',markersize=4,alpha=.85,ls='none',label='当前 DRC 位置'))
        binary=bool(self.probability_data and self.probability_data.get('binary'))
        if self.probability_toggle.isChecked() and binary:
            handles.append(Patch(facecolor=self.probability_colormap()(1.),alpha=self.view_settings.probability_alpha,label='当前姿态过孔实体覆盖 100%'))
        if self.probability_toggle.isChecked() and self.probability_data and not binary:
            handles.append(Line2D([],[],color='#677a9a80',ls='--',lw=.65,label='离散极端实体足迹'))
            maximum=max((float(q['probability'].max()) for q in self.probability_data['maps']),default=0)
            norm=matplotlib.colors.PowerNorm(self.view_settings.probability_gamma,0,1)
            for value,title in ((.001,'覆盖概率0.1%（低概率已增强）'),(maximum,f'覆盖概率最大{maximum:.1%}')):
                rgba=list(self.probability_colormap()(norm(value)));rgba[3]*=self.view_settings.probability_alpha
                handles.append(Patch(facecolor=rgba,label=title))
        if self.probability_toggle.isChecked() and self.probability_data and not binary and self.view_settings.probability_show_ranges:
            levels=dict(self.view_settings.probability_ranges);levels[self.params().target_nsigma]=self.view_settings.probability_target_color
            for level,color in levels.items():
                name='全部输入范围' if level==0 else '∞ 名义位置' if math.isinf(level) else f'{level:g}σ 有界扫掠包络'
                handles.append(Line2D([],[],color=color,ls='--',lw=1.25,label=name))
        self.legend=self.ax.legend(handles=handles,loc='upper right',fontsize=7,framealpha=1,ncol=1)
    def draw_binary_coverage(self,active,stamp):
        from matplotlib.transforms import Affine2D
        if stamp!=self.probability_stamp:
            for artist in self.probability_artists:artist.remove()
            self.probability_artists=[];self.probability_stamp=stamp
            groups={}
            for row in self.probability_data['maps']:
                groups.setdefault((row['key'],row['owner']),[]).append(row)
            for (key,owner),rows in groups.items():
                artist=PatchCollection([],facecolors=[self.probability_colormap()(1.)],edgecolors='none',
                                       alpha=self.view_settings.probability_alpha,zorder=4)
                artist._via_layer=key;artist._owner=owner;artist._pivot=rows[0]['rotation_center']
                artist._polys={q['index']:q['nominal_polygon'] for q in rows};artist._ids=None
                self.ax.add_collection(artist);self.probability_artists.append(artist)
        states=self.shown_states
        for a in self.probability_artists:
            visible=active and self.visible_boxes[a._via_layer].isChecked()
            a.set_visible(visible)
            if not visible:continue
            ids=tuple(i for i in self.viewport_index.query(a._via_layer,self.view_bounds,states) if i in a._polys)
            if ids!=a._ids:
                a.set_paths([self.geometry_patch(a._polys[i]) for i in ids]);a._ids=ids
            dx,dy,angle=states[a._owner] if states is not None else (0.,0.,0.)
            a.set_transform(Affine2D().rotate_around(*a._pivot,angle).translate(dx,dy)+self.ax.transData)

    def draw_probability(self):
        active=self.probability_toggle.isChecked() and self.probability_data is not None
        stamp=(id(self.probability_data),bool(active),self.view_settings.probability_alpha,self.view_settings.probability_cmap,self.view_settings.probability_gamma,self.view_settings.probability_interpolation,self.view_settings.probability_show_ranges,self.view_settings.probability_ranges,self.view_settings.probability_target_color,self.target_sigma.currentText())
        if active and self.probability_data.get('binary'):
            self.draw_binary_coverage(active,stamp);return
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
    def footprint_changed(self,*_):
        if self.loading:return
        if self.footprint_toggle.isChecked():self.request_preview()
        else:self.plot_layout(preserve=True)
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
                self.space_colorbar.ax.text(-.04,.5,'避让空间 · 所需线距 / μm',transform=self.space_colorbar.ax.transAxes,ha='right',va='center',fontsize=7,clip_on=False)
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
                    def compute_field():return package_map(self.space_data,self.case['board'],centers,self.shown_states,None,resolution)
                    if prepared is not None and prepared[0]==full_key:full=prepared[1]
                    elif getattr(self,'_in_animation',False):
                        full=self.space_motion_fields.get(cache_key)
                        if full is None:
                            full=compute_field();self.space_motion_fields.put(cache_key,full)
                    else:full=self.session_values.get_or_compute(cache_key,compute_field)
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
    def leave_extreme_selection(self):
        # Clearing the highlight alone leaves row 0 current in Qt. A click on
        # that already-current row would never deliver currentCellChanged.
        table=self.extreme_table
        blocked=table.blockSignals(True)
        table.clearSelection();table.setCurrentCell(-1,-1);table.blockSignals(blocked)

    def apply_manual(self,*_):
        self.manual_preview.preview()
    def cancel_previews(self):
        for worker in self.preview_workers:worker.cancelled.set()
    def request_preview(self,direct=False,settle=False,quiet=False,pose_change=False):
        if getattr(self.cut_drag,'state',None) is not None:return
        if getattr(self, 'reconstruction_pending', False) and self.reconstruction_toggle.isChecked() and not pose_change:
            return
        if self.remote_locked or getattr(self,'requested_measured',False):return
        self._preview_quiet=bool(quiet)
        if not self.data or getattr(self,'_restoring_project',False):return
        if self.result is None and (self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked()):
            self.log_bus.emit('分析需要完整方案评估，先建立全部极端情况。')
            if self.worker is not None:
                self._evaluation_requested=True;self._coverage_pending=True;self.coverage_timer.start()
            else:self.run_evaluation(automatic=True)
            return
        self._reconstruction_apply_once=False
        settings=self.preview_settings()
        if not getattr(self, 'reconstruction_pending', False):
            self._applied_preview_settings=settings
            self.schedule_coupled_filter()
        self.stop_animation()
        self.preview_ticket+=1;ticket=self.preview_ticket;self._prepared_motion=None
        # Committed extreme poses finish into cache even if a later pose wins
        # the display ticket. Only transient mouse-drag refinements are stale.
        for running in self.preview_workers:
            if getattr(running,'transient',False):running.cancelled.set()
        state=self.manual_states if self.manual_states is not None else (self.extremes[self.active_preview]['states'].copy() if self.active_preview is not None and self.extremes else None)
        overlays=(self.result is not None or self.footprint_toggle.isChecked() or self.probability_toggle.isChecked() or self.space_toggle.isChecked() or self.show_issues.isChecked() or self.width_toggle.isChecked()
                  or self.reconstruction_toggle.isChecked() or (False and self.result is not None))
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
        signature=self.preview_identity(self.data,roles,self.ordered_layers(),self.motions(),p,self.result)
        deform=False;drc=True
        width=settings.width_key() if self.width_toggle.isChecked() else (False,)
        recon=settings.reconstruction_key() if self.reconstruction_toggle.isChecked() else (False,)
        expected=self.active_frame_key()
        if show_frame:
            store=self.cache_group.frame_stores.get(signature)
            cached=store.get((expected,state.tobytes(),False)) if store is not None else None
        else:
            key=(signature,deform,settings.deformation_key(),(drc,settings.drc_min_angle_deg,settings.drc_tolerance_um),settings.numeric_key(),width,recon)
            cached=self.cache_group.nominal.get(key)
        ready=(not self.probability_toggle.isChecked() or self.probability_data is not None) and (not self.space_toggle.isChecked() or self.space_data is not None)
        ready=ready and (not self.footprint_toggle.isChecked() or math.isinf(p.target_nsigma) or self.footprint_data is not None)
        if width[0] and self.extremes is not None and self._width_grid_summary is None:ready=False
        global_ready=show_frame or not (deform and self.result and math.isfinite(p.target_nsigma)) or bool(cached and cached.get('global_deformation'))
        matches=[e for e in self.session_engines.values() if getattr(e,'signature',None)==signature]
        actual=next((e for e in matches if not getattr(e,'busy',False) and e.cache_key()==expected),next(iter(matches),None))
        if cached is not None and actual is not None and not getattr(actual,'busy',False) and ready and global_ready:
            actual.configure_deformation(deform,settings);actual.configure_drc(drc,settings)
            actual.configure_width(self.width_toggle.isChecked(),settings);actual.configure_reconstruction(self.reconstruction_toggle.isChecked(),settings)
            self.engine=actual
            animate=settings.animation_enabled and not direct and not np.array_equal(origin,state)
            prepared=animate and (np.any(state) or animate_back)
            target=cached if show_frame or overlays else None
            if prepared:
                from .prepared_motion import motion_packet
                from types import SimpleNamespace
                view=SimpleNamespace(signature=signature,cache_key=lambda:expected,bound=actual.bound)
                _,_,duration,steps,requested,_,packet=motion_packet(view,origin,state,settings,max(np.ptp(self.ax.get_xlim()),np.ptp(self.ax.get_ylim())),self.cache_group.motions)
                if packet and packet['complete']:
                    motion=dict(prepared=True,streamed=False,frames=packet['frames'],duration=duration,steps=steps,requested_steps=requested,cached=True,count=len(packet['frames']))
                    self._prepared_motion=motion;self.preview_pending=None;self.play_prepared_motion(target,motion);return
                self.statusBar().showMessage('目标状态已缓存，正在准备首次过渡动画。')
            else:
                self.preview_pending=None
                if direct or not show_frame or getattr(actual,'busy',False) or actual.cache_key()!=expected:
                    self._interpolated_states=None;self.current_frame=target;self.plot_layout(preserve=True)
                    self.update_state_table(state);self.update_histograms();self.show_frame_status(target)
                else:self.present_frame(target)
                return
        self.preview_pending=(self.revision,settings,ticket,state.copy(),self.motions(),roles,p,self.result,case,self.geometry_cache,self.connections,self.ordered_layers(),show_frame,self.probability_toggle.isChecked(),self.space_toggle.isChecked())
        self.preview_pending+=(False,True,direct,settle,origin,self.width_toggle.isChecked(),self.reconstruction_toggle.isChecked(),self.footprint_toggle.isChecked())
        self.statusBar().showMessage('正在后台生成预演…保留已显示状态。');self.launch_preview()
    def launch_preview(self):
        if self._closing or self.motion_worker is not None or self.preview_pending is None:return
        request=self.preview_pending
        request_key=(self.preview_identity(self.data,request[5],request[11],request[4],request[6],request[7]),request[1].deformation_key(),request[1].numeric_key(),request[1].reconstruction_key(),request[1].width_key(),request[1].drc_tolerance_um,request[3].tobytes(),request[12:19],request[19].tobytes(),request[20:],request[1].animation_enabled,request[1].animation_return_zero,request[1].animation_strategy,request[1].animation_speed,request[1].animation_fps,request[1].animation_duration_ms)
        for running in self.preview_workers:
            if getattr(running,'request_key',None)==request_key and not running.cancelled.is_set() and not getattr(running,'delivered',False) and not running.isFinished():
                # The identical in-flight result can answer the latest display ticket.
                running.view_ticket=request[2];self.preview_pending=None;return
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
        revision,settings,ticket,states,motions,roles,params,result,case,cache,connections,order,show_frame,heat,space,deform,drc,direct,settle,origin,width_analysis,reconstruction_view,footprint_view=self.preview_pending;self.preview_pending=None;data=self.data
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
            footprints=None
            if footprint_view and not math.isinf(params.target_nsigma):
                from .extreme_footprints import extreme_footprints
                footprints=session_values.get_or_compute(('extreme-footprints-v26',signature),lambda:extreme_footprints(actual.contact,params,progress))
            distribution=None;field=None
            if heat:distribution=session_values.get_or_compute(('probability-v28',signature,() if math.isinf(params.target_nsigma) else settings.numeric_key()),lambda:probability_maps(actual_cache,actual.bound,roles,params,masks=actual.contact.interface_masks,settings=settings,progress=progress))
            if space:field=session_values.get_or_compute(('space',signature,settings.space_resolution),lambda:space_maps(actual.contact,params,resolution=settings.space_resolution))
            prepared=None
            if space and field is not None and settings.space_scope=='package':
                from .space_map import package_map
                key=(id(field),states.tobytes() if show_frame else None,settings.space_resolution)
                prepared=(key,session_values.get_or_compute(('package',signature,key[1],settings.space_resolution),lambda:package_map(field,case['board'],actual.bound.centers,states,None,settings.space_resolution)))
            motion=None
            back=bool(not direct and settings.animation_enabled and settings.animation_return_zero and not np.any(states) and np.any(start_states))
            nominal_key=(signature,deform,settings.deformation_key(),(drc,settings.drc_min_angle_deg,settings.drc_tolerance_um),settings.numeric_key(),settings.width_key() if width_analysis else (False,),settings.reconstruction_key() if reconstruction_view else (False,))
            frame=None if show_frame else cache_group.nominal.get(nominal_key)
            if frame is None and (show_frame or drc or width_analysis or reconstruction_view or back or (deform and result is not None)):
                frame=actual.frame(states,progress,quick=direct and not settle)
            if frame is not None and not show_frame and not frame.get('nominal_global'):
                frame=dict(frame,nominal_global=True)
                if deform and result is not None and math.isfinite(params.target_nsigma):
                    frame=actual.global_frame(frame,settings,progress)
                elif math.isfinite(params.target_nsigma):
                    frame['contact']=dict(frame['contact'],issues=(result or {}).get('issues',[]),intrusions=[])
                cache_group.nominal[nominal_key]=frame
            summary=None
            if width_analysis and result is not None:
                summary=actual.width_summary(progress)
                if frame is not None and not show_frame:
                    frame=dict(frame,width_summary=summary)
                    cache_group.nominal[nominal_key]=frame
            if back or (settings.animation_enabled and not direct and np.any(states) and not np.array_equal(start_states,states)):
                from .prepared_motion import prepare_motion
                publish=(lambda intermediate:worker.publish((actual,intermediate,distribution,con,actual_cache,field))) if settings.animation_strategy=='stream' else None
                motion=prepare_motion(actual,start_states,states,settings,view_span,cache_group.motions,progress,publish,worker.cancelled)
            if direct:motion=dict(direct=True,settled=settle)
            if not show_frame and not (drc or width_analysis or heat or space or reconstruction_view or (deform and result is not None)):frame=None
            return actual,frame,distribution,con,actual_cache,field,prepared,motion,footprints,summary
        def work(progress):
            from .scheduling import job_budget
            with job_budget(admitted['threads'],admitted['processes']):return solve(progress)
        worker=Worker(work,self);worker.interactive_refinement=bool(direct and settle);worker.transient=bool(direct);worker.request_key=request_key;worker.view_ticket=ticket
        self.preview_workers.append(worker);self.preview_worker=worker;self.watch_worker(worker,'预演计算',quiet=bool(getattr(self,'_preview_quiet',False)))
        self.log_bus.emit(admitted['reason']+f" · 活跃预演 {len(self.preview_workers)} · CPU {admitted['load'].get('cpu_percent')}% · 可用内存 {admitted['load']['available_memory_mb']} MiB")
        def partial(_):
            value=worker.take_partial()
            if value is None or worker.cancelled.is_set() or self.navigation.drag is not None:return
            if revision!=self.revision or worker.view_ticket!=self.preview_ticket or data is not self.data:return
            actual,frame,distribution,con,cache,field=value
            self.engine=actual;self.case=case;self.probability_data=distribution;self.connections=con;self.geometry_cache=cache;self.space_data=field
            self.show_motion_frame(frame)
        worker.partial.connect(partial)
        def accepted(value):
            worker.delivered=True
            if worker.cancelled.is_set():return
            if revision!=self.revision or data is not self.data or worker.view_ticket!=self.preview_ticket or self._closing:return
            # Keep cursor and pan events ahead of expensive artist/table
            # replacement. Apply one finished pose after the middle drag.
            if self.navigation.drag is not None:
                QtCore.QTimer.singleShot(40,lambda:accepted(value))
                return
            self.engine,frame,self.probability_data,self.connections,self.geometry_cache,self.space_data,self._prepared_package,self._prepared_motion,self.footprint_data,summary=value
            if summary is not None and self._width_grid_summary is not summary:
                self._width_grid_summary=summary
                if self.problem_filter.currentIndex()!=0 and self.width_toggle.isChecked():self.filter_changed(pending_ok=True)
            if worker.view_ticket!=self.preview_ticket:return
            self.case=case
            if frame and frame.get('global_deformation') and self.result:
                self.result['adaptive_preview']=frame['global_deformation']
            if self._prepared_motion and (self._prepared_motion.get('streamed') or self._prepared_motion.get('direct')):
                self._interpolated_states=None;self.current_frame=frame;self.plot_layout(preserve=True);self.update_histograms();self.show_frame_status(frame);self.log_bus.emit('交互帧已更新' if self._prepared_motion.get('direct') else '型变流式帧完成：'+str(self._prepared_motion['count']))
            elif self._prepared_motion and self._prepared_motion.get('prepared'):
                self.play_prepared_motion(frame,self._prepared_motion)
            else:self.present_frame(frame)
            if self.current_frame is not None and not self.transition.timer.isActive():self.update_state_table(self.current_frame['states'])
        def rejected(message):
            if worker.view_ticket!=self.preview_ticket:return
            self.statusBar().showMessage('预演未应用：'+message.split('Traceback')[0]);self.error(message)
        worker.done.connect(accepted);worker.failed.connect(rejected)
        def finished():
            if actual is not None:actual.busy=False
            if worker in self.preview_workers:self.preview_workers.remove(worker)
            self.preview_worker=self.preview_workers[-1] if self.preview_workers else None
            worker.deleteLater();self.launch_preview()
        worker.finished.connect(finished);worker.start()
    def update_histograms(self,clear=False):
        if not hasattr(self,'distribution_figure'):return
        self.histogram_data=None;self.histogram_dirty=True
        if self.tabs.currentWidget() is self.analysis_page:self.render_histograms()
    def calculate_histograms(self):
        seg=self.all_connector_segments;delta=seg[:,1]-seg[:,0];magnitude=np.linalg.norm(delta,axis=1);nonzero=magnitude>1e-9
        directions=(np.degrees(np.arctan2(delta[nonzero,1],delta[nonzero,0]))+360)%360
        bins=min(24,max(5,int(np.sqrt(len(magnitude)))))
        mag_counts,mag_edges=np.histogram(magnitude,bins=bins,range=(0,max(1e-6,float(magnitude.max())*1.001))) if len(magnitude) else (np.zeros(5,int),np.linspace(0,1,6))
        dir_counts,dir_edges=np.histogram(directions,bins=np.linspace(0,360,13))
        self.histogram_data=dict(magnitude=magnitude,direction=directions,magnitude_counts=mag_counts,direction_counts=dir_counts,magnitude_edges=mag_edges,direction_edges=dir_edges,zero_count=int((~nonzero).sum()))
        self.chart_status.setText(f'参与计算的运动过孔 {len(seg):,} 个；零位移 {(~nonzero).sum():,} 个（无方向）。方向：+X为0°，逆时针增加。')
    def render_histograms(self):
        if not self.histogram_dirty:return
        if self.current_frame is None:
            self.chart_status.setText('选择极端情况后显示运动过孔位移统计。');self.distribution_figure.clear();self.distribution_canvas.draw_idle();self.histogram_dirty=False;return
        if self.histogram_data is None:self.calculate_histograms()
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
        if self.remote_locked or getattr(self,'requested_measured',False):return
        if not self.data:self.error('请先打开GDS');return
        try:p=self.params();roles=self.roles();order=self.ordered_layers()
        except Exception as e:self.error(e);return
        if self.worker is not None:return
        self._evaluation_requested=True
        if not automatic:self.reset_session_cache();self.drop_result()
        rev=self.revision;data=self.data;cache=self.geometry_cache;motions=self.motions();settings=self.view_settings;values=self.session_values
        group=self.cache_group;prepare_width=self.width_toggle.isChecked()
        known_connections=self.connections if self.case is not None else None
        def work(progress):
            progress(1,'构建图层连接与几何索引');actual_cache=cache or GeometryCache(data);connections=known_connections or Connections(data,roles,order,actual_cache.geometries);case=build_case(data,roles,connections=connections)
            key=self.assessment_identity(data,roles,order,p,settings)
            from .contact import ContactModel
            model=None
            def assess():
                nonlocal model
                model=ContactModel(data,case,roles,connections,actual_cache,motions,p)
                return evaluate(data,roles,p,progress=progress,layer_order=order,connections=connections,motions=motions,include_analysis=False,
                                settings=settings,safe_range_cache=values,contact_model=model,validate_extremes=True,extreme_scope=settings.evaluation_scope)
            r=dict(values.get_or_compute(key,assess),motions=motions,view_settings=asdict(settings))
            catalog=ExtremeCatalog(case,p)
            from .session_cache import logical_fingerprint
            digest=logical_fingerprint((r.get('parameters'),r.get('contact_analysis'),r.get('issues')))
            signature=logical_fingerprint((data.geometry_id,data.top_name,roles,order,motions,asdict(p),digest))
            engine=PreviewEngine(actual_cache,case,roles,motions,p,r,connections,group,signature,model)
            engine.signature=signature
            summary=None
            if prepare_width:
                engine.configure_width(True,settings)
                summary=engine.width_summary(progress)
            progress(100,'方案评估与极端状态校验完成')
            return r,case,actual_cache,connections,catalog,engine,summary
        def accepted(value,waited_for_drag=False):
            if data is not self.data or self._closing:return
            if rev!=self.revision:
                self.log_bus.emit('评估快照已过期：排队计算最新参数，旧任务不会覆盖结果');self._coverage_pending=True;self.coverage_timer.start();return
            if self.cut_drag.state is not None:
                QtCore.QTimer.singleShot(50,lambda:accepted(value,True))
                return
            self.log_bus.emit('评估结果已应用：版本代号 '+str(rev))
            r,self.case,self.geometry_cache,self.connections,catalog,engine,summary=value
            if group is not self.cache_group:return
            group.engines[(engine.signature,id(engine))]=engine
            self.display_result(r,catalog,preserve=automatic or waited_for_drag,summary=summary,retain_frame=waited_for_drag)
        self.start_worker(work,accepted,interactive=True)
    def display_result(self,r,catalog=None,preserve=False,summary=None,retain_frame=False):
        saved=(self.active_preview,self.manual_states,self.preview_page)
        self.cache_group.last_assessment=(self.assessment_identity(self.data,self.roles(),self.ordered_layers(),self.params(),self.view_settings),r)
        from .session_cache import logical_fingerprint
        # The published assessment is immutable. Derived analysis fields appended
        # later are deliberately outside its numerical identity.
        self._assessment_digest=logical_fingerprint((r.get('parameters'),r.get('contact_analysis'),r.get('issues')))
        self._width_grid_summary=summary;self._problem_tag=None
        self.result=r;self.dirty=False;self.evaluation_motions=r.get('motions',self.motions());self.manual_states=None
        self.width_yield.render()
        if self.case is None:self.case=build_case(self.data,self.roles(),layer_order=self.ordered_layers())
        self.extremes=catalog if catalog is not None else ExtremeCatalog(self.case,self.params())
        self.focus_extremes=RepresentativeExtremes.from_result(self.case,self.params(),r,self.extremes)
        self.risk_extremes=RepresentativeExtremes(self.case,self.params(),self.extremes,risk_indices=r.get('risk_indices',()))
        self.active_preview=None;self.owner_cache=None
        if not retain_frame:self.engine=None;self.current_frame=None
        self.show_extreme_page(saved[-1] if preserve else self._initial_extreme_page,redraw=False)
        self.fill_issues()
        self._analysis_result=r;self.histogram_dirty=True
        if preserve:
            old,manual,_=saved
            # A changed grid may reuse an old integer ID for a different pose.
            previous=getattr(self,'_selected_grid_pose',None)
            row=next((i for i,(_,item) in enumerate(self.page_items,1) if old is not None and previous is not None and np.array_equal(previous,item['states'])),0)
            self.active_preview=self.page_items[row-1][0] if row else None;self.manual_states=manual
            self.extreme_table.blockSignals(True);self.extreme_table.setCurrentCell(-1 if manual is not None else row,0);self.extreme_table.blockSignals(False)
        else:self.tabs.setCurrentIndex(0);self.preview_tabs.setCurrentIndex(0)
        self.plot_layout(preserve=preserve)
        self.request_preview(pose_change=bool(self.pending_deleted_shapes or self.pending_restored_shapes or self._reconstruction_apply_once));self.width_yield.refresh();self.analysis.activate()
        self.statusBar().showMessage('评估完成：选择极端情况预演；进入结果分析后计算安全范围与良率')
    def render_analysis_result(self,r,preserve=False):
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

    def tab_changed(self,*_):
        if self.tabs.currentIndex()==0 and self.data:
            self.resize_plot();self.plot_layout(preserve=True)
        if self.tabs.currentWidget() is self.analysis_page:self.analysis.activate()
    def jump_extreme_page(self):
        try:page=int(self.page_edit.text())-1
        except ValueError:page=self.preview_page
        self.show_extreme_page(page)
    def coupled_view(self):
        width=self.width_toggle.isChecked()
        return self.result is not None and (False or
            (width and self.reconstruction_toggle.isChecked())) and (width or self.problem_filter.currentIndex()!=0)

    def schedule_coupled_filter(self):
        if self.loading or self._closing:return
        if self.reconstruction_toggle.isChecked():
            if self.filter_job:self.filter_job.cancelled.set()
            self.reconstruction_yield.refresh()
            evidence=self.reconstruction_yield.evidence;base=self._width_grid_summary
            if self.width_toggle.isChecked() and evidence and base:
                extra=evidence['routing_summary']
                self._coupled_data=dict(complete=True,grid_key=evidence['grid_key'],package_states=evidence['package_states'],
                    lines={**base['channel_lines'],**extra['lines']},witnesses={**base['channel_witnesses'],**extra['witnesses']},
                    red_factors=[*base['channel_factors'],*extra['red_factors']])
            else:self._coupled_data=None
            return
        wanted=self.coupled_view() and self.extremes is not None and not self.dirty
        if not wanted:
            if self.filter_job:self.filter_job.cancelled.set()
            self._coupled_key=None;self._coupled_data=None;return
        p=self.params();roles=self.roles();order=self.ordered_layers();motions=self.motions()
        data=self.data;result=self.result;cfg=self.view_settings;group=self.cache_group
        signature=self.preview_identity(data,roles,order,motions,p,result)
        width=self.width_toggle.isChecked();deform=False;rebuild=width and self.reconstruction_toggle.isChecked()
        focus=None if width or self.grid_mode.currentIndex()==0 else tuple(self.scoped_catalog().indices)
        key=('changed-extremes-v29',signature,cfg.deformation_key() if deform else (),cfg.reconstruction_key() if rebuild else (),
             cfg.width_key() if width else (),focus)
        if key!=self._coupled_key:
            self._coupled_key=key;self._coupled_data=None;self._coupled_error=False
            if self.filter_job:self.filter_job.cancelled.set()
        if self.filter_job or self._coupled_data is not None or self._coupled_error:return
        from .scheduling import admission,job_budget
        policy=admission(len(self.preview_workers)+int(self.worker is not None))
        if not policy['start']:
            if not getattr(self,'_filter_retry',False):
                self._filter_retry=True
                def retry():
                    self._filter_retry=False;self.schedule_coupled_filter()
                QtCore.QTimer.singleShot(400,retry)
            return
        grid=self.extremes
        def solve(progress):
            from .coupled_filter import scan
            def calculate():
                progress(0,'准备型变后工况的几何索引')
                cache=GeometryCache(data);con=Connections(data,roles,order,cache.geometries)
                case=build_case(data,roles,connections=con)
                engine=PreviewEngine(cache,case,roles,motions,p,result,con,group);engine.signature=signature
                try:
                    engine.configure_deformation(deform,cfg);engine.configure_width(width,cfg);engine.configure_reconstruction(rebuild,cfg)
                    return scan(engine,grid,cfg,width,focus,progress)
                finally:engine.close()
            with job_budget(max(1,policy['threads']//2),max(1,policy['processes']//2)):
                return group.values.get_or_compute(key,calculate)
        job=Worker(solve,self);self.filter_job=job;self.watch_worker(job,'核实型变后工况')
        def accept(q):
            if key!=self._coupled_key or group is not self.cache_group or data is not self.data:return
            self._coupled_data=q;self.filter_changed();self.plot_layout(preserve=True)
        def fail(exc):
            if key==self._coupled_key:self._coupled_error=True;self.error(exc)
        def finish():
            self.filter_job=None;job.deleteLater()
            if not self._closing:QtCore.QTimer.singleShot(0,self.schedule_coupled_filter)
        job.done.connect(accept);job.failed.connect(fail);job.finished.connect(finish)
        job.start(QtCore.QThread.Priority.LowPriority if hasattr(QtCore.QThread,'Priority') else QtCore.QThread.LowPriority)

    def filter_changed(self,pending_ok=False):
        if self.loading or self.extremes is None or self.cut_drag.state is not None:return
        current=self.active_preview;manual=self.manual_states
        self.show_extreme_page(self.preview_page,redraw=False)
        row=next((i for i,(index,_) in enumerate(self.page_items,1) if index==current),0)
        if manual is not None:self.leave_extreme_selection()
        elif row:
            self.active_preview=current
            self.extreme_table.blockSignals(True);self.extreme_table.setCurrentCell(row,0);self.extreme_table.blockSignals(False)
        elif current is not None:
            if self.reconstruction_toggle.isChecked() or (pending_ok and ((self.coupled_view() and self._coupled_data is None) or (self.width_toggle.isChecked() and self._width_grid_summary is None))):
                self.active_preview=current;self.leave_extreme_selection()
            else:self.select_preview(0,0)

    def evaluation_scope_changed(self,*_):
        if self.loading:return
        self.view_settings=replace(self.view_settings,evaluation_scope=self.grid_mode.currentData())
        self.enable_settings();self.revision+=1;self.stop_animation();self.cancel_previews()
        self.width_yield.reset();self.reconstruction_yield.reset();self.constraint_feedback.cancel()
        self._width_grid_summary=None;self._coupled_data=None;self._problem_tag=None
        if self.result is not None or self._evaluation_requested:
            self._coverage_pending=True;self.coverage_timer.start()
        self.show_extreme_page(0,redraw=False)

    def scoped_catalog(self):
        mode=self.grid_mode.currentData()
        return self.focus_extremes if mode=='focus' else self.risk_extremes if mode=='risk' else self.extremes

    def show_extreme_page(self,page=0,redraw=True,keep_pose=False):
        if self.extremes is None:return
        catalogue=self.scoped_catalog()
        active=self.active_preview if keep_pose else None
        self.schedule_coupled_filter()
        source=catalogue;waiting=False
        if self.problem_filter.currentIndex()!=0:
            from .extreme_filter import ProblemIndex,FilteredCatalog,current_drc_factors
            from .pose_filter import filter_rules
            recon=self.reconstruction_toggle.isChecked()
            source_facts=(self.result or {}).get('extreme_factors');evidence=self.reconstruction_yield.evidence if recon else None
            mode=self.problem_filter.currentIndex()
            approved=self.approved_drc_poses() if mode==1 else ()
            tag=(id(self.extremes),id(source_facts),id(evidence),recon,mode,approved)
            if self.dirty:waiting=True
            elif tag!=getattr(self,'_problem_tag',None):
                rules=filter_rules(mode,recon,source_facts,evidence)
                if rules is None:waiting=True
                else:self._problem_index=ProblemIndex(self.extremes,rules,approved);self._problem_tag=tag
            if not waiting:catalogue=FilteredCatalog(source,self._problem_index)
        size=self.view_settings.table_page_limit-1
        pages=max(1,(catalogue.total+size-1)//size);page=max(0,min(page,pages-1))
        self.preview_page=page;self.page_items=catalogue.page(page,size);self.active_preview=active;self._extreme_paint={};self._paint_pass=None
        self.page_edit.setText(str(page+1));self.page_prev.setEnabled(page>0);self.page_next.setEnabled(page+1<pages)
        self.page_summary.setText(f'第 {page+1:,}/{pages:,} 页 · 共 {source.total+1:,} 行'+(' · 正在核验，暂列全部' if waiting else ''))
        self.page_summary.setToolTip('总行数按所选组合范围计，含零位；过滤只改变可见行。')
        self.extreme_table.blockSignals(True);self.extreme_table.clearContents();self.extreme_table.setRowCount(len(self.page_items)+1)
        for col in range(self.extreme_table.columnCount()):self.extreme_table.setItem(0,col,W.QTableWidgetItem(''))
        for i,(index,e) in enumerate(self.page_items,1):
            states=e['states'];values=[]
            for j in range(3):
                x=np.rad2deg(states[:,j]) if j==2 else states[:,j]
                values.append(fmt(x[0],5) if np.allclose(x,x[0]) else f'{fmt(min(x),5)} ～ {fmt(max(x),5)}')
            values.append(e['detail'])
            detail='\n'.join(f'DIE {j+1}: DX={state[0]:.6g} μm, DY={state[1]:.6g} μm, θ={math.degrees(state[2]):.6g}°' for j,state in enumerate(states))
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(value);item.setToolTip(detail);self.extreme_table.setItem(i,j,item)
        self.extreme_table.setVerticalHeaderLabels(['0',*[str(page*size+i) for i in range(1,len(self.page_items)+1)]])
        row=next((j for j,(index,_) in enumerate(self.page_items,1) if index==active),0)
        self.extreme_table.setCurrentCell(row if row or active is None else -1,0);self.extreme_table.blockSignals(False)
        for j,width in enumerate([99,94,94,81]):self.extreme_table.setColumnWidth(j,width)
        self.extreme_table.horizontalHeader().setStretchLastSection(True)
        if not keep_pose:self.state_table.setRowCount(0)
        self.refresh_extreme_colors()
        if redraw:self.select_preview(0,0)
    def refresh_extreme_colors(self,frame=None):
        from .extreme_validation import frame_status
        from .extreme_validation import status_for
        factors=(self.result or {}).get('extreme_factors') if not self.dirty else None
        if self.extremes is None:return
        rows=[(0,None),*[(i,item) for i,(_,item) in enumerate(self.page_items,1)]]
        current_key=(frame['states'].tobytes() if frame is not None else None)
        current=frame_status(frame) if current_key is not None else {}
        catalog=self.scoped_catalog() or self.extremes
        approved=self.approved_drc_poses()
        scope_key=(id(catalog),id(factors),id(self.reconstruction_yield.evidence),self.reconstruction_toggle.isChecked(),self.dirty,approved)
        if getattr(self,'_zero_scope_key',None)!=scope_key:
            from .extreme_validation import catalogue_status
            self._zero_scope_status=catalogue_status(self.extremes,catalog,factors,self.reconstruction_yield.evidence if not self.dirty else None,
                                                    reconstruct=self.reconstruction_toggle.isChecked(),approved=approved)
            self._zero_scope_key=scope_key
        version=(scope_key,id(self.result),self.dirty,current_key,tuple(current.items()),self.reconstruction_toggle.isChecked(),self.preview_page)
        if getattr(self,'_paint_pass',None)==version:return
        self._paint_pass=version
        for row,item in rows:
            key=item['states'].tobytes() if item else bytes(24*len(self.extremes.locals))
            status=status_for(factors,item['choices'] if item else [0]*len(self.extremes.locals))
            known=getattr(self,'_cached_row_checks',{}).get(key) if item else None
            if known and (known['checked'] or known['safe'] is False or known.get('width_pending') or False or self.dirty):status=known
            if key==current_key:
                if current.get('checked') or current.get('safe') is False:status=current
                elif current.get('width_pending') or False or self.dirty:status=current
            moving_drc=status.get('moving_drc',0)
            if item and self.reconstruction_toggle.isChecked():
                evidence=self.reconstruction_yield.evidence
                if evidence and evidence.get('complete'):
                    from .extreme_filter import ProblemIndex
                    if getattr(self,'_reconstruction_row_bank',None) is not evidence:
                        self._reconstruction_row_bank=evidence;self._reconstruction_row_index=ProblemIndex(self.extremes,evidence['red_factors']);self._reconstruction_connection_index=ProblemIndex(self.extremes,evidence.get('connection_factors',()))
                    status=dict(checked=True,safe=not (moving_drc or self._reconstruction_row_index.matches(item['choices'])),connection_failures=self._reconstruction_connection_index.matches(item['choices']),reason='按实际重构规则检查')
                else:status=dict(checked=False,safe=None,reason='重构极端核验中')
                if item and key==current_key and frame and frame.get('reconstruction',{}).get('constraints_complete'):
                    from .reconstruction_events import scene_failure
                    status=dict(checked=True,safe=not (frame.get('drc') or frame['reconstruction'].get('hard_failures')),drc=len(frame.get('drc',())),connection_failures=len(frame['reconstruction'].get('hard_failures',())),reason='按当前实际铜DRC和连接检查')
            if item is None:status=self._zero_scope_status
            elif self.page_items[row-1][0] in approved:
                status=dict(checked=True,safe=True,reason='该姿态剩余 DRC 均已忽略')
            safe=status.get('safe') is True
            if status.get('checked') and 'drc' in status:safe=status['drc']==0 and not status.get('connection_failures')
            check=('通过已检查规则' if safe else '存在已检查的问题' if status.get('safe') is False else '未作完整校验') if item else '名义位置 / 当前组合最严重结果'
            if not item:
                zero=(self.result or {}).get('nominal_discrete_check',{}) if not self.dirty else {}
                if zero.get('checked'):check+=' · 零位几何'+('通过' if zero['safe'] else '存在问题')
            if status.get('reason'):check+=' · '+status['reason']
            failed=bool(status.get('connection_failures'));known=bool(status.get('checked'))
            row_index=self.page_items[row-1][0] if (item and 1<=row<=len(self.page_items)) else None
            is_low,pose_prob,threshold=self.is_low_probability_pose(row_index) if (row_index is not None and self.reconstruction_toggle.isChecked()) else (False,None,None)
            signature=(check,safe,failed,known,is_low)
            if self._extreme_paint.get(row)==signature:continue
            self._extreme_paint[row]=signature
            if known or failed:
                color='#f4ecec' if (failed and is_low) else '#fbe7e7' if failed else '#eaf5ed' if safe else '#f6f1e4' if is_low else '#fff3d9'
                brush=QtGui.QBrush(QtGui.QColor(color))
            else:
                brush=QtGui.QBrush(QtGui.QColor('#f2f3f5')) if not item and self.reconstruction_toggle.isChecked() else QtGui.QBrush()
            low_note=f'（低概率 P≈{pose_prob:.3g} ≤ 门限 {threshold:.3g}，已低亮）' if is_low else ''
            detail=f'{check}{low_note}；浅绿：已检查无问题；浅黄：尺寸DRC违规但连通；浅红：重构连接失败。未检查行不着色。'
            for col in range(self.extreme_table.columnCount()):
                cell=self.extreme_table.item(row,col)
                if cell is not None:
                    if cell.background()!=brush:cell.setBackground(brush)
                    old=cell.toolTip().split('\n校验：')[0];tip=old+'\n校验：'+detail
                    if cell.toolTip()!=tip:cell.setToolTip(tip)


    def is_low_probability_pose(self,index=None):
        """True when a discrete extreme pose has cell probability <= 2Φ(-target_nsigma)."""
        if self.data is None or self.extremes is None:return False,None,None
        if index is None:
            if self.manual_states is not None or self.active_preview is None:return False,None,None
            index=self.active_preview
        try:p=self.params()
        except ValueError:return False,None,None
        if not (0<p.target_nsigma<math.inf):return False,None,None
        threshold=p.failure_threshold
        from .reconstruction_probability import pose_probability
        prob=pose_probability(self.extremes,p,index)
        return (prob is not None and prob<=threshold),prob,threshold

    def restore_preview(self):
        if self.data is None:return
        # Manual preview may have no selection; selecting an already current
        # zero row does not emit currentCellChanged. Dispatch exactly once.
        table=self.extreme_table
        bars=(table.verticalScrollBar(),table.horizontalScrollBar())
        offsets=[bar.value() for bar in bars]
        blocked=table.blockSignals(True)
        try:
            table.setCurrentCell(0,0)
            for bar,offset in zip(bars,offsets):bar.setValue(offset)
        finally:table.blockSignals(blocked)
        self.select_preview(0,0)

    def select_preview(self,row,column,*_):
        if self.loading:return
        next_index=self.page_items[row-1][0] if 1<=row<=len(self.page_items) else None
        if next_index!=self.active_preview or self.manual_states is not None:self.edit_history.record()
        if self.cut_drag.state is not None:self.cut_drag.cancel(redraw=False)
        if hasattr(self,'mouse_die'):
            self.mouse_die.cancel_pending();self.mouse_die.exit()
        self.reconstruction_overlay.clear_hover()
        self.manual_states=None
        self.active_preview=next_index
        self._selected_grid_pose=self.extremes[self.active_preview]['states'] if self.active_preview is not None else None
        self.state_table.setRowCount(0)
        if self.active_preview is not None:
            e=self.extremes[self.active_preview];self.state_table.setRowCount(len(e['states']))
            for i,state in enumerate(e['states']):
                for j,value in enumerate([i+1,fmt(state[0],6),fmt(state[1],6),fmt(math.degrees(state[2]),6),e['per_die_labels'][i]]):
                    self.state_table.setItem(i,j,W.QTableWidgetItem(str(value)))
            self.state_table.resizeColumnsToContents()
        self.request_preview(pose_change=True)
    def contact_entries(self,frame):
        if frame is None:return []
        if 'drc_locations' not in frame:
            from .current_drc import locations
            frame['drc_locations']=locations(frame.get('drc',()))
        return frame['drc_locations']

    def drc_waiver_context(self):
        from .session_cache import logical_fingerprint
        cfg=self.preview_settings();reconstruct=self.reconstruction_toggle.isChecked()
        reconstruction=cfg.reconstruction_key() if reconstruct else ()
        return logical_fingerprint((self.data.geometry_id,self.data.top_name,self.roles(),self.ordered_layers(),
                                    asdict(self.params()),cfg.drc_checks,cfg.drc_tolerance_um,cfg.drc_min_angle_deg,reconstruction))

    def drc_waiver_summary(self):
        cfg=self.view_settings
        from .drc_waivers import needs_events,summary
        if self.extremes is None or self.dirty or not (self.ignored_drc or needs_events(cfg)):
            return frozenset(),frozenset(),{}
        bank=self.reconstruction_yield.evidence if self.reconstruction_toggle.isChecked() else None
        context=self.drc_waiver_context() if self.ignored_drc else None
        key=(id(self.extremes),id(self.ignored_drc),context,id(bank),cfg.reconstruction_auto_waive_drc,
             cfg.reconstruction_waiver_mantissa,cfg.reconstruction_waiver_exponent,cfg.drc_waived_refs,cfg.drc_retained_refs)
        cached=getattr(self,'_waiver_cache',None)
        if cached is not None and cached[0]==key:return cached[1]
        def states_at(index):
            return self.extremes[index]['states'] if index>=0 else np.zeros((self.extremes.count,3))
        value=summary(cfg,bank,self.ignored_drc,context,self.extremes,states_at)
        self._waiver_cache=(key,value)
        return value

    def approved_drc_poses(self):
        return self.drc_waiver_summary()[1]

    def issue_menu(self,pos):
        row=self.issue_table.indexAt(pos).row()
        if self.remote_locked or not 0<=row<len(self.filtered_issues):return
        issue=self.filtered_issues[row]
        menu=W.QMenu(self.issue_table)
        action=menu.addAction('取消忽略此问题' if issue.get('ignored') else '忽略此问题')
        if 'figure_policy' in issue:
            action.setEnabled(False)
            menu.addAction('由图形右键菜单的 DRC 忽略设置控制').setEnabled(False)
        action.triggered.connect(lambda:self.toggle_drc_issue(issue))
        getattr(menu,'exec',getattr(menu,'exec_',None))(self.issue_table.viewport().mapToGlobal(pos))

    def toggle_drc_issue(self,issue):
        from .drc_waivers import pose_id,record
        frame=self.current_frame
        if self.remote_locked or self.data is None or frame is None or 'drc' not in frame or 'figure_policy' in issue:return
        rows=self.contact_entries(frame);pose=pose_id(frame['states'])
        index=-2  # A manual pose has no catalogue row.
        if not np.any(frame['states']):index=-1
        elif self.extremes is not None and self.active_preview is not None:
            if pose==pose_id(self.extremes[self.active_preview]['states']):index=self.active_preview
        if self.reconstruction_toggle.isChecked() and not frame.get('reconstruction',{}).get('constraints_complete'):
            index=-2
        old=self.ignored_drc.get(pose,{})
        entry=record(frame,rows,old,issue,self.drc_waiver_context(),index,self.drc_waiver_summary()[0])
        self.edit_history.record()
        records=dict(self.ignored_drc)
        if entry['ignored'] or entry['retained']:records[pose]=entry
        else:records.pop(pose,None)
        self.replace_drc_waivers(records)

    def change_drc_figure_waivers(self,refs,enabled):
        if self.remote_locked or self.data is None or not self.reconstruction_toggle.isChecked():return
        refs={(key,i) for key,i in refs if 0<=i<len(self.data.layers.get(key,()))}
        ignored=set(self.view_settings.drc_waived_refs)
        retained=set(self.view_settings.drc_retained_refs)
        if enabled:ignored.update(refs);retained.difference_update(refs)
        else:ignored.difference_update(refs);retained.update(refs)
        updated=replace(self.view_settings,drc_waived_refs=tuple(sorted(ignored)),drc_retained_refs=tuple(sorted(retained)))
        if updated==self.view_settings:return
        self.edit_history.record()
        self.view_settings=updated
        self.replace_drc_waivers(self.ignored_drc,refresh_evidence=True)

    def replace_drc_waivers(self,records,*,refresh_evidence=False):
        self.ignored_drc=records;self._issue_table_stamp=None;self._paint_pass=None
        if refresh_evidence:self.reconstruction_yield.refresh()
        self.fill_issues()
        if self.extremes is not None and self.problem_filter.currentIndex()==1:
            self.show_extreme_page(self.preview_page,redraw=False,keep_pose=True)
        else:self.refresh_extreme_colors(self.current_frame)
        self.reconstruction_overlay.draw();self.drc_overlay.draw();self.render_scene()

    def fill_issues(self):
        entries=self.contact_entries(self.current_frame);auto=self.drc_waiver_summary()[0]
        cfg=self.view_settings
        figures=(cfg.drc_waived_refs,cfg.drc_retained_refs) if self.reconstruction_toggle.isChecked() else ((),())
        stamp=(id(entries),cfg.drc_checks,cfg.drc_visible,id(self.ignored_drc),auto,figures)
        if stamp==self._issue_table_stamp:return
        from .drc_policy import visible
        self._drc_cleared_figures=frozenset()
        if entries and (self.ignored_drc or auto or any(figures)):
            from .drc_waivers import cleared_sources,event_id,figure_decision,figure_refs,pose_id,witness_id
            saved=self.ignored_drc.get(pose_id(self.current_frame['states']),{})
            waived=set(saved.get('ignored',()));retained=set(saved.get('retained',()))
            ignored_refs,retained_refs=map(set,figures)
            flags=[];display=[]
            for row in entries:
                decision=figure_decision(figure_refs(row),ignored_refs,retained_refs) if any(figures) else None
                if decision is None:
                    key=witness_id(row) if waived or retained else None
                    flag=(key in waived or bool(auto and event_id(row) in auto)) and key not in retained
                else:flag=decision
                flags.append(flag)
                display.append(dict(row,ignored=flag,figure_policy=decision) if decision is not None else dict(row,ignored=True) if flag else row)
            self._drc_cleared_figures=cleared_sources(self.current_frame,entries,flags)
            entries=display
        self._issue_table_stamp=stamp;self.display_issues=[q for q in entries if visible(self.view_settings,q)]
        self.show_issue_page(self.issue_page)
    def show_issue_page(self,page):
        entries=getattr(self,'display_issues',[]);size=self.view_settings.table_page_limit
        pages=max(1,(len(entries)+size-1)//size);self.issue_page=max(0,min(int(page),pages-1));start=self.issue_page*size
        self.filtered_issues=entries[start:start+size];self.issue_table.blockSignals(True);self.issue_table.setRowCount(len(self.filtered_issues))
        for i,row in enumerate(self.filtered_issues):
            required=(f"{row['required_deg']:g}°" if row.get('required_deg') is not None else f"{row['required_um']:g} μm" if row.get('required_um') is not None else '—')
            actual=(f"{row['actual_deg']:.3f}°" if row.get('actual_deg') is not None else f"{row['actual_um']:.3f} μm" if row.get('actual_um') is not None else '—')
            values=[row['kind'],'、'.join(row.get('layers',(row['layer'],))),required,actual,*[fmt(v) for v in row['point']]]
            for j,value in enumerate(values):
                item=W.QTableWidgetItem(str(value))
                if row.get('ignored'):
                    item.setBackground(QtGui.QColor('#e4f4e8'));item.setToolTip('已忽略，仍保留原始检查和位置。')
                else:item.setToolTip('当前姿态实际铜的DRC；显隐不影响检查。')
                self.issue_table.setItem(i,j,item)
        self.issue_table.setVerticalHeaderLabels([str(start+i+1) for i in range(len(self.filtered_issues))]);self.issue_table.blockSignals(False)
        self.issue_summary.setText(f'第 {self.issue_page+1}/{pages} 页');self.issue_prev.setEnabled(self.issue_page>0);self.issue_next.setEnabled(self.issue_page+1<pages)
    def contact_markers_changed(self,enabled):
        self.view_settings=replace(self.view_settings,show_contact_markers=bool(enabled))
        field=self.setting_fields.get('show_contact_markers')
        if field is not None:
            field.blockSignals(True);field.setChecked(enabled);field.blockSignals(False)
        if self.loading:return
        if enabled and self.data is not None and (self.current_frame is None or 'drc' not in self.current_frame):self.request_preview()
        else:self.plot_layout(preserve=True)
    def issue_row_changed(self,row,column,previous_row,previous_column):
        if row!=previous_row and not self.loading:self.issue_navigation.start()
    def locate_issue(self,row,column,*_):
        if not 0<=row<len(self.filtered_issues):return
        issue=self.filtered_issues[row];x0,y0,x1,y1=issue['geometry'].bounds
        span=max(100,min(600,max(x1-x0,y1-y0)*1.5));self.animate_view(issue['point'],span,span)
    def refresh_jobs(self,*_):
        try:items=list_jobs(self.jobs_dir)
        except OSError as exc:self.statusBar().showMessage('Jobs目录不可读：'+str(exc));return
        self.job_combo.blockSignals(True);self.job_combo.clear();self.job_combo.addItem('',None)
        for name,path in items:self.job_combo.addItem(name,str(path))
        if self.project_path and self.job_combo.findData(str(self.project_path))<0:
            self.job_combo.addItem(self.project_path.stem.removesuffix('.dshift')+'（外部工程）',str(self.project_path))
            self.job_combo.setItemData(self.job_combo.count()-1,str(self.project_path),TOOLTIP)
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
        if Path(path).parent.resolve()==self.jobs_dir.resolve():path=job_path(self.jobs_dir,Path(path).name.removesuffix('.json').removesuffix('.dshift'))
        try:self.write_project(path);self.refresh_jobs();self.statusBar().showMessage('工程已保存：'+Path(path).stem)
        except Exception as exc:self.error(exc)
    def project_settings(self):
        from .reconstruction_edits import retire_pad_intervals
        solver=None if getattr(self.engine,'busy',False) else getattr(self.engine,'reconstructor',None)
        settings=retire_pad_intervals(self.view_settings,solver)
        return dict(schema_version=5,app_version=__version__,rulers=self.rulers.rows,sha256=self.data.sha256,top_cell=self.data.top_name,
                    roles=self.roles(),parameters=asdict(self.params()),settings=asdict(settings),node_edits=list(self.data.node_edits),
                    deleted_shapes=list(self._effective_deleted_shapes()),
                    geometry_indexing=getattr(self.data,'geometry_indexing','legacy'),legacy_node_edits=list(getattr(self.data,'legacy_node_edits',[])),
                    layout_output=__import__('die_shift_studio.layout_export',fromlist=['configuration']).configuration(self),
                    dxf_directory=self.dxf_directory,dxf_options=asdict(self.dxf_settings),measured_output=self.measured_output.configuration(),
                    apd_export=self.apd_options,layer_compensation=self.output_compensation(),ignored_drc=self.ignored_drc,
                    ignored_reconstruction=dict(sha256=self.data.sha256,top_cell=self.data.top_name,refs=[list(r) for r in self.view_settings.reconstruction_ignored]),
                    ignored_width_lines=dict(sha256=self.data.sha256,top_cell=self.data.top_name,refs=[list(r) for r in sorted(self.ignored_width_lines)]),
                    extreme_view=dict(filter_mode=('all','drc','failed')[self.problem_filter.currentIndex()],filter_problems=bool(self.problem_filter.currentIndex()),mode=self.grid_mode.currentData(),page_size=self.view_settings.table_page_limit-1,page=(self.preview_page if self.extremes is not None else self._initial_extreme_page)+1),
                    layer_table_height=max(110,self.layer_split.sizes()[0]),expanded_settings=[title for title,header in self.setting_sections.items() if header.isChecked()],
                    expanded_process=[title for title,header in self.process_sections.items() if header.isChecked()],manual_preview=self.manual_preview.state(),
                    overlays={name:self.overlay_widget(name).isChecked() for name in OVERLAY_DEFAULTS},layer_styles=self.layer_styles,
                    visibility={k:w.isChecked() for k,w in self.visible_boxes.items()},
                    selectability={k:w.isChecked() for k,w in self.selectable_boxes.items()},
                    motions=self.motions(),layer_order=self.ordered_layers())

    def show_shape_menu(self,event,point):
        from .shape_menu import show_shape_menu
        show_shape_menu(self,event,point)

    def change_fixed_via_following(self,refs,following):
        if self.remote_locked or self.view_settings.reconstruction_fixed_via_mode!='individual':return
        old=set(self.view_settings.reconstruction_fixed_via_follows);refs=set(refs)
        updated=old|refs if following else old-refs
        if old==updated:return
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,reconstruction_fixed_via_follows=tuple(sorted(updated)))
        self.invalidate_reconstruction_edit();self.request_preview(quiet=True)
        action='固定过孔随动' if following else '固定过孔恢复静止'
        if getattr(self,'reconstruction_pending',False):
            self.statusBar().showMessage(f'已暂存：{action}（{len(refs)} 个过孔已标记，点击“重构计算”统一计算）')
        else:
            self.statusBar().showMessage(f'已设置：{action}（{len(refs)} 个过孔）')

    def change_between_motion(self,block_id,mode):
        if self.remote_locked or self.data is None:return
        from .between_motion import LABELS,MODES
        if mode is not None and mode not in MODES:return
        layer,index,ax,ay=block_id
        layer=str(layer);index=int(index);ax=round(float(ax),4);ay=round(float(ay),4)
        # Replace any existing override on the same nominal block (matching exact anchor or within nominal geometry).
        block_geom=None
        if getattr(self,'selection',None) and self.selection.selected_between and self.selection.selected_between.get('id')==block_id:
            block_geom=self.selection.selected_between.get('nominal')
        rows=[]
        for row in self.view_settings.reconstruction_between_overrides:
            if (row[0],row[1])==(layer,index):
                if (row[2],row[3])==(ax,ay):continue
                if block_geom is not None and not block_geom.is_empty and block_geom.covers(shapely.Point(row[2],row[3])):continue
            rows.append(row)
        if mode is not None:
            rows.append((layer,index,ax,ay,mode))
        rows=tuple(sorted(set(rows)))
        if rows==self.view_settings.reconstruction_between_overrides:return
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,reconstruction_between_overrides=rows)
        self.invalidate_reconstruction_edit();self.request_preview(quiet=True)
        label=LABELS[mode] if mode is not None else '默认（'+LABELS[self.view_settings.reconstruction_between_motion]+'）'
        if getattr(self,'reconstruction_pending',False):
            self.statusBar().showMessage(f'已暂存动区间随动逻辑：{label}（点击“重构计算”统一计算）')
        else:
            self.statusBar().showMessage(f'已设置动区间随动逻辑：{label}')

    def _strip_ignored_zones(self,settings,refs):
        """Automatically delete all movable regions on ignored fixed-RDL shapes."""
        fields=('reconstruction_intervals','reconstruction_angles','reconstruction_deleted','reconstruction_added',
                'reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts','reconstruction_dropped_cuts',
                'reconstruction_between_overrides')
        edits={name:tuple(row for row in getattr(settings,name) if tuple(row[:2]) not in refs) for name in fields}
        edits['reconstruction_forced']=tuple(sorted(set(settings.reconstruction_forced)-refs))
        edits['reconstruction_box_excluded']=tuple(sorted(set(settings.reconstruction_box_excluded)|refs))
        return edits

    def _staged_summary(self,refs):
        actions=((getattr(self.reconstruction_overlay,'staged_overlay',None) or {}).get('actions',{})) if hasattr(self,'reconstruction_overlay') else {}
        parts=[]
        for ref in sorted(refs)[:3]:
            ops=actions.get(ref)
            if ops:parts.append(f'{ref[0]} #{ref[1]+1}: ' + ' + '.join(ops))
        return '；'.join(parts)

    def change_reconstruction_mode(self,refs,following):
        if getattr(self,'remote_locked',False):return
        refs={(k,int(i)) for k,i in refs};old=set(self.view_settings.reconstruction_following)
        edits=self._strip_ignored_zones(self.view_settings,refs)
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,**edits,reconstruction_following=tuple(sorted(old|refs if following else old-refs)),
                                   reconstruction_ignored=tuple(sorted(set(self.view_settings.reconstruction_ignored)|refs)))
        self.invalidate_reconstruction_edit();self.request_preview()
        mode='整块随动' if following else '整块静止'
        if getattr(self,'reconstruction_pending',False):
            summary=self._staged_summary(refs)
            self.statusBar().showMessage(f'已暂存：{summary or ("忽略图形改为"+mode)}（点击“重构计算”统一计算）')
        else:
            self.statusBar().showMessage(f'已设置：忽略图形为{mode}（{len(refs)} 个图形，已自动删除动区）')
    def change_reconstruction_ignores(self,refs,ignore):
        if getattr(self,'remote_locked',False):return
        old=set(self.view_settings.reconstruction_ignored)
        refs={(k,int(i)) for k,i in refs if self.roles().get(k)=='固定RDL'}
        updated=old|refs if ignore else old-refs
        if updated==old and ignore:return
        extra={}
        if ignore:
            extra.update(self._strip_ignored_zones(self.view_settings,refs))
        else:
            extra['reconstruction_forced']=tuple(sorted(set(self.view_settings.reconstruction_forced)|refs))
            extra['reconstruction_deleted']=tuple(r for r in self.view_settings.reconstruction_deleted if r[:2] not in refs)
            extra['reconstruction_following']=tuple(sorted(set(self.view_settings.reconstruction_following)-refs))
        self.edit_history.record()
        self.view_settings=replace(self.view_settings,reconstruction_ignored=tuple(sorted(updated)),**extra)
        self.invalidate_reconstruction_edit();self.request_preview()
        if getattr(self,'reconstruction_pending',False):
            summary=self._staged_summary(refs)
            action='忽略重构' if ignore else '取消忽略重构'
            self.statusBar().showMessage(f'已暂存：{summary or action}（点击“重构计算”统一计算）')
        elif ignore:
            self.statusBar().showMessage(f'已忽略重构 {len(updated)} 个图形并自动删除其全部动区；运动过孔仍按所属DIE运动。')
        else:
            self.statusBar().showMessage(f'已取消忽略重构 {len(refs)} 个图形；重新参与重构预演。')

    def change_width_ignores(self,refs,ignore):
        updated=self.ignored_width_lines.union(refs) if ignore else self.ignored_width_lines.difference(refs)
        if updated==self.ignored_width_lines:return
        self.edit_history.record();self.ignored_width_lines=updated
        self._legend_stamp=None
        self.plot_layout(preserve=True)
        self.width_yield.refresh()

    def show_properties(self):
        if getattr(self,'remote_locked',False):return
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
        if self.remote_locked or self.data is None:return
        if self.worker is not None:
            self._queued_node_edit=(self.data,edits);self.log_bus.emit('节点编辑已排队，当前任务完成后重建几何。');return
        from .area_filter import canonical_project
        project=canonical_project(self.data,dict(self.project_settings(),node_edits=edits))
        before=self.edit_history.capture()
        self.apply_layout_edit(project,commit=lambda:self.edit_history.record(before),
                               pose=before['pose'],extreme=before['extreme'])

    def undo_edit(self):
        self.edit_history.step()

    def redo_edit(self):
        self.edit_history.step(forward=True)

    def delete_selected_shapes(self):
        if self.remote_locked or self.data is None or self.cut_drag.state is not None:return
        if self.worker is not None:
            self.statusBar().showMessage('当前图纸正在更新，完成后可删除图形。');return
        from .area_filter import canonical_project
        from .gds_shapes import shape_nodes,node_fingerprint
        original=self.data;roles=self.roles()
        source=getattr(original,'_area_base',original)
        base=getattr(source,'_source_snapshot',source)
        rows={tuple(q[:2]):q[2] for q in self._effective_deleted_shapes()}
        added=[]
        removed_selected=set()
        for key,index in self.selection.selected:
            if roles.get(key) in ('芯片边框','封装边框') or not 0<=index<len(original.layers.get(key,())):continue
            raw=getattr(original,'area_indices',{}).get(key,range(len(original.layers[key])))[index]
            if (key,raw) in rows:continue
            kind,coords,_=shape_nodes(base,key,raw)
            fp=node_fingerprint(kind,coords)
            rows[key,raw]=fp;added.append(((key,raw),fp));removed_selected.add((key,index))
        if not added:return
        target_deletions=tuple((*ref,rows[ref]) for ref in sorted(rows))
        before=self.edit_history.capture()
        if self._can_defer_deletion_edit():
            self.pending_layout_ops.append(('delete',tuple(added)))
            self._sync_staged_deletion_markers(target_deletions)
            self.selection.selected=[item for item in self.selection.selected if item not in removed_selected]
            self.selection.draw()
            self.edit_history.record(before)
            self.defer_reconstruction()
            self.statusBar().showMessage(f'已暂存删除 {len(added)} 个图形（保持特殊标记，点击“重构计算”按顺序应用）')
            return
        project=canonical_project(original,self.project_settings())
        project['deleted_shapes']=list(target_deletions)
        self.apply_layout_edit(project,commit=lambda:self.edit_history.record(before),
                               pose=before['pose'],extreme=before['extreme'])

    def apply_layout_edit(self,project,*,commit,pose=None,extreme=None,force_reconstruct=False):
        """Publish a source-bound geometry edit only after reindexing succeeds."""
        original=self.data;view=(self.ax.get_xlim(),self.ax.get_ylim())
        was_assessed=self.result is not None
        selected=list(self.selection.selected)
        stage=not force_reconstruct and self.reconstruction_toggle.isChecked() and not self.view_settings.reconstruction_auto
        from .edit_history import mapped_edits
        previously_applied,_=mapped_edits(original,self.preview_settings(),(),to_source=True)
        pending_before=dict(self.pending_deleted_shapes)
        old_deleted={tuple(q[:2]) for q in getattr(original,'deleted_shapes',())}
        next_deleted={tuple(q[:2]) for q in project['deleted_shapes']}
        ghosts={} if force_reconstruct else {ref:g for ref,g in pending_before.items() if ref in next_deleted}
        if stage and next_deleted-old_deleted:
            for (layer,raw) in next_deleted-old_deleted:
                g=self._shape_marker_geometry(layer,raw)
                if not g.is_empty:ghosts[layer,raw]=g
        def prepare(progress):
            progress(5,'校验图纸编辑')
            from .area_filter import apply
            base=getattr(original,'_area_base',original)
            unfiltered=base if base.node_edits==project['node_edits'] else edited_snapshot(base,project['node_edits'])
            updated,edited_project=apply(unfiltered,project,reference=original)
            progress(35,'重建几何和金属连接');cache=GeometryCache(updated)
            try:case=build_case(updated,edited_project['roles'],layer_order=edited_project['layer_order'])
            except ValueError:case=None
            return updated,cache,case,edited_project
        def accepted(product):
            if self.data is not original:return
            updated,cache,case,edited_project=product
            updated.geometry_cache=cache;updated.prepared_case=case;updated.prepared_roles=project['roles'];updated.prepared_auto=automatic_parameters(updated,project['roles'])
            self.set_data(updated,edited_project,project_path=self.project_path,request_analysis=False)
            self.ax.set_xlim(view[0]);self.ax.set_ylim(view[1])
            self.manual_states=None if pose is None else np.array(pose,float)
            self.active_preview=extreme
            self.pending_deleted_shapes=ghosts
            if force_reconstruct:
                self._clear_pending_layout_markers()
                self.reconstruction_pending=False;self._reconstruction_apply_once=True
            elif ghosts:
                self.reconstruction_pending=True
                applied,_=mapped_edits(updated,previously_applied,(),to_source=False)
                self._applied_preview_settings=replace(self.view_settings,**applied)
            previous_map=getattr(original,'area_indices',{})
            current_map=getattr(updated,'area_indices',{})
            mapped=[]
            for key,index in selected:
                source_index=previous_map.get(key,range(len(original.layers[key])))[index]
                present=current_map.get(key,range(len(updated.layers.get(key,()))))
                if source_index in present:mapped.append((key,present.index(source_index)))
            self.selection.selected=mapped
            self.plot_layout(preserve=True)
            if case is not None and (force_reconstruct or was_assessed or self.width_toggle.isChecked() or self.reconstruction_toggle.isChecked()):
                self._evaluation_requested=True;self._coverage_pending=True;self.coverage_timer.start()
            elif case is not None:self.request_preview(pose_change=True)
            commit()
            self.log_bus.emit('工程图形、连通和分析缓存已更新。')
        self.start_worker(prepare,accepted)

    def write_project(self,path):
        path=Path(path).resolve();gds=Path(self.data.path).resolve()
        from .jobs import local_source,provision_project
        # A named Jobs project owns its client workspace. Also provision any
        # new project saved elsewhere while preserving legacy external jobs.
        if not path.exists() or path.parent.parent==self.jobs_dir.resolve():
            provision_project(path.parent)
        source=local_source(path,gds)
        from .area_filter import canonical_project
        project=dict(canonical_project(self.data,self.project_settings()),gds_path=source)
        project=self._merge_staged_restored_edits(project)
        if self._staged_node_edits is not None:project['node_edits']=list(self._staged_node_edits)
        if self.remote_locked or getattr(self,'requested_measured',False):
            for name,value in self.measured_output.saved_overlays.items():
                project['overlays']['footprint' if name=='probability' else name]=value
        from .core import clean_json
        from .project_results import save
        if self.remote_locked:
            # Measured poses can override the rotation centres without editing
            # engineering parameters. Never save that frame as a matching normal
            # project result. Keep a valid existing engineering sidecar instead.
            try:
                old=json.loads(path.read_text(encoding='utf8'));clean=clean_json(project)
                keys=('sha256','top_cell','roles','parameters','settings','node_edits','deleted_shapes','layer_order')
                if all(old.get(k)==clean.get(k) for k in keys) and old.get('saved_results'):project['saved_results']=old['saved_results']
            except (OSError,ValueError):pass
        else:
            try:project['saved_results']=save(self,path)
            except (ValueError,TypeError,OSError) as exc:self.log_bus.emit('结果未保存：'+str(exc),'WARNING')
        atomic_save(path,clean_json(project));self.project_path=path
        self._project_cache_root=path.parent/'Cache'/'runtime';self.cache_group.relocate(self._project_cache_root)
        from .session_cache import logical_fingerprint
        cache_id=logical_fingerprint((str(path),self.data.sha256,self.data.top_name))
        if cache_id!=self.cache_project_key:
            # First save names the same engineering session; keep solved poses.
            previous=self.cache_registry.retire(cache_id)
            if previous is not None:self.retired_sessions.append(previous)
            old_cache_key=self.cache_project_key
            self.cache_registry.rekey(old_cache_key,cache_id);self.cache_project_key=cache_id
            self.edit_history.rekey(old_cache_key,cache_id)
            self.cleanup_retired_sessions();self.refresh_cache_status()
    def load_project(self,path):
        try:
            from .jobs import resolve_project_path
            path=resolve_project_path(path);project=normalize_project(json.loads(path.read_text(encoding='utf-8')));gds=Path(project['gds_path'])
            if not gds.is_absolute():gds=path.parent/gds
            if self.remote_locked:
                saved=project['overlays']
                self.measured_output.saved_overlays={name:saved.get('footprint' if name=='probability' else name,False) for name in self.measured_output.saved_overlays}
                project['overlays']={key:False for key in OVERLAY_DEFAULTS}
            self.load_gds(gds,project.get('top_cell'),project,project_path=path)
        except Exception as exc:self.refresh_jobs();self.error(exc)
    def closeEvent(self,event):
        measured=getattr(self,'measured_output',None)
        if measured and (measured.active() or measured.running or measured.stopping):
            self._closing=True
            if measured.active():measured.stop(disable=True)
            if measured.running or measured.stopping:
                event.ignore();QtCore.QTimer.singleShot(100,self.close);return
        self.stop_animation();self.cut_drag.cancel(redraw=False);self.width_yield.reset();self.reconstruction_yield.reset();self.constraint_feedback.cancel()
        if hasattr(self,'mouse_die'):self.mouse_die.pending=False;self.mouse_die.full_pending=False;self.mouse_die.timer.stop();self.mouse_die.idle.stop()
        running=[w for w in self.findChildren(Worker) if w.isRunning()]
        # The physical thread may have returned before Qt delivers its queued
        # finished handlers. Keep their QObject parent alive through delivery.
        if running or self._tasks:
            self._closing=True
            if not self._tasks:QtCore.QTimer.singleShot(10,self.close)
            self.preview_pending=None;self.preview_ticket+=1;self.motion_pending=None;self.motion_generation+=1;self.coverage_timer.stop();self._coverage_pending=False
            for w in running:w.cancelled.set()
            event.ignore();return
        self._closing=True;self._queued_node_edit=None
        for timer in (self.cache_status_timer,self.viewport_timer,self.coverage_timer,self.issue_navigation,self.runtime_timer):timer.stop()
        W.QApplication.instance().removeEventFilter(self.wheel_filter)
        paths=self.job_watcher.directories()+self.job_watcher.files()
        if paths:self.job_watcher.removePaths(paths)
        self.cache_registry.close();self.cleanup_retired_sessions()
        self.runtime_capture.close();event.accept()


def launch(project=None,measured=False,ipc_endpoint=None):
    app=W.QApplication.instance() or W.QApplication([]);app.setApplicationName('Die Shift Analyze')
    startup_status={'failed':False}
    window=MainWindow(measured=measured);window.requested_ipc_endpoint=ipc_endpoint;window.show()
    if measured:
        def startup_error(message):
            window.log_bus.emit(str(message),'ERROR');window._closing=True;startup_status['failed']=True
            window.close()
        window.error=startup_error
        def activate():
            if window._closing:return
            if window.worker is None and not window.preview_workers and not window._tasks:
                window.measured_output.toggle.setChecked(True)
                if window.measured_output.active():window.error=MainWindow.error.__get__(window,MainWindow)
            else:QtCore.QTimer.singleShot(100,activate)
        QtCore.QTimer.singleShot(150,activate)
    if project:QtCore.QTimer.singleShot(0,lambda:window.load_project(project))
    code=app.exec() if hasattr(app,'exec') else app.exec_()
    QtCore.QCoreApplication.sendPostedEvents(None,getattr(QtCore.QEvent,'Type',QtCore.QEvent).DeferredDelete)
    return 1 if startup_status['failed'] else code
