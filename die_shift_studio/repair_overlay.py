"""Draws the equipment-side straight-line repair patches and the DRC they add.

The patch is new copper on the fixed RDL layer, so it is painted as its own
layer-like object: amber for the added copper, hatched red for the violation
areas that only exist once the patch is present. The unpatched DRC markers stay
with the shared violation artist; when repair mode is on that artist shows the
*post-patch* markers instead so the two never contradict each other.
"""
from matplotlib.collections import PatchCollection

PATCH_FILL = '#d98a2b'
PATCH_EDGE = '#8a5a14'
ADDED_FILL = '#c0392b'
ADDED_EDGE = '#8e2b21'


class RepairOverlay:
    def __init__(self, window):
        self.w = window
        self.patches = None
        self.added = None
        self.stamp = None
        self.table_stamp = None

    def active(self):
        w = self.w
        return w.repair_toggle.isChecked() and getattr(w, 'repair_data', None) is not None

    def draw(self):
        w = self.w
        if self.patches is None or self.patches.axes is None:
            self.patches = PatchCollection([], facecolors=PATCH_FILL, edgecolors=PATCH_EDGE,
                                           linewidths=.8, alpha=.45, zorder=9.4)
            self.added = PatchCollection([], facecolors=ADDED_FILL, edgecolors=ADDED_EDGE,
                                         linewidths=.7, alpha=.5, hatch='///', zorder=9.6)
            for artist in (self.patches, self.added):
                w.ax.add_collection(artist)
            self.stamp = None
        stamp = (id(getattr(w, 'repair_data', None)),
                 tuple(k for k, b in w.visible_boxes.items() if b.isChecked()), w.view_bounds)
        if stamp != self.stamp:
            self.stamp = stamp
            data = w.repair_data if self.active() else None
            # Intermediate animation poses have no patch of their own; showing
            # the target pose's patch on them would misplace new copper.
            if getattr(w, '_in_animation', False):
                data = None
            faces = []
            for q in (data or {}).get('repairs', []):
                if not w.visible_boxes[q.layer].isChecked():
                    continue
                if not _in_view(q.patch.bounds, w.view_bounds):
                    continue
                faces.append(w.geometry_patch(q.patch))
            wounds = []
            for q in (data or {}).get('new_violations', []):
                if not _in_view(q['geometry'].bounds, w.view_bounds):
                    continue
                wounds.append(w.geometry_patch(q['geometry']))
            self.patches.set_paths(faces)
            self.patches.set_visible(bool(faces))
            self.added.set_paths(wounds)
            self.added.set_visible(bool(wounds))
        if not getattr(w, '_in_animation', False) and (id(getattr(w, 'repair_data', None)), stamp[1]) != self.table_stamp:
            self.table_stamp = (id(getattr(w, 'repair_data', None)), stamp[1])
            self.fill_table(w.repair_data if self.active() else None)

    def fill_table(self, data):
        w = self.w
        table = getattr(w, 'repair_table', None)
        summary = getattr(w, 'repair_summary', None)
        if table is None or summary is None:
            return
        if not w.repair_toggle.isChecked():
            table.setRowCount(0)
            self.fill_violations(None)
            summary.setText('勾选“修复补块”后，按当前姿态显示设备方在固定RDL层的直线补块，以及补块后的DRC。')
            return
        from .qt_compat import QtWidgets as W
        table.blockSignals(True)
        if data is None:
            table.setRowCount(0)
            table.blockSignals(False)
            self.fill_violations(None)
            summary.setText('正在按当前姿态生成补块与补块后DRC…')
            return
        rows = data['summary']['rows']
        table.setRowCount(len(rows))
        for i, q in enumerate(rows):
            values = [q['layer'], q['feature'], f"{q['via']} #{q['via_index']}",
                      f"DIE{q['owner']}", f"{q['offset_um']:.3f}", f"{q['width_um']:.3f}",
                      f"{q['patch_area_um2']:.1f}", q['new_violations']]
            for j, value in enumerate(values):
                item = W.QTableWidgetItem(str(value))
                item.setToolTip('补块为固定RDL层的直线延伸；“新增违规”为补块出现后才产生的DRC区域。'
                                '双击或选中该行可把视图跳到对应区域。')
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        self.fill_violations(data)
        detail = data['summary']
        if not detail['count']:
            summary.setText('当前姿态下没有过孔脱离固定RDL，无需直线补块。'
                            f"补块后DRC {detail['violations']} 处，均为原有问题（{_kinds(detail['kinds'])}）。 " + detail['scope'])
            return
        widths = detail['widths_um']
        shown = '、'.join(f'{v:.3f}' for v in widths[:4]) + ('…' if len(widths) > 4 else '')
        summary.setText(
            f"固定RDL层直线补块 {detail['count']} 块（层 {'、'.join(detail['layers'])}，"
            f"直径 {shown} μm，最大偏移 {detail['max_offset_um']:.3f} μm）。补块后DRC {detail['violations']} 处，"
            f"其中补块新增 {detail['added']} 处（{_kinds(detail['added_kinds'])}），"
            f"原有 {detail['preexisting']} 处（{_kinds(detail['kinds'])}）。 " + detail['scope'])

    def fill_violations(self, data):
        """Detail rows for the markers the patches created, each clickable."""
        w = self.w
        table = getattr(w, 'repair_violation_table', None)
        if table is None:
            return
        from .qt_compat import QtWidgets as W
        table.blockSignals(True)
        rows = (data or {}).get('new_violations', [])
        owners = (data or {}).get('summary', {}).get('owners', [])
        patches = (data or {}).get('summary', {}).get('rows', [])
        table.setRowCount(len(rows))
        for i, violation in enumerate(rows):
            bounds = violation['geometry'].bounds
            center = ((bounds[0] + bounds[2]) / 2., (bounds[1] + bounds[3]) / 2.)
            owner = owners[i] if i < len(owners) else None
            if owner is None or not 0 <= owner < len(patches):
                origin = '—'
            else:
                origin = f"{patches[owner]['layer']} #{patches[owner]['feature']}"
            values = [violation['kind'], f"{violation['area_um2']:.2f}", origin,
                      f"{center[0]:.1f}, {center[1]:.1f}"]
            for j, value in enumerate(values):
                item = W.QTableWidgetItem(str(value))
                item.setToolTip('补块出现后才产生的违规区域；双击或选中该行可把视图跳到该区域。')
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    # ------------------------------------------------------------ navigation
    def focus(self, bounds, minimum=100.):
        x0, y0, x1, y1 = bounds
        self.w.animate_view(((x0 + x1) / 2., (y0 + y1) / 2.),
                            max(minimum, (x1 - x0) * 1.7), max(minimum, (y1 - y0) * 1.7))

    def locate_patch(self, row, *_):
        """Zoom to one patch, together with the markers it created."""
        data = getattr(self.w, 'repair_data', None)
        if not self.active() or data is None or not 0 <= row < len(data['repairs']):
            return
        patch = data['repairs'][row]
        x0, y0, x1, y1 = patch.patch.bounds
        window = data['summary'].get('window_um') or 0.
        owners = data['summary'].get('owners', [])
        for i, violation in enumerate(data['new_violations']):
            if i < len(owners) and owners[i] != row:
                continue
            if i >= len(owners) and patch.patch.distance(violation['geometry']) > window:
                continue
            bounds = violation['geometry'].bounds
            x0, y0 = min(x0, bounds[0]), min(y0, bounds[1])
            x1, y1 = max(x1, bounds[2]), max(y1, bounds[3])
        self.focus((x0, y0, x1, y1))

    def locate_violation(self, row, *_):
        """Zoom to one marker the patch created."""
        data = getattr(self.w, 'repair_data', None)
        if not self.active() or data is None or not 0 <= row < len(data['new_violations']):
            return
        self.focus(data['new_violations'][row]['geometry'].bounds)


def _kinds(mapping):
    if not mapping:
        return '无'
    return '、'.join(f'{name} {count}' for name, count in sorted(mapping.items()))


def _in_view(bounds, view):
    return (bounds[0] <= view[2] and bounds[2] >= view[0]
            and bounds[1] <= view[3] and bounds[3] >= view[1])
