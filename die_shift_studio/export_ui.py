"""Native save picker and background export of a frozen completed scene."""
from pathlib import Path
from .qt_compat import QtWidgets as W,run_dialog,ACCEPTED


def export_dxf(window,regions=False):
    if window.data is None:
        window.error('请先打开GDS');return
    if window.worker is not None or getattr(window,'preview_workers',None) or getattr(window,'preview_pending',None) or getattr(getattr(window,'cut_drag',None),'state',None):
        window.error('图纸操作尚未完成，请完成后再输出');return
    if regions and (window.case is None or window.engine is None):
        window.error('请先设置封装、芯片及金属层用途，并完成一次重构预演');return
    root=Path(window.dxf_directory) if getattr(window,'dxf_directory','') else Path(window.data.path).parent
    if not root.is_dir():root=Path(window.data.path).parent
    dialog=W.QFileDialog(window,'动静区输出' if regions else 'DXF输出',str(root/(Path(window.data.path).stem+('_ABC' if regions else '')+'.dxf')),'DXF (*.dxf)')
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
    flags=dict(reconstruction=window.reconstruction_toggle.isChecked(),
               repairs=window.repair_data if window.repair_toggle.isChecked() else None)
    if frame is not None and getattr(window,'preview_pending',None):
        window.error('当前预演仍在更新，请显示完成后再输出');return
    roles=window.roles()
    if regions:
        from .region_contract import EXPORT_ROLES
        selected=frozenset(k for k in selected if roles.get(k) in EXPORT_ROLES)
        if not selected:window.error('所选范围没有封装边框、芯片边框或固定RDL');return
    process=window.params();view=window.view_settings;model=window.engine.contact if regions else None
    export_plans={}
    if regions and getattr(window.engine,'reconstructor',None) is not None:
        from .reconstruction_status import clone_plan_pool
        export_plans=clone_plan_pool(window.engine.reconstructor.plan_pool)
    def work(progress):
        from .fast_preview import GeometryCache
        from .dxf_export import snapshot,write
        progress(0,'冻结当前GDS几何')
        current_frame=frame
        if regions:
            split=(frame or {}).get('reconstruction')
            reusable=bool(flags['reconstruction'] and split and split.get('complete') and split.get('constraints_complete') and split.get('configuration')==view.reconstruction_key())
            if reusable:
                progress(4,'复用当前已完成的重构图形')
            else:
                from .reconstruction import Reconstructor
                import numpy as np
                rebuild=Reconstructor(model,process,view,plan_pool=export_plans)
                try:
                    states=frame['states'] if frame is not None else np.zeros((len(model.case['dies']),3))
                    split=rebuild.frame(states,progress=progress)
                    current_frame=dict(frame or {},states=states,reconstruction=split)
                finally:rebuild.close()
            flags['reconstruction']=True
        current=snapshot(data,cache or GeometryCache(data),current_frame,progress=lambda n,t:progress(round(n*.2),t),**flags)
        from dataclasses import replace
        current=replace(current,layers={k:v for k,v in current.layers.items() if k in selected})
        if regions:
            from .region_export import write_regions
            result=write_regions(current,split,roles,destination,lambda n,t:progress(20+round(n*.8),t),settings=options)
        else:result=write(current,destination,lambda n,t:progress(20+round(n*.8),t),settings=options)
        result['states']=current.states.tolist() if current.states is not None else None
        return result
    def complete(result):
        message=f"DXF已输出：{result['path']} · {result['polygons']}个图形，{result['layers']}层；Flatten已完成，Merge {'开启' if result['merge_layers'] else '关闭'}（{result['input_polygons']}→{result['merged_polygons']}）；比例{result['scale']:g}，单位μm。"
        if result.get('regions'):
            message+=' A动区／B平移区／C静区，仅输出边框及固定RDL；开放轮廓已回读检查闭合与重复，无HATCH。'
            if result.get('unresolved_regions'):message+=f" {result['unresolved_regions']}个重构区域未完成有效连接；输出当前实际实体，红色提示区不导出。"
        if result['ignored']:message+=f"已跳过导入时未能识别的{result['ignored']}个图形。"
        window.log_bus.emit(message);window.statusBar().showMessage(message)
    window.start_worker(work,complete)
