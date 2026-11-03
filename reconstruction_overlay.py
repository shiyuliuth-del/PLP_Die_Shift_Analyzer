"""Draws the RDL reconstruction preview: movable part, its move, and the cover.

The reconstruction is a *guess* at how an operator would split the fixed RDL, so
the picture separates the three objects it invents: the movable part in its
original position (blue, below), the same copper after the die's rigid move
(amber, on top), the cover patch that spans the fissure (green) — the polygon
built from the four corners of the two cut faces, the movable one and the
stationary one — and the cut line the split used (dashed, at a right angle to the
route). One cut is drawn per chain of landings: landings of one net that sit close
together on the same fixed-RDL feature share one movable segment, and the cut is
measured from the farthest landing of that chain. A fixed-RDL polygon that spans
several chips is clipped at the chip frame, so one piece always belongs to one
die. Nothing is hidden: the table lists every piece and says when a piece had no
immovable copper left to hold on to, so no seam is left to cover.
"""
import numpy as np
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import FancyArrowPatch

# 冷色＝原来的位置（移动区原位、切口线），暖色＝重构后的铜与补线，这样
# “动过的”和“没动的”一眼分得开，不会和固定RDL本身的浅蓝混在一起。
FIXED_FILL = '#eba96a'
FIXED_EDGE = '#bf7a35'
MOVABLE_FILL = '#9fb3c8'
MOVABLE_EDGE = '#5d7894'
MOVED_FILL = '#e07a2f'
MOVED_EDGE = '#8f4712'
BRIDGE_FILL = '#f5b955'
BRIDGE_EDGE = '#a86f16'
CUT_COLOR = '#8a4b3a'
# Spacing check of the reconstructed copper against other nets: same colours as
# the air-gap analysis so a violation reads the same way in both pictures.
SPACE_COLORS = {'不足': '#d55e70', '临界': '#ddb15c', '充足': '#70af9c'}
MAX_DIMENSIONS = 200


