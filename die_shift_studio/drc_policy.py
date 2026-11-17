"""Global per-rule calculation and presentation policy (independent switches)."""
CATEGORIES=(('width','线宽'),('space','线距 / 异网接触'),('enclosure','过孔包围量'),('angle','最小内角'),('board','图形距封装边框'),('die_gap','芯片间机械净距'),('die_edge','芯片距封装边框'),('keepout','禁布区'))
ALL=tuple(k for k,_ in CATEGORIES)
def enabled(settings,key):return key in getattr(settings,'drc_checks',ALL)
def category(kind):
    if '芯片间' in kind:return 'die_gap'
    if '芯片到封装' in kind:return 'die_edge'
    if '包围' in kind:return 'enclosure'
    if '内角' in kind:return 'angle'
    if '禁布' in kind:return 'keepout'
    if '封装边' in kind:return 'board'
    if '线宽' in kind:return 'width'
    if '线距' in kind or '异网' in kind:return 'space'
    return None

def visible(settings,row):
    key=category(row['kind'])
    return key is None or (enabled(settings,key) and key in getattr(settings,'drc_visible',ALL))

def build(w,layout):
    from .qt_compat import QtWidgets as W
    _,_,form=w.collapsible(layout,'DRC',w.setting_sections)
    class RuleEditor(W.QWidget):
        from .qt_compat import Signal
        valueChanged=Signal()
        def __init__(self,key):
            super().__init__();self.key=key;self.boxes={}
        def value(self):return tuple(k for k,b in self.boxes.items() if b.isChecked())
        def setValue(self,values):
            for k,b in self.boxes.items():b.blockSignals(True);b.setChecked(k in values);b.blockSignals(False)
    show=RuleEditor('drc_visible');check=RuleEditor('drc_checks')
    # Editors are holders for the normal saved-settings field protocol;
    # actual checkboxes share rows for compact, explicit category labels.
    w.setting_fields['drc_visible']=show;w.setting_fields['drc_checks']=check
    w._drc_policy_editors=(show,check)
    for key,title in CATEGORIES:
        row=W.QWidget();box=W.QHBoxLayout(row);box.setContentsMargins(0,0,0,0)
        for editor,label in ((show,'显示'),(check,'检查')):
            b=W.QCheckBox(label);b.setChecked(True);editor.boxes[key]=b;box.addWidget(b);b.toggled.connect(editor.valueChanged)
        form.addRow(title,row)
    show.valueChanged.connect(w.settings_changed);check.valueChanged.connect(w.settings_changed)
