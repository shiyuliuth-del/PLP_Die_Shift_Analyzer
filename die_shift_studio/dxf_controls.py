"""Collapsible export preferences; no control mutates numerical/visible geometry."""
from dataclasses import asdict
from .qt_compat import QtWidgets as W,Qt
from .dxf_settings import DxfSettings

def build(window,layout):
    header,body,form=window.collapsible(layout,'DXF输出参数',window.setting_sections)
    hint=W.QLabel('动静区输出：_A 运动侧、_B 固定侧、_C 动区，同一图形按层成块。PKG_REF 为封装边框，DIE_REF 为芯片边框。应用图层范围和比例。');hint.setWordWrap(True);form.addRow(hint)
    fields={};window.dxf_fields=fields
    for key,title,items in (
        ('layer_scope','导出图层',[('全部已导入图层','all'),('当前显示图层','visible'),('参与分析图层','participating')]),
        ('polygon_mode','DXF实体',[('闭合轮廓；含孔图形保留HATCH','outline'),('全部使用HATCH面','hatch')])):
        box=W.QComboBox()
        for text,value in items:box.addItem(text,value)
        fields[key]=box;form.addRow(title,box)
    for key,title in (('merge_layers','Merge 导出层图案'),('separate_holes','孔洞单独输出到 _holes 图层'),('show_fill','显示 HATCH 填充'),('region_auto_join','动静区自动JOIN')):
        box=W.QCheckBox(title);fields[key]=box;form.addRow(box)
    fields['layer_scope'].setToolTip('“当前显示图层”按显示勾选。参与分析不含“不参与”及“一并导出”。动静区输出始终附带“一并导出”图层。')
    fields['merge_layers'].setToolTip('默认开启：在独立导出副本内逐层合并相接、重叠和重复图形。关闭仍输出已展开的顶层几何，不恢复层级。')
    fields['separate_holes'].setToolTip('主图层写外轮廓，孔洞写同名_holes层；目标CAD须将孔洞层作为减法掩膜，不能直接视为同层实心铜。')
    fields['show_fill'].setToolTip('只改变DXF的FILLMODE显示偏好；仍保留HATCH实体与孔洞。')
    fields['region_auto_join'].setToolTip('默认开启。勾选后，动静区DXF中每个块内首尾相接的线段将自动合并为一条长多段线，闭合环以闭合多段线输出。')
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
