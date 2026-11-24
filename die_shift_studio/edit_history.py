"""Undo source-bound editor actions without rolling back process parameters."""
from collections import defaultdict
from dataclasses import replace

import numpy as np

from .area_filter import remap_project


DRC_FIELDS=('drc_waived_refs','drc_retained_refs')
RECONSTRUCTION_FIELDS=tuple('reconstruction_'+name for name in (
    'regions','boxes','angle_boxes','box_order','box_starts','box_excluded','fixed_via_follows','between_overrides','cuts','extra_cuts',
    'second_cuts','dropped_cuts','ignored','following','forced','angles','deleted','added','intervals'))
EDIT_FIELDS=RECONSTRUCTION_FIELDS+DRC_FIELDS


def mapped_edits(data,settings,width,*,to_source):
    edits={name:getattr(settings,name) for name in EDIT_FIELDS} if not isinstance(settings,dict) else settings
    area=getattr(data,'area_indices',None)
    if area is None:return edits,tuple(width)
    if not hasattr(data,'_edit_index_maps'):
        forward={(layer,index):original for layer,indices in area.items() for index,original in enumerate(indices)}
        reverse={(layer,original):index for (layer,index),original in forward.items()}
        data._edit_index_maps=(forward,reverse)
    lookup=data._edit_index_maps[0 if to_source else 1]
    mapped=remap_project({'settings':edits,'ignored_width_lines':{'refs':tuple(width)}},lookup)
    return {name:tuple(rows) for name,rows in mapped['settings'].items()},tuple(mapped['ignored_width_lines']['refs'])


def record(window):
    history=getattr(window,'edit_history',None)
    if history is not None:history.record()


