"""Undo source-bound editor actions without rolling back process parameters."""
from collections import defaultdict
from dataclasses import replace

import numpy as np

from .area_filter import remap_project


EDIT_FIELDS=tuple('reconstruction_'+name for name in (
    'regions','boxes','angle_boxes','box_excluded','fixed_via_follows','cuts','extra_cuts',
    'second_cuts','dropped_cuts','ignored','following','forced','angles','deleted','added','intervals'))


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
        return dict(edits=edits,width=tuple(sorted(width)),
                    nodes=tuple(getattr(base,'node_edits',())),
                    deletions=tuple(getattr(data,'deleted_shapes',())),
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
        def committed():
            source.pop();target.append(before);self.trim()
        if (state['nodes'],state['deletions'])!=(before['nodes'],before['deletions']):
            from .area_filter import canonical_project
            project=canonical_project(w.data,w.project_settings())
            project.update(node_edits=list(state['nodes']),deleted_shapes=list(state['deletions']),rulers=state['rulers'])
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
        if state['edits']!=before['edits']:
            w.invalidate_reconstruction_edit()
        if state['width']!=before['width']:w.width_yield.refresh()
        w.plot_layout(preserve=True)
        if any(state[key]!=before[key] for key in ('edits','width','pose','extreme')):
            w.request_preview(quiet=True,pose_change=state['pose']!=before['pose'] or state['extreme']!=before['extreme'])
        committed()
