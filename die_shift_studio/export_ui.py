"""Native save picker and background export of a frozen completed scene."""
from pathlib import Path
from .qt_compat import QtWidgets as W,run_dialog,ACCEPTED


def export_dxf(window):
    if window.data is None:
        window.error('请先打开GDS');return
    if window.worker is not None:
        window.error('图纸操作尚未完成，请完成后再输出');return
    root=Path(window.dxf_directory) if getattr(window,'dxf_directory','') else Path(window.data.path).parent
    if not root.is_dir():root=Path(window.data.path).parent
    dialog=W.QFileDialog(window,'DXF输出',str(root/(Path(window.data.path).stem+'.dxf')),'DXF (*.dxf)')
    dialog.setAcceptMode(getattr(W.QFileDialog,'AcceptMode',W.QFileDialog).AcceptSave)
    dialog.setDefaultSuffix('dxf')
    # Deliberately no stylesheet, custom options or DontUseNativeDialog.
    accepted=run_dialog(dialog)==ACCEPTED
    paths=dialog.selectedFiles() if accepted else []
    dialog.deleteLater()
    if not paths:return
    destination=Path(paths[0]);window.dxf_directory=str(destination.parent)
    window.stop_animation()
    data=window.data;cache=window.geometry_cache;frame=window.current_frame
    options=window.dxf_settings
    selected=frozenset(data.layers) if options.layer_scope=='all' else frozenset(k for k,v in window.visible_boxes.items() if v.isChecked()) if options.layer_scope=='visible' else frozenset(k for k,v in window.roles().items() if v!='不参与')
    if not selected:window.error('所选导出范围没有图层');return
    flags=dict(deformation=window.deformation_toggle.isChecked(),reconstruction=window.reconstruction_toggle.isChecked(),
               repairs=window.repair_data if window.repair_toggle.isChecked() else None)
    if frame is not None and getattr(window,'preview_pending',None):
        window.error('当前预演仍在更新，请显示完成后再输出');return
    def work(progress):
        from .fast_preview import GeometryCache
        from .dxf_export import snapshot,write
        progress(0,'冻结当前GDS几何')
        current=snapshot(data,cache or GeometryCache(data),frame,progress=lambda n,t:progress(round(n*.2),t),**flags)
        from dataclasses import replace
        current=replace(current,layers={k:v for k,v in current.layers.items() if k in selected})
        result=write(current,destination,lambda n,t:progress(20+round(n*.8),t),settings=options)
        result['states']=current.states.tolist() if current.states is not None else None
        return result
    def complete(result):
        message=f"DXF已输出：{result['path']} · {result['polygons']}个图形，{result['layers']}层；Flatten已完成，Merge {'开启' if result['merge_layers'] else '关闭'}（{result['input_polygons']}→{result['merged_polygons']}）；比例{result['scale']:g}，单位μm。"
        if result['ignored']:message+=f"已跳过导入时未能识别的{result['ignored']}个图形。"
        window.log_bus.emit(message);window.statusBar().showMessage(message)
    window.start_worker(work,complete)
