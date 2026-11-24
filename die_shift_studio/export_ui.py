"""Native save picker and background export of a frozen completed scene."""
from pathlib import Path
from .qt_compat import QtWidgets as W,run_dialog,ACCEPTED
from .layer_roles import EXPORT_ONLY,NON_PARTICIPATING


def import_regions(window):
    if window.remote_locked:return
    if window.data is None:
        window.error('请先打开原始 GDS/OAS 并设置图层用途');return
    if window.worker is not None or window.cut_drag.state is not None or window.pending_layout_ops or window.pending_node_shapes:
        window.error('请先完成当前任务及待应用的图纸编辑');return
    data=window.data
    root=window.dxf_directory or str(Path(data.path).parent)
    name,_=W.QFileDialog.getOpenFileName(window,'动静区输入',root,'DXF (*.dxf)')
    if not name:return
    roles,order,motions=window.roles(),window.ordered_layers(),window.motions()
    params,settings=window.params(),window.view_settings
    geometry=window.geometry_cache;case=window.case
    compensation=window.output_compensation();window_options=window.dxf_settings

    def work(progress):
        from .contact import ContactModel
        from .core import build_case
        from .fast_preview import GeometryCache
        from .region_import import configure,read,read_domains,recover_metadata,validate_rebuild
        from .resources import progress_slice
        if progress:progress(0,'读取动静区 DXF 并匹配源图形')
        contours,record=read(name,with_metadata=True)
        cache=geometry or GeometryCache(data)
        current=case or build_case(data,roles,geometry=cache.geometries,layer_order=order)
        model=ContactModel(data,current,roles,current['connections'],cache,motions,params)
        verification=progress_slice(progress,18,.82)
        try:
            restored=recover_metadata(record,contours,data,roles,order,params,settings)
            if restored is not None:
                from .dxf_settings import DxfSettings
                from .output_compensation import normalize
                options=DxfSettings.from_dict(record.get('dxf'))
                offsets=normalize(record.get('compensation'))
                if validate_rebuild(model,params,restored,contours,options,offsets,verification,references=record.get('references')):
                    return restored,len(contours),'已核对原生动区及输出轮廓'
        except (TypeError,ValueError,OverflowError):
            if progress:progress(18,'原生记录无法复核，依据 DXF 轮廓导入')
        try:
            domains=read_domains(contours,cache,roles,compensation,window_options.scale,data.dbu_um)
            return configure(model,params,settings,domains,verification),len(contours),'已核对源铜、切口及实际动区'
        except ValueError:
            # Legacy files only carry exterior outlines. If they match the
            # current generated layout, the existing settings resolve them.
            if validate_rebuild(model,params,settings,contours,window_options,compensation,verification):
                return settings,len(contours),'与当前名义动静区一致'
            raise

    def complete(out):
        if window.data is not data or window.view_settings!=settings or window.remote_locked:return
        updated,count,reason=out
        window.edit_history.record()
        window.view_settings=updated
        window.dxf_directory=str(Path(name).parent)
        window.reconstruction_pending=False
        window._reconstruction_apply_once=True
        overlay=window.reconstruction_overlay
        overlay.staged_overlay=None;overlay.draft_frame=overlay.draft_data=overlay.draft_key=None;overlay.clear_hover()
        toggle=window.reconstruction_toggle
        blocked=toggle.blockSignals(True);toggle.setChecked(True);toggle.blockSignals(blocked)
        window.invalidate_reconstruction_edit()
        window.request_preview(quiet=True)
        window.log_bus.emit(f'已按 DXF 名义坐标应用 {count} 层动静区定义。{reason}。')

    window.start_worker(work,complete)


