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
from matplotlib.collections import LineCollection, PatchCollection

# 冷色＝原来的位置（移动区原位、切口线），暖色＝重构后的铜与补线，这样
# “动过的”和“没动的”一眼分得开，不会和固定RDL本身的浅蓝混在一起。
MOVABLE_FILL = '#9fb3c8'
MOVABLE_EDGE = '#5d7894'
MOVED_FILL = '#e07a2f'
MOVED_EDGE = '#8f4712'
BRIDGE_FILL = '#f5b955'
BRIDGE_EDGE = '#a86f16'
CUT_COLOR = '#8a4b3a'


class ReconstructionOverlay:
    def __init__(self, window):
        self.w = window
        self.movable = None
        self.moved = None
        self.bridges = None
        self.cuts = None
        self.stamp = None
        self.table_stamp = None

    def active(self):
        return self.w.reconstruction_toggle.isChecked()

    def data(self):
        frame = getattr(self.w, 'current_frame', None)
        return (frame or {}).get('reconstruction')

    def draw(self):
        w = self.w
        if self.movable is None or self.movable.axes is None:
            self.movable = PatchCollection([], facecolors=MOVABLE_FILL, edgecolors=MOVABLE_EDGE,
                                           linewidths=.8, linestyles=(0, (4, 2)), alpha=.20, zorder=9.3)
            self.moved = PatchCollection([], facecolors=MOVED_FILL, edgecolors=MOVED_EDGE,
                                         linewidths=1.1, alpha=.78, zorder=9.5)
            self.bridges = PatchCollection([], facecolors=BRIDGE_FILL, edgecolors=BRIDGE_EDGE,
                                           linewidths=1.0, alpha=.80, zorder=9.7)
            self.cuts = LineCollection([], colors=CUT_COLOR, linewidths=1.2, linestyles=(0, (6, 3)),
                                       alpha=.90, zorder=9.8)
            for artist in (self.movable, self.moved, self.bridges, self.cuts):
                w.ax.add_collection(artist)
            self.stamp = None
        data = self.data() if self.active() else None
        stamp = (id(data), tuple(k for k, b in w.visible_boxes.items() if b.isChecked()), w.view_bounds)
        if stamp != self.stamp:
            self.stamp = stamp
            origin, target, bridge, cut = [], [], [], []
            for q in (data or {}).get('pieces', []):
                if not w.visible_boxes[q['layer']].isChecked():
                    continue
                if not _in_view(q['movable'].bounds, w.view_bounds) and not _in_view(q['moved'].bounds, w.view_bounds):
                    continue
                origin.append(w.geometry_patch(q['movable']))
                target.append(w.geometry_patch(q['moved']))
                if q['bridge'] is not None and not q['bridge'].is_empty:
                    bridge.append(w.geometry_patch(q['bridge']))
                for line in _lines(q['cut']):
                    cut.append(line)
            self.movable.set_paths(origin)
            self.movable.set_visible(bool(origin))
            self.moved.set_paths(target)
            self.moved.set_visible(bool(target))
            self.bridges.set_paths(bridge)
            self.bridges.set_visible(bool(bridge))
            self.cuts.set_segments(cut)
            self.cuts.set_visible(bool(cut))
        if not getattr(w, '_in_animation', False) and (id(data), stamp[1]) != self.table_stamp:
            self.table_stamp = (id(data), stamp[1])
            self.fill_table(data)

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
                            '补上裂隙。')
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
            values = [f"{q['layer']} #{q['index'] + 1}", f"DIE{q['die'] + 1}", f"net {q['net']}",
                      f"{q['movable_area_um2']:.1f}", f"{q['shift_um']:.3f}", gap, bridge, length, state]
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
                                '切口缝长＝最长切口面的长度。选中该行可把视图跳到该区域。')
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        detail = data['summary']
        if not detail['pieces']:
            summary.setText('当前姿态下没有移动过孔（或过孔下方没有固定RDL铜），无需重构。 ' + data['scope'])
            return
        parameters = data['parameters']
        cut_rule = (f"切口垂直于该串的走线（与走线两侧各成 90°），量到串里最远的落盘再往外 {parameters['length_um']:.3f} μm"
                    if parameters['length_um'] > 0. else
                    f"切口垂直于该串的走线（与走线两侧各成 90°），量到串里最远的落盘再往外 "
                    f"{parameters['landing_diameters']:.1f} 个落盘直径（可用“切口距离 D”改写）")
        summary.setText(
            f"按运动过孔自动划分移动区 {detail['pieces']} 段（同一网络彼此相距不远的落盘合为一条串，整条串只切一刀），"
            f"移动面积合计 {detail['movable_area_um2']:.1f} μm²，"
            f"最大位移 {detail['max_shift_um']:.3f} μm；补线 {detail['bridges']} 处"
            f"（由两面切口的 4 个端点围成多边形，补线面积合计 {detail.get('cover_area_um2', 0.):.1f} μm²，"
            f"外扩 {detail['width_um']:.3f} μm），"
            f"整块随动、无不动铜可接 {detail['orphans']} 块；"
            f"移动后与留下的铜贴合（裂隙为 0）{detail.get('flush', 0)} 段"
            f"——铜沿运动方向继续延伸时，任何切线位置都拉不开这两块，"
            f"由补线多边形把两者相接的地方盖住。"
            f"同一块固定RDL若跨多个芯片，移动区只取本 DIE 芯片边框（128/0）以内的铜，"
            f"边框外的铜留在原位归相邻 DIE，边框线也算一条切口并按 4 端点补线。"
            f"{cut_rule}；落盘外扩 overlap={parameters['overlap_um']:.3f} μm。 "
            + data['scope'])

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
