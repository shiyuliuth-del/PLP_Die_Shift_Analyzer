"""Non-modal canvas menu. The source selection owns the identity across previews."""
from collections import Counter
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
    if not selection.selected:
        tolerance=3*abs(window.ax.get_xlim()[1]-window.ax.get_xlim()[0])/max(1,window.ax.bbox.width)
        hits=selection.hits(Point(point).buffer(tolerance),point=True)
        if not hits:return
        selection.last_pixel=None
        selection.apply_hits(hits[:1],'replace')
    refs=tuple(selection.selected)
    menu=W.QMenu(window.canvas)
    counts=Counter();nodes=0
    for key,i in refs:
        kind,xy,_=shape_nodes(window.data,key,i)
        counts[(key,kind)]+=1;nodes+=len(xy)
    menu.addAction(f'已选 {len(refs)} 个图形 · {nodes} 个名义节点').setEnabled(False)
    for (layer,kind),n in sorted(counts.items())[:8]:
        menu.addAction(f'{layer}  {kind} × {n}').setEnabled(False)
    if len(counts)>8:menu.addAction(f'另有 {len(counts)-8} 类').setEnabled(False)
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
    ratio=window.canvas.devicePixelRatioF()
    pos=QtCore.QPoint(round(event.x/ratio),round(window.canvas.height()-event.y/ratio))
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(window.canvas.mapToGlobal(pos))
