"""Non-modal canvas menu. The source selection owns the identity across previews."""
from shapely.geometry import Point
from .gds_shapes import shape_nodes
from .qt_compat import QtWidgets as W,QtCore


def issue_refs(window):
    frame=window.current_frame or {}
    data=frame.get('width_analysis') or {}
    refs={tuple(ref) for row in data.get('rows',()) if row['status']!='宽度足够' for ref in row['refs']}
    refs.update((row['layer'],i) for row in data.get('edge_violations',()) for i in row['indices'])
    summary=window.width_overlay.shown_summary() or {}
    return refs.union(summary.get('lines',{}))


def show_shape_menu(window,event,point):
    if window.data is None:return
    selection=window.selection
    picked=None;between=None
    if window.reconstruction_toggle.isChecked():
        picked=window.reconstruction_overlay.pick(*point,window.cut_drag._tolerance())
        current=getattr(selection,'selected_between',None)
        if current is not None and current['geometry'].contains(Point(point)):
            # Give the selected strip its own menu even if it is narrower than
            # the two adjacent boundary handles' pointer tolerance.
            picked=None
        if picked is None:
            between=current if current is not None and current['geometry'].covers(Point(point)) else selection.find_between_block(point)
            if between is None and current is not None and not selection.selected:
                between=current
        if between is not None:
            selection.selected=[];selection.selected_between=between;selection.draw()
        elif not selection.selected and picked and picked[2] in ('moved','extra'):
            selection.apply_hits([(picked[0]['layer'],picked[0]['index'])],'replace')
    if between is None and not selection.selected:
        tolerance=3*abs(window.ax.get_xlim()[1]-window.ax.get_xlim()[0])/max(1,window.ax.bbox.width)
        hits=selection.hits(Point(point).buffer(tolerance),point=True)
        if not hits:return
        selection.last_pixel=None
        selection.apply_hits(hits[:1],'replace')
    refs=tuple(selection.selected) if between is None else (between['ref'],)
    menu=W.QMenu(window.canvas)
    if between is not None:
        from .between_motion import LABELS,MODES
        ref=between['ref'];cur_mode=between['mode']
        policy='由端点约束' if between.get('locked') else LABELS.get(cur_mode,cur_mode)
        summary=f"动区间图形 · {ref[0]} #{ref[1]+1} · 当前：{policy}"
        menu.addAction(summary).setEnabled(False)
        if not getattr(window,'remote_locked',False) and not between.get('locked'):
            source_data=window.data;source_group=window.cache_group;block_id=between['id']
            def set_between(mode):
                if window.data is source_data and window.cache_group is source_group and window.reconstruction_toggle.isChecked():
                    window.change_between_motion(block_id,mode)
            for mode in MODES:
                prefix='✓ ' if cur_mode==mode else ''
                menu.addAction(f'{prefix}动区间随动：{LABELS[mode]}',lambda m=mode:set_between(m))
            if between.get('custom'):
                menu.addAction('恢复默认动区间随动逻辑',lambda:set_between(None))
            menu.addSeparator()
    elif len(refs)==1:
        key,i=refs[0];kind,xy,_=shape_nodes(window.data,key,i)
        summary=f'{key} #{i+1} · {kind} · {len(xy)} 个名义节点'
        pending=((getattr(getattr(window,'reconstruction_overlay',None),'staged_overlay',None) or {}).get('actions',{})).get(refs[0])
        if pending and getattr(window,'reconstruction_pending',False):
            summary+=' · 待算：'+' + '.join(pending)
        menu.addAction(summary).setEnabled(False)
    else:
        summary=f'已选 {len(refs)} 个图形 · {len({k for k,_ in refs})} 层'
        menu.addAction(summary).setEnabled(False)
    if window.width_toggle.isChecked():
        ignored=window.ignored_width_lines;selected=set(refs)
        add=(issue_refs(window)&selected)-ignored;remove=ignored&selected
        if add or remove:menu.addSeparator()
        # Discard callbacks from a previous project or a subsequently edited GDS.
        data=window.data;group=window.cache_group
        def change(items,value):
            if window.data is data and window.cache_group is group and window.width_toggle.isChecked():
                window.change_width_ignores(items,value)
        if add:menu.addAction('忽略选中的问题线路',lambda:change(add,True))
        if remove:menu.addAction('取消忽略选中的问题线路',lambda:change(remove,False))
    if window.reconstruction_toggle.isChecked() and not getattr(window,'remote_locked',False):
        from .reconstruction_edits import change_zone
        source_data=window.data;source_group=window.cache_group
        def edit_zone(action,**kwargs):
            if window.data is source_data and window.cache_group is source_group and window.reconstruction_toggle.isChecked():change_zone(window,action,**kwargs)
        roles=window.roles()
        selected={r for r in refs if roles.get(r[0])=='固定RDL'}
        solver=getattr(getattr(window,'engine',None),'reconstructor',None)
        frame=window.current_frame or {}
        displayed=(window.reconstruction_overlay.data() if getattr(window,'reconstruction_overlay',None) else None) or frame.get('reconstruction') or {}
        pieces=displayed.get('pieces',())
        zoned={(q['layer'],q['index']) for q in pieces if q.get('route_plan') is not None}
        if picked:
            piece,_,kind=picked
            if kind in ('moved','extra'):
                from .branch_regions import editable_piece
                member=editable_piece(piece,point,kind)
                menu.addAction('撤销旋转',lambda:edit_zone('rotate_reset',piece=member,side=1 if kind=='extra' else 0))
            menu.addAction('删除动区',lambda:edit_zone('delete',piece=piece))
        delete_all={(picked[0]['layer'],picked[0]['index'])} if picked else selected&zoned
        if delete_all:
            menu.addAction('删除全部动区',lambda:edit_zone('delete_all',refs=delete_all))
        selected_roles={roles.get(layer) for layer,_ in refs}
        if selected_roles!={'固定过孔'}:
            hits=selection.hits(Point(point).buffer(window.cut_drag._tolerance()),point=True)
            at_cursor=[ref for ref in hits if roles.get(ref[0])=='固定RDL']
            if selected_roles=={'运动过孔'}:
                via=next((item for item in hits if item in refs),refs[0])
                targets=solver.model.connections.fixed_interfaces(*via) if solver is not None else ()
                ref=next((item for item in at_cursor if item in targets),targets[0] if targets else None)
            else:
                ref=(picked[0]['layer'],picked[0]['index']) if picked else at_cursor[0] if at_cursor else None
            if ref is not None or selected_roles=={'运动过孔'}:
                action=menu.addAction('增加动区',lambda:edit_zone('add',ref=ref,point=point))
                if solver is None or ref not in solver.vias_by_target:
                    action.setEnabled(False);action.setToolTip('没有可重构的运动过孔与固定RDL连接')
        if solver is not None and window.view_settings.reconstruction_fixed_via_mode=='individual':
            from .ignored_motion import linked_fixed_vias
            eligible=set(linked_fixed_vias(solver,pieces))&set(refs)
            selected_vias=set(window.view_settings.reconstruction_fixed_via_follows)
            def via_follow(items,enabled):
                if window.data is source_data and window.cache_group is source_group and window.reconstruction_toggle.isChecked():
                    window.change_fixed_via_following(items,enabled)
            if eligible-selected_vias:
                menu.addAction('固定过孔随动',lambda:via_follow(eligible-selected_vias,True))
            if eligible&selected_vias:
                menu.addAction('固定过孔恢复静止',lambda:via_follow(eligible&selected_vias,False))
        ignored=set(window.view_settings.reconstruction_ignored)|set(displayed.get('auto_ignored',{}))
        add=selected-ignored;remove=selected&ignored
        data=window.data;group=window.cache_group
        def change_reconstruction(items,value):
            if window.data is data and window.cache_group is group and window.reconstruction_toggle.isChecked():
                window.change_reconstruction_ignores(items,value)
        if add or remove:menu.addSeparator()
        if add:menu.addAction('忽略选中图形的重构',lambda:change_reconstruction(add,True))
        if remove:
            menu.addAction('取消忽略选中图形的重构',lambda:change_reconstruction(remove,False))
            menu.addAction('忽略图形：整块静止',lambda:window.change_reconstruction_mode(remove,False))
            menu.addAction('忽略图形：整块随动',lambda:window.change_reconstruction_mode(remove,True))
        waived=set(window.view_settings.drc_waived_refs)
        def change_figure_drc(items,enabled):
            if window.data is source_data and window.cache_group is source_group:
                window.change_drc_figure_waivers(items,enabled)
        menu.addSeparator()
        if set(refs)-waived:
            menu.addAction('忽略选中图形的DRC',lambda:change_figure_drc(set(refs)-waived,True))
        if waived.intersection(refs):
            menu.addAction('取消忽略DRC',lambda:change_figure_drc(waived.intersection(refs),False))
    ratio=window.canvas.devicePixelRatioF()
    pos=QtCore.QPoint(round(event.x/ratio),round(window.canvas.height()-event.y/ratio))
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(window.canvas.mapToGlobal(pos))
