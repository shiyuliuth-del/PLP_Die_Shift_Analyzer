"""CAD-style air-gap dimensions for the moving-via landing analysis.

Every risky landing is drawn as its own outline with a double-headed dimension
line from the landing edge to the fixed-RDL copper it is close to, and the
measured value in a boxed label — the same reading a layout engineer expects
from a dimension annotation, rather than a coloured rectangle.

Artists are pooled and updated in place: the animation calls this on every
intermediate pose, so creating and destroying annotations per frame would be the
dominant cost. Only the worst few sections are dimensioned on screen; the table
still lists every risk row, and the two never disagree because they come from
the same result.
"""
import numpy as np
from matplotlib.collections import PatchCollection
from matplotlib.patches import FancyArrowPatch
from shapely.geometry import Point
from shapely.strtree import STRtree
from .qt_compat import QtWidgets as W, QtGui
from .width_analysis import COLORS, RANK, TARGET_LABELS
from .viewport import overlaps

# A dense layout can hold thousands of risky landings; the plot stays readable
# and the artist pool stays bounded, while the table keeps every row.
MAX_DIMENSIONS = 200


class WidthOverlay:
    def __init__(self, window):
        self.w = window
        self.lands = None
        self.arrows = []
        self.labels = []
        self.stamp = None
        self.table_stamp = None
        self.rows = []
        self.tree = None
        window.canvas.mpl_connect('motion_notify_event', self.hover)

    def visible(self, row):
        w = self.w
        return all(w.visible_boxes.get(k) is not None and w.visible_boxes[k].isChecked()
                   for k in row['layers'])

    def _pool(self, count):
        w = self.w
        while len(self.arrows) < count:
            arrow = FancyArrowPatch((0, 0), (0, 1), arrowstyle='<->', mutation_scale=7,
                                    lw=.9, zorder=12.4)
            w.ax.add_patch(arrow)
            self.arrows.append(arrow)
        while len(self.labels) < count:
            label = w.ax.text(0, 0, '', fontsize=6.4, ha='center', va='bottom', zorder=12.6,
                              bbox=dict(fc='white', ec='none', alpha=.88, pad=1.1))
            self.labels.append(label)

    def draw(self):
        w = self.w
        frame = w.current_frame or {}
        data = frame.get('width_analysis') if w.width_toggle.isChecked() else None
        if self.lands is None or self.lands.axes is None:
            self.lands = PatchCollection([], facecolors='none', linewidths=1.1, zorder=12.0)
            w.ax.add_collection(self.lands)
            self.stamp = None
        stamp = (id(data), tuple(k for k, b in w.visible_boxes.items() if b.isChecked()), w.view_bounds)
        if stamp != self.stamp:
            self.stamp = stamp
            active = [] if data is None else [q for q in data['rows'] if q['draw'] and self.visible(q)]
            # Rows arrive sorted worst first, so the cap keeps the worst ones.
            shown = [q for q in active if overlaps(q['land'].bounds, w.view_bounds)][:MAX_DIMENSIONS]
            self.lands.set_paths([w.geometry_patch(q['land']) for q in shown])
            self.lands.set_edgecolors([COLORS[q['status']] for q in shown])
            self.lands.set_facecolors('none')
            self.lands.set_visible(bool(shown))
            self._pool(len(shown))
            for i, q in enumerate(shown):
                colour = COLORS[q['status']]
                arrow, label = self.arrows[i], self.labels[i]
                link = q.get('link')
                if link:
                    a, b = np.asarray(link[0], float), np.asarray(link[1], float)
                    arrow.set_positions(a, b)
                    label.set_position(((a[0] + b[0]) / 2., (a[1] + b[1]) / 2.))
                    label.set_text(f"{q['gap_um']:.2f}")
                else:
                    arrow.set_positions(np.asarray(q['point'], float), np.asarray(q['point'], float))
                    label.set_position(tuple(q['point']))
                    label.set_text('R内无')
                arrow.set_color(colour)
                arrow.set_visible(True)
                label.set_color(colour)
                label.set_visible(True)
                box = label.get_bbox_patch()
                if box is not None:
                    box.set_edgecolor(colour)
            for i in range(len(shown), len(self.arrows)):
                self.arrows[i].set_visible(False)
                self.labels[i].set_visible(False)
            self.rows = shown
            self.tree = STRtree([q['land'] for q in shown]) if shown else None
            self.active_rows = active
        if not getattr(w, '_in_animation', False) and (id(data), stamp[1]) != self.table_stamp:
            self.table_stamp = (id(data), stamp[1])
            self.fill_table(data)

    def fill_table(self, data):
        w = self.w
        rows = getattr(self, 'active_rows', []) if data else []
        w.width_rows = rows
        table = w.width_table
        target_label = (data or {}).get('target_label', TARGET_LABELS['vertical'])
        table.setHorizontalHeaderLabels(['风险', '运动过孔', '所属', '落盘半径 / μm', 'air-gap / μm',
                                         '有效线距 / μm', target_label, '位置 / μm'])
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for i, q in enumerate(rows):
            gap = '—' if q['gap_um'] is None else f"{q['gap_um']:.3f}"
            if q['target'] is None:
                nearest = '—'
            else:
                nearest = f"{q['target']['layer']} #{q['target']['index'] + 1}（net {q['target']['net']}）"
            values = [q['status'], f"{q['key']} #{q['via_index']}",
                      '—' if q['owner'] < 0 else f"DIE{q['owner'] + 1}",
                      f"{q['land_radius_um']:.3f}", gap, f"{q['rule_space_um']:.3f}", nearest,
                      f"{q['point'][0]:.1f}, {q['point'][1]:.1f}"]
            for j, value in enumerate(values):
                item = W.QTableWidgetItem(str(value))
                colour = QtGui.QColor(COLORS[q['status']])
                colour.setAlpha(60)
                item.setBackground(colour)
                item.setToolTip(self.description(q))
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        parameters = (data or {}).get('parameters', {})
        counts = (data or {}).get('counts', {})
        suffix = '；'.join(f'{k}{v}' for k, v in counts.items() if v)
        if not data:
            w.width_summary.setText('勾选“宽度分析”后，按当前姿态测量每个运动过孔落盘区到垂直相邻固定RDL层'
                                    '其他网络铜的 air-gap；使用当前线宽、线距与容差。')
            return
        hint = ''
        pending = [q for q in data['rows'] if q['status'] == '待确认']
        if pending:
            hint = f'灰色{pending[0]["status"]}：'
            hint += ('相关运动图形归属未解析，请在“图层随动设置”核对用途与层序。'
                     if '归属未解析' in pending[0]['detail'] else '需人工复核。')
        shown = min(len(rows), MAX_DIMENSIONS)
        reconstruction = (w.current_frame or {}).get('reconstruction')
        if w.reconstruction_toggle.isChecked():
            reconstruction_note = ('本次测量使用“重构预演”自动划分并移动后的固定RDL铜（移动区＋裂隙补线），'
                                   '即按重构后的图形判定；重构为软件猜测，不代表产线做法。')
            if reconstruction is not None and not reconstruction['summary']['pieces']:
                reconstruction_note = '已勾选“重构预演”，但当前姿态没有移动区，测量等同未重构的原图。'
        else:
            reconstruction_note = ''
        w.width_summary.setText(
            f"宽度分析对象：{data.get('mode_label', '')}。"
            f"共检查 {data['examined']} 个运动过孔（全部 {data['total_vias']} 个）："
            f"落盘区＝过孔外扩 overlap {parameters.get('overlap_um', 0):g} μm，"
            f"局部查找半径 R {parameters.get('radius_um', 0):g} μm，"
            f"标注阈值 {parameters.get('threshold_um', 0):g} μm，有效线距 {parameters.get('rule_space_um', 0):g} μm。"
            f"风险 {len(rows)} 处：{suffix or '无'}；{data['beyond_radius']} 个过孔在 R 内没有可比对象。"
            f"图上最多标注 {shown} 处，表格列出全部风险行，点击可定位。{hint}{reconstruction_note}"
            '这是局部落盘间距的必要条件，R 之外未参与；不代表整图可布通或完整DRC通过。')

    @staticmethod
    def description(q):
        owner = '—' if q['owner'] < 0 else f"DIE{q['owner'] + 1}"
        text = (f"{q['status']} · 运动过孔 {q['key']} #{q['via_index']} · {owner}\n"
                f"落盘区等效半径 {q['land_radius_um']:.3f} μm")
        if q['gap_um'] is None:
            text += '\n半径内没有可比对象'
        else:
            text += (f"\nair-gap {q['gap_um']:.3f} μm（有效线距 {q['rule_space_um']:g} μm，"
                     f"标注阈值 {q['threshold_um']:g} μm）")
            if q['target'] is not None:
                kind = '固定RDL' if q['target'].get('kind') == 'fixed' else '过孔原始落盘'
                text += f"\n最近异网对象：{q['target']['layer']} #{q['target']['index'] + 1}（{kind}，net {q['target']['net']}）"
        return text + '\n' + q['detail']

    def hover(self, event):
        w = self.w
        if self.tree is None or event.inaxes is not w.ax or event.xdata is None:
            if w.canvas.toolTip():
                w.canvas.setToolTip('')
            return
        if getattr(getattr(w, 'mouse_die', None), 'drag', None) is not None:
            return
        hits = self.tree.query(Point(event.xdata, event.ydata), predicate='intersects')
        text = self.description(self.rows[int(hits[0])]) if len(hits) else ''
        if w.canvas.toolTip() != text:
            w.canvas.setToolTip(text)

    def locate(self, row):
        rows = getattr(self.w, 'width_rows', [])
        if not 0 <= row < len(rows):
            return
        q = rows[row]
        x0, y0, x1, y1 = q['land'].bounds
        span = max(80., (x1 - x0) * 2.5, (y1 - y0) * 2.5)
        self.w.animate_view(q['point'], span, span)
