"""APD rule export controller for the current drawing and pose."""
from pathlib import Path

from .qt_compat import ACCEPTED, QtWidgets as W, run_dialog


def export(window):
    if window.remote_locked or window.worker is not None:return
    if window.data is None or window.case is None:
        window.error('请先打开图纸并设置有效的芯片和封装边框')
        return
    from .apd_export import template,rule_regions,write
    from .contact import ContactModel
    from .fast_preview import GeometryCache
    from .space_map import space_maps
    data,roles,case=window.data,window.roles(),window.case
    record=case['connections']
    fixed=sorted({target for layer,role in roles.items() if role=='运动过孔'
                  for index in range(len(data.layers.get(layer,())))
                  for target,_ in record.fixed_interfaces(layer,index)})
    if not fixed:
        window.error('当前图纸没有连接运动过孔的固定 RDL')
        return
    saved=window.apd_options
    source_path,_=W.QFileDialog.getOpenFileName(window,'选择 APD 23.1 规则模板',
        saved.get('template',''),'Cadence 规则模板 (*.dcfx)')
    if not source_path:return
    try:source=template(source_path)
    except (OSError,ValueError,SyntaxError) as exc:
        window.error(str(exc));return
    dialog=W.QDialog(window);dialog.setWindowTitle('APD 导体层对应')
    layout=W.QFormLayout(dialog)
    selectors={}
    for key in fixed:
        box=W.QComboBox();box.addItems(['',*source[4]])
        remembered=saved.get('layers',{}).get(key)
        if remembered in source[4]:box.setCurrentText(remembered)
        elif len(source[4])==1:box.setCurrentIndex(1)
        selectors[key]=box;layout.addRow(f'固定 RDL {key}',box)
    buttons=W.QDialogButtonBox(getattr(W.QDialogButtonBox,'StandardButton',W.QDialogButtonBox).Ok |
                              getattr(W.QDialogButtonBox,'StandardButton',W.QDialogButtonBox).Cancel)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addRow(buttons)
    if run_dialog(dialog)!=ACCEPTED:return
    layers={key:box.currentText() for key,box in selectors.items()}
    if not all(layers.values()) or len(set(layers.values()))!=len(layers):
        window.error('请为每个固定 RDL 选择不同的 APD 导体层')
        return
    suggested=Path(data.path).with_name(Path(data.path).stem+'_spacing.dcfx')
    path,_=W.QFileDialog.getSaveFileName(window,'APD规则输出',str(suggested),'Cadence 规则 (*.dcfx)')
    if not path:return
    if Path(path).with_suffix('.dcfx').resolve()==Path(source_path).resolve():
        window.error('请选择与 APD 模板不同的输出文件')
        return
    window.stop_animation()
    states=None if window.shown_states is None else window.shown_states.copy()
    parameters=window.params();settings=window.view_settings
    case=dict(case);cache=window.geometry_cache
    def work(progress):
        progress(10,'计算固定 RDL 避让空间')
        model=ContactModel(data,case,roles,record,cache or GeometryCache(data),parameters=parameters)
        field=space_maps(model,parameters,settings.space_resolution)
        progress(50,'生成离散 APD 约束区域')
        bands=rule_regions(field,case['board'],model.bound.centers,states,
                           settings.space_export_interval_um,settings.space_scope)
        progress(85,'写出 DCFX 与版图区域脚本')
        return write(path,bands,{key:layers[key] for key in bands},source)
    def complete(result):
        dcfx,script,count=result
        window.apd_options=dict(template=source_path,layers=layers)
        window.log_bus.emit(f'APD 已导出 {count} 个区域。先加载 {script} 并执行 dsa_apd_regions()，再合并导入 {dcfx}。')
        W.QMessageBox.information(window,'APD规则输出',f'已生成 {count} 个约束区。\n\n'
            f'在 APD 加载 {script.name} 并执行 dsa_apd_regions()。\n'
            f'然后在 Constraint Manager 中以 Merge 模式导入 {dcfx.name}。')
    window.start_worker(work,complete,interactive=True)