class EditHistory:
    def __init__(self,window):
        self.w=window
        self.projects=defaultdict(lambda:([],[]))
        self.queued=[]

    @property
    def stacks(self):
        return self.projects[self.w.cache_project_key]

    def capture(self):
        w=self.w;data=w.data
        base=getattr(data,'_area_base',data)
        edits,width=mapped_edits(data,w.view_settings,w.ignored_width_lines,to_source=True)
        staged=getattr(w,'_staged_restored_edits',None)
        if staged:
            staged_edits,staged_width=staged
            from .manual_regions import BOX_HISTORY_FIELDS
            edits={name:(edits[name] if name in BOX_HISTORY_FIELDS else tuple(dict.fromkeys(tuple(edits[name])+tuple(staged_edits.get(name,()))))) for name in EDIT_FIELDS}
            width=tuple(sorted(set(width)|set(staged_width)))
        deletions=w._effective_deleted_shapes() if hasattr(w,'_effective_deleted_shapes') else tuple(getattr(data,'deleted_shapes',()))
        return dict(edits=edits,width=tuple(sorted(width)),drc=getattr(w,'ignored_drc',{}),
                    nodes=w._effective_node_edits() if hasattr(w,'_effective_node_edits') else tuple(getattr(base,'node_edits',())),
                    deletions=deletions,
                    rulers=tuple(tuple(map(tuple,row)) for row in w.rulers.rows),
                    pose=None if w.manual_states is None else tuple(map(tuple,w.manual_states)),
                    extreme=w.active_preview)

    def record(self,state=None):
        undo,redo=self.stacks
        limit=self.w.view_settings.max_undo_steps
        if not limit:return
        undo.append(self.capture() if state is None else state)
        redo.clear()
        self.trim()

    def trim(self):
        limit=self.w.view_settings.max_undo_steps
        for undo,redo in self.projects.values():
            del undo[:max(0,len(undo)-limit)];del redo[:max(0,len(redo)-limit)]
        self.refresh()

    def clear(self,all_projects=False):
        if all_projects:self.projects.clear()
        else:self.projects.pop(self.w.cache_project_key,None)
        self.queued=[row for row in self.queued if not all_projects and row[0]!=self.w.cache_project_key]
        self.refresh()

    def rekey(self,old,new):
        if old in self.projects:self.projects[new]=self.projects.pop(old)

    def refresh(self):
        undo,redo=self.projects.get(self.w.cache_project_key,([],[]))
        self.w.undo_button.setEnabled(bool(undo) and not self.w.remote_locked)
        self.w.redo_button.setEnabled(bool(redo) and not self.w.remote_locked)

    def drain(self):
        if self.w.worker is not None or not self.queued:return
        key,forward=self.queued.pop(0)
        if key==self.w.cache_project_key:self.step(forward)
        if self.w.worker is None and self.queued:
            from .qt_compat import QtCore
            QtCore.QTimer.singleShot(0,self.drain)

    def step(self,forward=False):
        w=self.w
        if w.remote_locked:return
        undo,redo=self.stacks
        source,target=(redo,undo) if forward else (undo,redo)
        if w.worker is not None:
            if len(self.queued)<w.view_settings.max_undo_steps:self.queued.append((w.cache_project_key,forward))
            return
        if not source:return
        w.cut_drag.cancel(redraw=False);w.mouse_die.cancel_pending()
        before=self.capture()
        state=source[-1]
        recon_changed=any(before['edits'][name]!=state['edits'][name] for name in RECONSTRUCTION_FIELDS)
        waiver_changed=before['drc']!=state['drc'] or any(before['edits'][name]!=state['edits'][name] for name in DRC_FIELDS)
        def committed():
            source.pop();target.append(before);self.trim()
        if (state['nodes'],state['deletions'])!=(before['nodes'],before['deletions']):
            if hasattr(w,'_can_defer_deletion_edit') and w._can_defer_deletion_edit():
                before_map={tuple(q[:2]):q[2] for q in before['deletions']}
                target_map={tuple(q[:2]):q[2] for q in state['deletions']}
                restored=tuple(sorted(set(before_map)-set(target_map)))
                deleted=tuple((ref,target_map[ref]) for ref in sorted(set(target_map)-set(before_map)))
                if restored:w.pending_layout_ops.append(('restore',restored))
                if deleted:w.pending_layout_ops.append(('delete',deleted))
                w._sync_staged_deletion_markers(state['deletions'])
                if state['nodes']!=before['nodes']:w._stage_history_nodes(state['nodes'])
                actual_refs={(layer,raw) for layer,indices in getattr(w.data,'area_indices',{}).items() for raw in indices}
                if actual_refs:
                    from .manual_regions import BOX_HISTORY_FIELDS
                    missing_edits={name:tuple(row for row in state['edits'].get(name,()) if len(row)>=2 and (row[0],int(row[1])) not in actual_refs)
                                   for name in EDIT_FIELDS if name not in (*BOX_HISTORY_FIELDS,'reconstruction_regions')}
                    missing_width=tuple(ref for ref in state['width'] if tuple(ref) not in actual_refs)
                    w._staged_restored_edits=(missing_edits,missing_width) if (any(missing_edits.values()) or missing_width) else None
                else:
                    w._staged_restored_edits=None
                mapping,width=mapped_edits(w.data,state['edits'],state['width'],to_source=False)
                w.view_settings=replace(w.view_settings,**mapping)
                w.ignored_width_lines=frozenset(width)
                w.rulers.restore(state['rulers'])
                w.manual_states=None if state['pose'] is None else np.array(state['pose'],float)
                w.active_preview=state['extreme']
                row=next((row for row,(index,_) in enumerate(w.page_items,1) if index==w.active_preview),-1)
                if w.active_preview is None and w.manual_states is None:row=0
                blocked=w.extreme_table.blockSignals(True)
                w.extreme_table.setCurrentCell(row,0);w.extreme_table.blockSignals(blocked)
                w._selected_grid_pose=w.extremes[w.active_preview]['states'] if w.extremes is not None and w.active_preview is not None else None
                if state['width']!=before['width']:w.width_yield.refresh()
                has_pending=bool(w.pending_deleted_shapes or w.pending_restored_shapes or w.pending_node_shapes or (w._applied_preview_settings is not None and w.view_settings.reconstruction_key()!=w._applied_preview_settings.reconstruction_key()))
                if has_pending:
                    w.defer_reconstruction()
                else:
                    w._clear_pending_layout_markers()
                    w.reconstruction_pending=False
                    if hasattr(w,'reconstruction_overlay'):w.reconstruction_overlay.staged_overlay=None
                    w.plot_layout(preserve=True)
                action='撤销删除' if restored else '删除图形' if deleted else '节点修改'
                if has_pending:
                    w.statusBar().showMessage(f'已暂存：{action}（保持特殊标记，点击“重构计算”按顺序应用）')
                else:
                    w.statusBar().showMessage(f'已撤销未计算的{action}，无需重新计算')
                if waiver_changed:w.replace_drc_waivers(state['drc'],refresh_evidence=True)
                committed()
                return
            from .area_filter import canonical_project
            project=canonical_project(w.data,w.project_settings())
            project.update(node_edits=list(state['nodes']),deleted_shapes=list(state['deletions']),rulers=state['rulers'],ignored_drc=state['drc'])
            project['settings'].update(state['edits'])
            project['ignored_width_lines']['refs']=list(state['width'])
            project['ignored_reconstruction']['refs']=list(state['edits']['reconstruction_ignored'])
            w.apply_layout_edit(project,commit=committed,pose=state['pose'],extreme=state['extreme'])
            return
        mapping,width=mapped_edits(w.data,state['edits'],state['width'],to_source=False)
        w.view_settings=replace(w.view_settings,**mapping)
        w.ignored_width_lines=frozenset(width)
        w.rulers.restore(state['rulers'])
        w.manual_states=None if state['pose'] is None else np.array(state['pose'],float)
        w.active_preview=state['extreme']
        row=next((row for row,(index,_) in enumerate(w.page_items,1) if index==w.active_preview),-1)
        if w.active_preview is None and w.manual_states is None:row=0
        blocked=w.extreme_table.blockSignals(True)
        w.extreme_table.setCurrentCell(row,0);w.extreme_table.blockSignals(blocked)
        w._selected_grid_pose=w.extremes[w.active_preview]['states'] if w.extremes is not None and w.active_preview is not None else None
        if recon_changed:
            applied=w._applied_preview_settings
            if (w._can_defer_deletion_edit() and applied is not None and not (w.pending_layout_ops or w.pending_node_shapes)
                    and w.view_settings.reconstruction_key()==applied.reconstruction_key()):
                w.reconstruction_pending=False;w.reconstruction_overlay.staged_overlay=None
                w.reconstruction_overlay.draft_frame=w.reconstruction_overlay.draft_data=w.reconstruction_overlay.draft_key=None
                w.reconstruction_yield.refresh()
            else:w.invalidate_reconstruction_edit()
        if state['width']!=before['width']:w.width_yield.refresh()
        if waiver_changed:w.replace_drc_waivers(state['drc'],refresh_evidence=True)
        w.plot_layout(preserve=True)
        if recon_changed or any(state[key]!=before[key] for key in ('width','pose','extreme')):
            w.request_preview(quiet=True,pose_change=state['pose']!=before['pose'] or state['extreme']!=before['extreme'])
        committed()
