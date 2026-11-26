"""Non-modal canvas menu. The source selection owns the identity across previews."""
from shapely.geometry import Point
from .gds_shapes import shape_nodes
from .qt_compat import QtWidgets as W,QtCore,QtGui,Qt

_EVENT_TYPE=getattr(QtCore.QEvent,'Type',QtCore.QEvent)
_KEY_TYPE=getattr(Qt,'Key',Qt)
_MOD_TYPE=getattr(Qt,'KeyboardModifier',Qt)


class _MenuWheelFilter(QtCore.QObject):
    def eventFilter(self,obj,event):
        if event.type()==_EVENT_TYPE.Wheel:
            delta=event.angleDelta().y() if hasattr(event,'angleDelta') else 0
            if delta:
                key=_KEY_TYPE.Key_Up if delta>0 else _KEY_TYPE.Key_Down
                steps=max(1,min(4,abs(delta)//60))
                for _ in range(steps):
                    ev=QtGui.QKeyEvent(_EVENT_TYPE.KeyPress,key,_MOD_TYPE.NoModifier)
                    W.QApplication.sendEvent(obj,ev)
                event.accept()
                return True
        return False


def _configure_scrollable_menu(menu,anchor,global_pos=None):
    hint=menu.sizeHint() if hasattr(menu,'sizeHint') else None
    hint_h=hint.height() if hint is not None and hasattr(hint,'height') else None
    if not isinstance(hint_h,int):return
    screen=None
    if global_pos is not None and hasattr(W.QApplication,'screenAt'):
        screen=W.QApplication.screenAt(global_pos)
    if screen is None and anchor is not None and hasattr(anchor,'window'):
        win=anchor.window()
        handle=win.windowHandle() if win is not None and hasattr(win,'windowHandle') else None
        if handle is not None:screen=handle.screen()
    if screen is None and hasattr(W.QApplication,'primaryScreen'):
        screen=W.QApplication.primaryScreen()
    avail_h=screen.availableGeometry().height() if screen is not None else 720
    max_h=max(220,int(avail_h*0.72))
    if hint_h>max_h:
        menu.setMaximumHeight(max_h)
        menu.setStyleSheet('QMenu { menu-scrollable: 1; }')
        flt=_MenuWheelFilter(menu)
        menu._wheel_filter=flt
        menu.installEventFilter(flt)
    for act in menu.actions():
        sub=act.menu() if hasattr(act,'menu') else None
        if sub is not None:
            _configure_scrollable_menu(sub,anchor,global_pos)


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
        candidate=(selection.find_subregion(point)
                   if hasattr(selection,'find_subregion') else (selection.find_between_block(point)
                   if hasattr(selection,'find_between_block') else None))
        if candidate is not None and candidate['geometry'].contains(Point(point)):
            # The interior is the copper's menu even when both handle hit
            # circles overlap it. The actual boundary still has its own menu.
            picked=None
            between=candidate
        elif picked is None:
            between=candidate
            if between is None and current is not None and not selection.selected:between=current
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
        from .between_motion import ENDPOINT_LABELS,ENDPOINT_MODES,LABELS,SPECIAL_MODES,STATIONARY,die_mode,mode_label
        ref=between['ref'];cur_mode=between['mode']
        if 'kind' in between:
            kind_label='固定端铜区' if between['kind']=='fixed' else '运动端铜区'
            policy=ENDPOINT_LABELS.get(cur_mode,cur_mode)+('（强制）' if between.get('custom') else '（默认）')
            menu.addAction(f"{kind_label} · {ref[0]} #{ref[1]+1} · 当前：{policy}").setEnabled(False)
            if not getattr(window,'remote_locked',False):
                source_data=window.data;source_group=window.cache_group;block_id=between['id']
                def set_endpoint(mode):
                    if window.data is source_data and window.cache_group is source_group and window.reconstruction_toggle.isChecked():
                        window.change_endpoint_motion(block_id,mode)
                for mode in ENDPOINT_MODES:
                    prefix='✓ ' if cur_mode==mode else ''
                    menu.addAction(f'{prefix}端点随动：{ENDPOINT_LABELS[mode]}',lambda *_,m=mode:set_endpoint(m))
                if between.get('custom'):
                    menu.addAction('恢复默认端点随动（随对应过孔）',lambda:set_endpoint(None))
                menu.addSeparator()
        else:
            policy='由端点约束' if between.get('locked') else mode_label(cur_mode)
            summary=f"动区间图形 · {ref[0]} #{ref[1]+1} · 当前：{policy}"
            menu.addAction(summary).setEnabled(False)
            if between.get('locked'):
                menu.addAction('该段铜与端孔或归属接缝相连，随动由连接约束决定').setEnabled(False)
            if not getattr(window,'remote_locked',False) and not between.get('locked'):
                source_data=window.data;source_group=window.cache_group;block_id=between['id']
                def set_between(mode):
                    if window.data is source_data and window.cache_group is source_group and window.reconstruction_toggle.isChecked():
                        window.change_between_motion(block_id,mode)
                prefix='✓ ' if cur_mode==STATIONARY else ''
                menu.addAction(f'{prefix}动区间随动：{LABELS[STATIONARY]}',lambda *_:set_between(STATIONARY))
                die_count=len((getattr(window,'case',None) or {}).get('dies',())) or len(getattr(getattr(getattr(window,'engine',None),'bound',None),'centers',()))
                for d in range(die_count):
                    dm=die_mode(d)
                    dprefix='✓ ' if cur_mode==dm else ''
                    menu.addAction(f'{dprefix}动区间随动：{mode_label(dm)}',lambda *_,m=dm:set_between(m))
                special_prefix='✓ ' if cur_mode in SPECIAL_MODES else ''
                special=menu.addMenu(f'{special_prefix}特殊随动跟随')
                for mode in SPECIAL_MODES:
                    sprefix='✓ ' if cur_mode==mode else ''
                    special.addAction(f'{sprefix}动区间随动：{LABELS[mode]}',lambda *_,m=mode:set_between(m))
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
            overlay=getattr(window,'reconstruction_overlay',None)
            if piece.get('box_groups') and hasattr(overlay,'linked_regions'):
                linked,groups=overlay.linked_regions(piece)
                if len(linked)>1:
                    def unlink():
                        if (window.data is not source_data or window.cache_group is not source_group
                                or window.remote_locked or not window.reconstruction_toggle.isChecked()):return
                        from dataclasses import replace
                        cfg=window.view_settings
                        ids=tuple(sorted(set(cfg.reconstruction_unlinked_boxes)|groups))
                        window.apply_reconstruction_edit(replace(cfg,reconstruction_unlinked_boxes=ids),'拆分绑定动区')
                    menu.addAction('拆分绑定动区',unlink)
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
    global_pos=window.canvas.mapToGlobal(pos)
    _configure_scrollable_menu(menu,window.canvas,global_pos)
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(global_pos)
