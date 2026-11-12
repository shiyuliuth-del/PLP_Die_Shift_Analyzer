"""Collapsible export preferences; no control mutates numerical/visible geometry."""
from dataclasses import asdict
from .qt_compat import QtWidgets as W,Qt
from .dxf_settings import DxfSettings

def build(window,layout):
    header=W.QToolButton();header.setText('DXF');header.setCheckable(True)
    header.setToolButtonStyle(getattr(Qt,'ToolButtonStyle',Qt).ToolButtonTextBesideIcon)
    body=W.QWidget();form=W.QFormLayout(body);body.hide()
    def expand(opened):
        body.setVisible(opened)
        header.setArrowType(getattr(Qt,'ArrowType',Qt).DownArrow if opened else getattr(Qt,'ArrowType',Qt).RightArrow)
    header.toggled.connect(expand);expand(False)
    layout.addWidget(header);layout.addWidget(body)
    window.setting_sections['DXF']=header
    info=W.QLabel('Flatten Top Cell：已启用，包含全部实例、阵列与层级变换。')
    info.setWordWrap(True);form.addRow(info)
    hint=W.QLabel('动静区输出：A动区、B静区、C平移区，仅输出相接的开放轮廓，不使用HATCH。图层范围、Merge、孔洞拆层和比例仍有效。');hint.setWordWrap(True);form.addRow(hint)
    fields={};window.dxf_fields=fields
    for key,title,items in (
        ('layer_scope','导出图层',[('全部已导入图层','all'),('当前显示图层','visible'),('参与分析图层','participating')]),
        ('polygon_mode','DXF实体',[('闭合轮廓；含孔图形保留HATCH','outline'),('全部使用HATCH面','hatch')])):
        box=W.QComboBox()
        for text,value in items:box.addItem(text,value)
        fields[key]=box;form.addRow(title,box)
    for key,title in (('merge_layers','Merge 导出层图案'),('separate_holes','孔洞单独输出到 _holes 图层'),('show_fill','显示 HATCH 填充')):
        box=W.QCheckBox(title);fields[key]=box;form.addRow(box)
    fields['layer_scope'].setToolTip('“当前显示图层”只按图层表显示勾选；宽度分析的低亮/元素隐藏不会删除实体。参与分析指用途不是“不参与”的图层。')
    fields['merge_layers'].setToolTip('默认开启：在独立导出副本内逐层合并相接、重叠和重复图形。关闭仍输出已展开的顶层几何，不恢复层级。')
    fields['separate_holes'].setToolTip('主图层写外轮廓，孔洞写同名_holes层；目标CAD须将孔洞层作为减法掩膜，不能直接视为同层实心铜。')
    fields['show_fill'].setToolTip('只改变DXF的FILLMODE显示偏好；仍保留HATCH实体与孔洞。')
    scale=W.QDoubleSpinBox();scale.setDecimals(6);scale.setRange(1e-6,1e6);scale.setKeyboardTracking(False)
    fields['scale']=scale;form.addRow('输出比例',scale)
    scale.setToolTip('以顶层原点等比例缩放最终输出坐标，默认1；文件长度单位仍为μm，不修改工程图形。')
    def changed(*_):
        values={k:w.currentData() if isinstance(w,W.QComboBox) else w.isChecked() if isinstance(w,W.QCheckBox) else w.value() for k,w in fields.items()}
        window.dxf_settings=DxfSettings.from_dict(values)
    for widget in fields.values():
        (widget.currentIndexChanged if isinstance(widget,W.QComboBox) else widget.toggled if isinstance(widget,W.QCheckBox) else widget.valueChanged).connect(changed)
    restore(window)

def restore(window):
    for key,value in asdict(window.dxf_settings).items():
        widget=window.dxf_fields[key];widget.blockSignals(True)
        if isinstance(widget,W.QComboBox):widget.setCurrentIndex(widget.findData(value))
        elif isinstance(widget,W.QCheckBox):widget.setChecked(value)
        else:widget.setValue(value)
        widget.blockSignals(False)