class ReconstructionOverlay:
    def __init__(self, window):
        self.w = window
        self.movable = None
        self.moved = None
        self.bridges = None
        self.cuts = None
        self.manual_cuts = None
        self.drag_line = None
        self.stamp = None
        self.table_stamp = None
        self.arrow_artists = []
        self.active_rows = []
        self.hover = None
        self.hover_key = None
        self._pick_cache = (None, [])

    def active(self):
        return self.w.reconstruction_toggle.isChecked()

    def highlight(self, picked):
        """Bright outline under the pointer, so the cut that can be dragged is obvious."""
        w = self.w
        if self.hover is None:
            from matplotlib.lines import Line2D
            self.hover = Line2D([], [], color='#1f9ad6', lw=4.4, alpha=.9,
                                solid_capstyle='round', zorder=13.1)
            w.ax.add_line(self.hover)
        if picked is None:
            self.hover_key = None
            self.hover.set_data([], [])
            self.hover.set_visible(False)
            return False
        piece, points = picked
        key = (piece['layer'], int(piece['index']),
               tuple(sorted(int(v) for v in piece['vias'])))
        changed = key != self.hover_key
        self.hover_key = key
        normals = np.linalg.norm(points[1:] - points[:-1], axis=1)
        index = int(np.argmax(normals))
        start, end = points[index], points[index + 1]
        self.hover.set_data([start[0], end[0]], [start[1], end[1]])
        self.hover.set_visible(True)
        return changed

    def clear_hover(self):
        self.highlight(None)

    def data(self):
        frame = getattr(self.w, 'current_frame', None)
        return (frame or {}).get('reconstruction')

    def draw(self):
        w = self.w
        if self.movable is None or self.movable.axes is None:
            self.movable = PatchCollection([], facecolors=MOVABLE_FILL, edgecolors=MOVABLE_EDGE,
                                           linewidths=.8, linestyles=(0, (4, 2)), alpha=.20, zorder=9.3)
            self.fixed = PatchCollection([], facecolors=FIXED_FILL, edgecolors=FIXED_EDGE,
                                         linewidths=.6, alpha=.55, zorder=9.4)
            self.moved = PatchCollection([], facecolors=MOVED_FILL, edgecolors=MOVED_EDGE,
                                         linewidths=1.1, alpha=.78, zorder=9.5)
            self.bridges = PatchCollection([], facecolors=BRIDGE_FILL, edgecolors=BRIDGE_EDGE,
                                           linewidths=1.0, alpha=.80, zorder=9.7)
            self.cuts = LineCollection([], colors=CUT_COLOR, linewidths=1.2, linestyles=(0, (6, 3)),
                                       alpha=.90, zorder=9.8)
            # A cut the user dragged is drawn solid and thicker, so it is clear
            # which seams follow the software's search and which are pinned.
            self.manual_cuts = LineCollection([], colors='#b3432f', linewidths=2.6,
                                              alpha=.95, zorder=9.9)
            for artist in (self.movable, self.fixed, self.moved, self.bridges, self.cuts,
                           self.manual_cuts):
                w.ax.add_collection(artist)
            self.stamp = None
        data = self.data() if self.active() else None
        stamp = (id(data), tuple(k for k, b in w.visible_boxes.items() if b.isChecked()), w.view_bounds)
        if stamp != self.stamp:
            self.stamp = stamp
            origin, target, bridge, cut, still, manual = [], [], [], [], [], []
            seen_still=set()
            for q in (data or {}).get('pieces', []):
                if not w.visible_boxes[q['layer']].isChecked():
                    continue
                bounds=[q['movable'].bounds,q['moved'].bounds]
                if q.get('fixed') is not None and not q['fixed'].is_empty:
                    bounds.append(q['fixed'].bounds)
                if not any(_in_view(b, w.view_bounds) for b in bounds):
                    continue
                origin.append(w.geometry_patch(q['movable']))
                target.append(w.geometry_patch(q['moved']))
                key=(q['layer'],q['index'])
                if key not in seen_still and q.get('fixed') is not None and not q['fixed'].is_empty:
                    # One feature can carry several dies; its stationary copper is
                    # the same geometry, so draw it once instead of stacking
                    # semi-transparent patches on top of each other.
                    seen_still.add(key);still.append(w.geometry_patch(q['fixed']))
                if q['bridge'] is not None and not q['bridge'].is_empty:
                    bridge.append(w.geometry_patch(q['bridge']))
                for line in _lines(q['cut']):
                    (manual if q.get('manual') else cut).append(line)
            self.movable.set_paths(origin)
            self.movable.set_visible(bool(origin))
            self.fixed.set_paths(still)
            self.fixed.set_visible(bool(still))
            self.moved.set_paths(target)
            self.moved.set_visible(bool(target))
            self.bridges.set_paths(bridge)
            self.bridges.set_visible(bool(bridge))
            self.cuts.set_segments(cut)
            self.cuts.set_visible(bool(cut))
            self.manual_cuts.set_segments(manual)
            self.manual_cuts.set_visible(bool(manual))
            # Spacing dimensions: a CAD-style arrow across the smallest distance
            # between the reconstructed copper and the nearest other net, with
            # the measured value in a box. Only the pieces that break the spacing
            # rule (or come within twice it) are annotated, biggest font first so
            # the numbers stay readable when the view is zoomed out.
            for artist in self.arrow_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
            self.arrow_artists = []
            self.active_rows = []
            shown = 0
            for q in (data or {}).get('pieces', []):
                if not w.visible_boxes[q['layer']].isChecked():
                    continue
                status = _space_status(q.get('clearance_um'), data['summary'].get('rule_space_um', 0.))
                if status == '充足':
                    continue
                link = q.get('conflict_link')
                if not link or shown >= MAX_DIMENSIONS:
                    continue
                shown += 1
                self.active_rows.append(q)
                colour = SPACE_COLORS[status]
                (x0, y0), (x1, y1) = link
                arrow = FancyArrowPatch((x0, y0), (x1, y1), arrowstyle='<->',
                                        mutation_scale=9, lw=1.1, color=colour, zorder=12.4)
                w.ax.add_patch(arrow)
                label = w.ax.text((x0 + x1) / 2., (y0 + y1) / 2.,
                                  f"{q['clearance_um']:.2f} μm", fontsize=8.6,
                                  ha='center', va='bottom', color=colour, zorder=12.6,
                                  bbox=dict(fc='white', ec='none', alpha=.92, pad=1.2))
                self.arrow_artists.extend([arrow, label])
        if not getattr(w, '_in_animation', False) and (id(data), stamp[1]) != self.table_stamp:
            self.table_stamp = (id(data), stamp[1])
            self.fill_table(data)

    def pieces(self):
        """Pieces that are currently visible (layer box checked)."""
        data = self.data() if self.active() else None
        if not data:
            return []
        return [q for q in data.get('pieces', [])
                if self.w.visible_boxes[q['layer']].isChecked()]

    def pick(self, x, y, tolerance):
        """Nearest piece whose cut line passes within ``tolerance`` of (x, y)."""
        data = self.data() if self.active() else None
        stamp = id(data)
        if self._pick_cache[0] != stamp:
            lines = []
            for q in self.pieces():
                for line in _lines(q['cut']):
                    points = np.asarray(line, float).reshape(-1, 2)
                    if len(points) >= 2:
                        lines.append((q, points))
            self._pick_cache = (stamp, lines)
        best = None
        for q, points in self._pick_cache[1]:
            distance = _segment_distance(points, x, y)
            if best is None or distance < best[0]:
                best = (distance, q, points)
        if best is None or best[0] > tolerance:
            return None
        return best[1], best[2]

    def fill_table(self, data):
        w = self.w
        table = getattr(w, 'reconstruction_table', None)
        summary = getattr(w, 'reconstruction_summary', None)
        if table is None or summary is None:
            return
        from .qt_compat import QtWidgets as W
        if not self.active():
            table.setRowCount(0)
            summary.setText('勾选“重构预演”后，软件按运动过孔自动划分固定RDL的移动区：'
                            '同一网络、彼此相距不远的多个落盘算作一条串，整条串只切一刀，切口量到串里最远的'
                            '那个落盘再往外；同一块固定RDL跨多个芯片时，移动区只取本 DIE 芯片边框以内的铜；'
                            '移动区按当前姿态移动后，用两个切口面的 4 个端点围成的多边形'
                            '补上裂隙，再用重构后的铜做落盘 air-gap。')
            return
        table.blockSignals(True)
        if data is None:
            table.setRowCount(0)
            table.blockSignals(False)
            summary.setText('正在按当前姿态重构固定RDL…')
            return
        rows = data['pieces']
        table.setRowCount(len(rows))
        for i, q in enumerate(rows):
            gap = '—' if q['gap_um'] is None else f"{q['gap_um']:.3f}"
            clearance = '—' if q.get('clearance_um') is None else f"{q['clearance_um']:.2f}"
            space = _space_status(q.get('clearance_um'), data['summary'].get('rule_space_um', 0.))
            bridge = '—' if q['bridge_area_um2'] is None else f"{q['bridge_area_um2']:.1f}"
            length = '—' if q['bridge_length_um'] is None else f"{q['bridge_length_um']:.3f}"
            flush = q['gap_um'] is not None and q['gap_um'] <= 1e-6
            if q['orphan']:
                state = '整块随动（无不动铜可接）'
            elif flush and q['bridge_area_um2'] is not None:
                # The two pieces meet at the seam. No cut position can pull them
                # apart (the copper keeps going in the shift direction), so the
                # row says 贴合 and the cover fills the overlap instead.
                state = '贴合（由补线覆盖）'
            elif flush:
                state = '贴合（直接相接）'
            elif q['bridge_area_um2'] is None:
                state = '无需补线（无切口面）'
            else:
                state = '已补线'
            if space != '充足':
                state = f"{state}｜间距{space}"
            if q.get('manual'):
                state = f"{state}｜手动切口"
                if q.get('cut_clamped'):
                    state = f"{state}｜已到极限"
            values = [f"{q['layer']} #{q['index'] + 1}", f"DIE{q['die'] + 1}", f"net {q['net']}",
                      f"{q['movable_area_um2']:.1f}", f"{q['shift_um']:.3f}", gap, bridge, length,
                      clearance, state]
            for j, value in enumerate(values):
                item = W.QTableWidgetItem(str(value))
                item.setToolTip('移动区＝这条串的过孔下方（再往外量切口距离 D）的固定RDL铜，'
                                '按该 DIE 的刚体变换移动；同一网络彼此相距不远的落盘算一条串，只切一刀，'
                                '切口线垂直于这条串的走线、与走线两侧各成 90°，量到串里最远的落盘再往外；'
                                '同一块固定RDL跨多个芯片时，只有本 DIE 芯片边框（128/0）以内的铜跟着走，'
                                '边框外的铜归相邻 DIE、留在原位，边框边也算一条切口；'
                                '裂隙＝移动后切口线到不动铜块切口线的最近距离；'
                                '补线＝两个切口面的 4 个端点围成的多边形，不论裂隙大小都会补上，'
                                '只有两面完全重合或缝隙已被铜盖住时才不重复补；'
                                '切口缝长＝最长切口面的长度；'
                '间距＝重构后的铜（移动后的铜＋补线）到同层其他网络最近铜的距离，'
                '低于有效线距为“不足”、低于两倍为“临界”，图上用尺寸箭头标出。'
                '选中该行可把视图跳到该区域。')
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        detail = data['summary']
        if not detail['pieces']:
            summary.setText('当前姿态下没有移动过孔（或过孔下方没有固定RDL铜），无需重构。 ' + data['scope'])
            return
        parameters = data['parameters']
        cut_rule = (f"切口垂直于该串的走线（与走线两侧各成 90°），固定在串里最远的落盘之外 {parameters['length_um']:.3f} μm 处"
                    if parameters['length_um'] > 0. else
                    "切口垂直于该串的走线（与走线两侧各成 90°），位置由软件沿走线自动搜索："
                    "从落盘边缘向外，止于同网络汇合节点或下一个同网络落盘，"
                    "在“不与其它网络短路”的前提下取横向最窄、附近最直处")
        parts=[
            f"按运动过孔自动划分移动区 {detail['pieces']} 段（同一网络彼此相距不远的落盘合为一条串，整条串只切一刀），"
            f"移动面积合计 {detail['movable_area_um2']:.1f} μm²，"
            f"最大位移 {detail['max_shift_um']:.3f} μm；补线 {detail['bridges']} 处"
            f"（由两面切口的 4 个端点围成多边形，补线面积合计 {detail.get('cover_area_um2', 0.):.1f} μm²，"
            f"外扩 {detail['width_um']:.3f} μm），"
            f"整块随动、无不动铜可接 {detail['orphans']} 块；"
            f"重构后与其它网络（暖色走线）的间距检查：有效线距 {detail.get('rule_space_um', 0.):.2f} μm，",
        ]
        if detail.get('conflicts'):
            parts.append(f"重构后间距不足 {detail.get('conflicts', 0)} 段"
                         "（已用尺寸箭头标出，是当前姿态下任何切口位置都避免不了的那几段）；")
        else:
            parts.append("各段都满足有效线距；")
        parts.append(f"移动后与留下的铜贴合（裂隙为 0）{detail.get('flush', 0)} 段")
        if detail.get('manual_cuts'):
            parts.append(f"；其中 {detail.get('manual_cuts', 0)} 条串的切口是手动拖动固定的"
                         "（拖动切口线可改写，右键/双击切口或“清除全部手动切口”可恢复自动搜索）")
        parts.append("——铜沿运动方向继续延伸时，任何切线位置都拉不开这两块，"
                     "由补线多边形把两者相接的地方盖住。")
        parts.append(f"图上暖色＝固定区里不动的铜（{detail.get('fixed_area_um2', 0.):.1f} μm²）＋移动后的铜＋补线，"
                     "合起来是同一条完整走线，一眼能看出这条网络的哪一段动了、哪一段没动；冷色只是移动区的原位。")
        parts.append("同一块固定RDL若跨多个芯片，移动区只取本 DIE 芯片边框（128/0）以内的铜，"
                     "边框外的铜留在原位归相邻 DIE，边框线也算一条切口并按 4 端点补线。")
        parts.append(f"{cut_rule}；落盘外扩 overlap={parameters['overlap_um']:.3f} μm。 ")
        parts.append(data['scope'])
        summary.setText(''.join(parts))

    # ------------------------------------------------------------ navigation
    def locate(self, row, *_):
        """Zoom to one reconstructed piece, cut line and bridge together."""
        data = self.data() if self.active() else None
        if data is None or not 0 <= row < len(data['pieces']):
            return
        q = data['pieces'][row]
        x0, y0, x1, y1 = q['movable'].bounds
        for geometry in (q['moved'], q['cut'], q['bridge']):
            if geometry is None or geometry.is_empty:
                continue
            bounds = geometry.bounds
            x0, y0 = min(x0, bounds[0]), min(y0, bounds[1])
            x1, y1 = max(x1, bounds[2]), max(y1, bounds[3])
        self.w.animate_view(((x0 + x1) / 2., (y0 + y1) / 2.),
                           max(100., (x1 - x0) * 1.7), max(100., (y1 - y0) * 1.7))


