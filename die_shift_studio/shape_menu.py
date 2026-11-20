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
    picked=None
    if window.reconstruction_toggle.isChecked():
        picked=window.reconstruction_overlay.pick(*point,window.cut_drag._tolerance())
        if not selection.selected and picked and picked[2] in ('moved','extra'):
            selection.apply_hits([(picked[0]['layer'],picked[0]['index'])],'replace')
    if not selection.selected:
        tolerance=3*abs(window.ax.get_xlim()[1]-window.ax.get_xlim()[0])/max(1,window.ax.bbox.width)
        hits=selection.hits(Point(point).buffer(tolerance),point=True)
        if not hits:return
        selection.last_pixel=None
        selection.apply_hits(hits[:1],'replace')
    refs=tuple(selection.selected)
    menu=W.QMenu(window.canvas)
    if len(refs)==1:
        key,i=refs[0];kind,xy,_=shape_nodes(window.data,key,i)
        summary=f'{key} #{i+1} · {kind} · {len(xy)} 个名义节点'
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
        at_cursor=[ref for ref in selection.hits(Point(point).buffer(window.cut_drag._tolerance()),point=True) if window.roles().get(ref[0])=='固定RDL']
        if picked or at_cursor:
            ref=(picked[0]['layer'],picked[0]['index']) if picked else at_cursor[0]
            action=menu.addAction('增加动区',lambda:edit_zone('add',ref=ref,point=point))
            if solver is None or ref not in solver.vias_by_target:
                action.setEnabled(False);action.setToolTip('此固定RDL没有可追溯的运动过孔连接')
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
    ratio=window.canvas.devicePixelRatioF()
    pos=QtCore.QPoint(round(event.x/ratio),round(window.canvas.height()-event.y/ratio))
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(window.canvas.mapToGlobal(pos))