def export_dxf(window,regions=False):
    if window.data is None:
        window.error('请先打开GDS');return
    if window.worker is not None or getattr(window,'preview_workers',None) or getattr(window,'preview_pending',None) or getattr(getattr(window,'cut_drag',None),'state',None) or getattr(window,'reconstruction_pending',False) or getattr(window,'pending_layout_ops',None):
        window.error('请先完成当前预演及待应用的重构修改后再输出');return
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
    roles=window.roles()
    selected=set(data.layers) if options.layer_scope=='all' else {k for k,v in window.visible_boxes.items() if v.isChecked()} if options.layer_scope=='visible' else {k for k,v in roles.items() if v not in NON_PARTICIPATING}
    flags=dict(reconstruction=window.reconstruction_toggle.isChecked())
    if frame is not None and getattr(window,'preview_pending',None):
        window.error('当前预演仍在更新，请显示完成后再输出');return
    if regions:
        from .region_contract import EXPORT_ROLES
        selected={k for k in selected if roles.get(k) in EXPORT_ROLES}
        selected.update(k for k,v in roles.items() if v==EXPORT_ONLY)
    if not selected:window.error('所选导出范围没有图层');return
    process=window.params();view=window.view_settings;model=window.engine.contact if regions else None
    order=window.ordered_layers() if regions else ()
    compensation=window.output_compensation()
    export_plans={}
    if regions and getattr(window.engine,'reconstructor',None) is not None:
        from .reconstruction_status import clone_plan_pool
        export_plans=clone_plan_pool(window.engine.reconstructor.plan_pool)
    def work(progress):
        from .fast_preview import GeometryCache
        from .dxf_export import snapshot,write
        from .resources import progress_slice
        if progress:progress(0,'冻结当前GDS几何')
        current_frame=frame
        if regions:
            split=(frame or {}).get('reconstruction')
            reusable=bool(frame is not None and not __import__('numpy').any(frame['states']) and flags['reconstruction'] and split and split.get('complete') and split.get('constraints_complete') and split.get('configuration')==view.reconstruction_key())
            if reusable:
                if progress:progress(4,'复用当前已完成的重构图形')
            else:
                from .reconstruction import Reconstructor
                import numpy as np
                rebuild=Reconstructor(model,process,view,plan_pool=export_plans)
                try:
                    states=np.zeros((len(model.case['dies']),3))
                    split=rebuild.frame(states,progress=progress)
                    current_frame=dict(states=states,layers=model.bound.transform(states),reconstruction=split)
                finally:rebuild.close()
            split=dict(split,pieces=[dict(q,regions=q['nominal_regions']) for q in split['pieces']])
            flags['reconstruction']=False
            current_frame=dict(states=__import__('numpy').zeros((len(model.case['dies']),3)))
        current=snapshot(data,cache or GeometryCache(data),current_frame,progress=progress_slice(progress,0,.2),**flags)
        from dataclasses import replace
        current=replace(current,layers={k:v for k,v in current.layers.items() if k in selected})
        if regions:
            from .region_export import write_regions
            from .region_import import metadata_for
            result=write_regions(current,split,roles,destination,progress_slice(progress,20,.8),settings=options,
                                 compensation=compensation,dbu=data.dbu_um,
                                 edit_metadata=metadata_for(data,roles,order,process,view,selected,options,compensation))
        else:
            from .output_compensation import compensate_snapshot
            current=compensate_snapshot(current,compensation,data.dbu_um)
            result=write(current,destination,progress_slice(progress,20,.8),settings=options)
        result['states']=current.states.tolist() if current.states is not None else None
        return result
    def complete(result):
        message=f"DXF已输出：{result['path']} · {result['polygons']}个图形，{result['layers']}层；Flatten已完成，Merge {'开启' if result['merge_layers'] else '关闭'}（{result['input_polygons']}→{result['merged_polygons']}）；比例{result['scale']:g}，单位μm。"
        if result.get('regions'):
            message+=' 固定RDL按 _A 运动侧、_B 固定侧、_C 动区输出CAD块；封装和芯片完整轮廓分别在 PKG_REF、DIE_REF。'
            if result['extra_layers']:message+=f" 已附带 {result['extra_layers']} 个标准DXF图层。"
            if result.get('unresolved_regions'):message+=f" {result['unresolved_regions']}个重构区域未完成有效连接；输出当前实际实体，红色提示区不导出。"
        if result['ignored']:message+=f"已跳过导入时未能识别的{result['ignored']}个图形。"
        window.log_bus.emit(message);window.statusBar().showMessage(message)
    window.start_worker(work,complete)