def _space_status(clearance, rule_space):
    """'不足' below the spacing rule, '临界' below twice it, else '充足'."""
    if clearance is None:
        return '充足'
    if rule_space and clearance < rule_space - 1e-6:
        return '不足'
    if rule_space and clearance < 2. * rule_space - 1e-6:
        return '临界'
    return '充足'


def _lines(geometry):
    """The cut as drawable segments, whatever the line-merge left behind."""
    if geometry is None or geometry.is_empty:
        return []
    kind = geometry.geom_type
    if kind == 'LineString':
        return [list(geometry.coords)]
    if kind == 'MultiLineString' or kind == 'GeometryCollection':
        segments = []
        for part in geometry.geoms:
            segments.extend(_lines(part))
        return segments
    return []


def _in_view(bounds, view):
    return (bounds[0] <= view[2] and bounds[2] >= view[0]
            and bounds[1] <= view[3] and bounds[3] >= view[1])


def _segment_distance(points, x, y):
    """Shortest distance from (x, y) to the polyline ``points``."""
    start = points[:-1]
    end = points[1:]
    delta = end - start
    length = np.einsum('ij,ij->i', delta, delta)
    target = np.array([x, y], float) - start
    ratio = np.where(length > 0., np.einsum('ij,ij->i', target, delta) / np.maximum(length, 1e-18), 0.)
    ratio = np.clip(ratio, 0., 1.)
    nearest = start + delta * ratio[:, None]
    return float(np.min(np.linalg.norm(nearest - np.array([x, y], float), axis=1)))


class CutDrag:
    """Drag a chain's cut line to pin it, right-click a cut to let the search run again.

    Dragging only moves the plane along its own normal, so the cut stays at a
    right angle to the route and the movable side does not change. The position
    is stored per chain in ``view_settings.reconstruction_cuts`` and re-runs the
    preview, so the cover, the air-gap and the spacing check follow immediately.
    """

    def __init__(self, window):
        self.w = window
        self.state = None
        self.line = None
        for name, fn in [('button_press_event', self.press),
                         ('motion_notify_event', self.move),
                         ('button_release_event', self.release),
                         ('figure_leave_event', self.leave)]:
            window.canvas.mpl_connect(name, fn)

    def _tolerance(self):
        inverse = self.w.ax.transData.inverted()
        origin = np.asarray(inverse.transform((0., 0.)), float)
        edge = np.asarray(inverse.transform((10., 0.)), float)
        return float(np.linalg.norm(edge - origin))

    def _plane(self, piece, points):
        """Plane of a piece's cut: point on it plus a unit normal toward the fixed side."""
        normals = np.linalg.norm(points[1:] - points[:-1], axis=1)
        index = int(np.argmax(normals))
        start, end = points[index], points[index + 1]
        direction = end - start
        span = float(np.linalg.norm(direction))
        if span <= 0.:
            return None
        normal = np.array([-direction[1], direction[0]], float) / span
        origin = (start + end) / 2.
        fixed = piece.get('immovable')
        if fixed is not None and not fixed.is_empty:
            target = np.asarray(fixed.centroid.coords[0], float)
            if float(np.dot(normal, target - origin)) < 0.:
                normal = -normal
        return normal, origin

    def press(self, event):
        w = self.w
        if not w.reconstruction_toggle.isChecked() or w.ax is None or event.inaxes is not w.ax:
            return
        if getattr(w.canvas, 'die_move_active', False) or getattr(w, '_in_animation', False):
            return
        picked = w.reconstruction_overlay.pick(event.xdata, event.ydata, self._tolerance())
        if picked is None:
            w.reconstruction_overlay.clear_hover()
            return
        w.reconstruction_overlay.highlight(picked)
        piece, points = picked
        plane = self._plane(piece, points)
        if plane is None:
            return
        normal, origin = plane
        key = (piece['layer'], int(piece['index']),
               tuple(sorted(int(v) for v in piece['vias'])))
        if event.button == 3 or event.dblclick:
            self.clear(key, piece)
            return
        if event.button != 1:
            return
        # The canvas' box selection also listens to the left button and starts a
        # rubber band on motion; drop its press so a drag pins the cut instead of
        # selecting a rectangle.
        selection = getattr(w, 'selection', None)
        if selection is not None:
            selection.press = None
            if getattr(selection, 'rubber', None) is not None:
                selection.rubber.remove()
                selection.rubber = None
        window = piece.get('cut_window')
        self.state = dict(piece=piece, normal=normal, origin=origin, key=key, window=window,
                          start=np.array([event.xdata, event.ydata], float),
                          pixel=np.array([event.x, event.y], float), moved=False,
                          along=float(np.dot(np.array([event.xdata, event.ydata], float) - origin, normal)))
        if self.line is None:
            from matplotlib.lines import Line2D
            self.line = Line2D([], [], color='#b3432f', lw=2.6, zorder=13.0)
            w.ax.add_line(self.line)
        self._paint(origin)
        w.statusBar().showMessage('拖动切口线：松开鼠标固定该串的切口，右键或双击恢复自动搜索')

    def _clamp(self, state, origin):
        """Keep the guide line inside the stretch the cut may use.

        The commit clamps with the same numbers, so the line the user sees is the
        line that gets pinned — no snapping back on release.
        """
        window = state.get('window')
        if window is None:
            return origin
        pivot = np.asarray(window['pivot'], float)
        normal = np.asarray(window['normal'], float)
        along = float(np.dot(origin - pivot, normal))
        along = min(max(along, float(window['low'])), float(window['high']))
        return pivot + normal * along

    def _paint(self, origin):
        w = self.w
        if self.line is None:
            return
        x0, x1 = w.view_bounds[0], w.view_bounds[2]
        y0, y1 = w.view_bounds[1], w.view_bounds[3]
        state = self.state
        if state is None:
            self.line.set_data([], [])
            return
        normal = state['normal']
        tangent = np.array([-normal[1], normal[0]], float)
        span = 2. * float(np.hypot(x1 - x0, y1 - y0)) + 1.
        start = origin - tangent * span
        end = origin + tangent * span
        self.line.set_data([start[0], end[0]], [start[1], end[1]])
        self.line.set_visible(True)

    def leave(self, event=None):
        if self.state is None:
            self.w.reconstruction_overlay.clear_hover()

    def move(self, event):
        state = self.state
        if state is None:
            # Not dragging: show which cut the pointer can grab.
            w = self.w
            if (not w.reconstruction_toggle.isChecked() or event.inaxes is not w.ax
                    or event.xdata is None or event.ydata is None):
                return
            picked = w.reconstruction_overlay.pick(event.xdata, event.ydata, self._tolerance())
            if w.reconstruction_overlay.highlight(picked):
                w.statusBar().showMessage('按住这条切口线拖动可以固定该串的切口；右键或双击恢复自动搜索')
            return
        if event.xdata is None or event.ydata is None:
            return
        selection = getattr(self.w, 'selection', None)
        if selection is not None and (selection.press is not None or selection.rubber is not None):
            selection.press = None
            if getattr(selection, 'rubber', None) is not None:
                selection.rubber.remove()
                selection.rubber = None
        if event.x is not None and not state['moved']:
            state['moved'] = bool(np.linalg.norm(
                np.array([event.x, event.y], float) - state['pixel']) >= 3.)
            if not state['moved']:
                return
        cursor = np.array([event.xdata, event.ydata], float)
        along = float(np.dot(cursor - state['origin'], state['normal']))
        origin = state['origin'] + state['normal'] * (along - state['along'])
        self._paint(self._clamp(state, origin))

    def release(self, event):
        state = self.state
        if state is None:
            return
        self.state = None
        if self.line is not None:
            self.line.set_data([], [])
            self.line.set_visible(False)
        if not state.get('moved'):
            # A plain click on the cut must not pin it: only a drag does.
            self.w.statusBar().showMessage('按住切口线拖动可以固定这条串的切口位置')
            return
        cursor = np.array([event.xdata, event.ydata], float) if event.xdata is not None else state['start']
        along = float(np.dot(cursor - state['origin'], state['normal']))
        origin = state['origin'] + state['normal'] * (along - state['along'])
        origin = self._clamp(state, origin)
        self.commit(state['key'], state['normal'], origin)

    def commit(self, key, normal, origin):
        w = self.w
        from dataclasses import replace
        cuts = [c for c in w.view_settings.reconstruction_cuts
                if (c[0], int(c[1]), tuple(sorted(int(v) for v in c[2]))) != key]
        cuts.append((key[0], int(key[1]), key[2], float(normal[0]), float(normal[1]),
                     float(origin[0]), float(origin[1])))
        w.view_settings = replace(w.view_settings, reconstruction_cuts=tuple(cuts))
        w.statusBar().showMessage('已固定该串的切口位置；右键或双击切口可恢复自动搜索')
        w.request_preview(quiet=True)

    def clear(self, key, piece):
        w = self.w
        from dataclasses import replace
        cuts = [c for c in w.view_settings.reconstruction_cuts
                if (c[0], int(c[1]), tuple(sorted(int(v) for v in c[2]))) != key]
        if len(cuts) == len(w.view_settings.reconstruction_cuts):
            w.statusBar().showMessage('这条串的切口本来就是自动搜索的')
            return
        w.view_settings = replace(w.view_settings, reconstruction_cuts=tuple(cuts))
        w.statusBar().showMessage('已恢复该串的自动切口搜索')
        w.request_preview()
