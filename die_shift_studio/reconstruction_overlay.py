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
import shapely
from .reconstruction import normalize_via_refs, cut_identity_matches
# import shapely.Point
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle
from matplotlib.lines import Line2D

# 第一刀是否允许手工拖动。第二刀体系与本开关无关，单独工作。
DRAG_FIRST_CUT = True
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
# Manual 动区/静区: boxes the user drew to override the software's split.

REGION_COLORS = {'move': '#2f7bd6', 'stay': '#c2571f'}
REGION_LABELS = {'move': '动区', 'stay': '静区'}
# Dead ends of a chain: the two landings a chain starts and stops at.
# Chain ends: the landing the chain starts from moves with the die, the one it
# stops at is preferably a fixed landing outside the chip frame.
CHAIN_START_COLOR = '#ff3000'
CHAIN_END_COLOR   = '#0086d6'
CHAIN_END_UM      = 9.
CUT_SEAM_COLOR = '#7a2f10'
SECOND_CUT_COLOR='#1f5c2f' # 2nd刀

SPAN_FILL = '#6A5ACD'      # 固定活动区
SPAN_EDGE = '#2b6e96'


class ReconstructionOverlay:
    def __init__(self, window):
        self.w = window
        self.movable = None
        self.moved = None
        self.bridges = None
        self.cuts = None
        self.manual_cuts = None
        self.extra_cuts = None
        self.moved_cuts = None
        self.drag_line = None
        self.stamp = None
        self.table_stamp = None
        self.arrow_artists = []

        self.end_artists = []
        self.region_artists = []
        self.hover = None
        self.hover_key = None
        self._pick_cache = (None, [])

        # The chain whose two bounding cuts are shown and draggable. Nothing is
        # selected by default: on a pour that carries many pads the lines of
        # every bridge would otherwise cross the copper and look like cuts.
        self.selected = None
        # Which of the selected bridge's lines the user is working on: None for
        # the bridge itself, else 'cut' / 'extra' / 'moved'.
        self.selected_kind = None


    def active(self):
        return self.w.reconstruction_toggle.isChecked()

    def highlight(self, picked):
        """Bright outline under the pointer, so the cut that can be dragged is obvious."""
        w = self.w
        if self.hover is None or self.hover.axes is not w.ax:
            from matplotlib.lines import Line2D
            self.hover = Line2D([], [], color='#1f9ad6', lw=4.4, alpha=.9,
                                solid_capstyle='round', zorder=13.1)
            w.ax.add_line(self.hover)
        if picked is None:
            self.hover_key = None
            self.hover.set_data([], [])
            self.hover.set_visible(False)
            return False
        piece, points, kind = picked
        key = (piece['layer'], int(piece['index']),
               normalize_via_refs(piece.get('via_refs',piece['vias'])), kind)
        changed = key != self.hover_key
        self.hover_key = key
        if points is None or len(points) < 2:
            # A bridge is picked by its area, not by a line: nothing to outline.
            self.hover.set_data([], [])
            self.hover.set_visible(False)
            return changed
        normals = np.linalg.norm(points[1:] - points[:-1], axis=1)
        index = int(np.argmax(normals))
        start, end = points[index], points[index + 1]
        self.hover.set_data([start[0], end[0]], [start[1], end[1]])
        self.hover.set_visible(True)
        return changed

    def clear_hover(self):
        self.highlight(None)

    @staticmethod
    def _key(piece):
        """Identity of one chain: its layer, its fixed-RDL feature, its landings."""
        return (piece['layer'], int(piece['index']),
                normalize_via_refs(piece.get('via_refs',piece['vias'])))

    def data(self):
        frame = getattr(self.w, 'current_frame', None)
        return (frame or {}).get('reconstruction')

    def draw(self):
        w = self.w
        if self.movable is None or self.movable.axes is not w.ax:
            self.movable = PatchCollection([], facecolors=MOVABLE_FILL, edgecolors=MOVABLE_EDGE,
                                           linewidths=.8, linestyles=(0, (4, 2)), alpha=.20, zorder=9.3)
            self.fixed = PatchCollection([], facecolors=FIXED_FILL, edgecolors=FIXED_EDGE,
                                         linewidths=.6, alpha=.55, zorder=9.4)
            self.moved = PatchCollection([], facecolors=MOVED_FILL, edgecolors=MOVED_EDGE,
                                         linewidths=1.1, alpha=.78, zorder=9.5)
            self.bridges = PatchCollection([], facecolors=BRIDGE_FILL, edgecolors=BRIDGE_EDGE,
                                           linewidths=1.0, alpha=.80, zorder=9.7)
            self.spans = PatchCollection([], facecolors=SPAN_FILL, edgecolors=SPAN_EDGE,
                                         linewidths=.7, alpha=.45, zorder=9.45)
            self.seams = LineCollection([], colors=CUT_SEAM_COLOR, linewidths=3.4,
                                        linestyles='solid', zorder=9.95)
            self.second_cuts = LineCollection([], colors=SECOND_CUT_COLOR, linewidths=3.4,
                                              zorder=9.96)
            self.cuts = LineCollection([], colors=CUT_COLOR, linewidths=1.2, linestyles=(0, (6, 3)),
                                       alpha=.90, zorder=9.8)
            # A cut the user dragged is drawn solid and thicker, so it is clear
            # which seams follow the software's search and which are pinned.
            self.extra_cuts = LineCollection([], colors='#8a5f2f', linewidths=1.7,
                                             linestyles=(0, (2, 2)), zorder=6.4)
            self.moved_cuts = LineCollection([], colors='#b9612c', linewidths=1.9,
                                             linestyles=(0, (5, 2)), zorder=6.5)
            self.manual_cuts = LineCollection([], colors='#b3432f', linewidths=2.6,
                                              alpha=.95, zorder=9.9)
            for artist in (self.movable, self.fixed, self.spans, self.moved, self.bridges,
                           self.cuts, self.manual_cuts, self.extra_cuts, self.moved_cuts,
                           self.seams, self.second_cuts):
                w.ax.add_collection(artist)
            self.stamp = None
        data = self.data() if self.active() else None

        regions = tuple(getattr(w.view_settings,'reconstruction_regions',()) or ())
        stamp = (regions,id(data), tuple(k for k, b in w.visible_boxes.items() if b.isChecked()), w.view_bounds, self.selected)
        if stamp != self.stamp:
            self.stamp = stamp
            # origin, target, bridge, cut, still, manual, extra, moved_lines, seam_lines = ([], [], [], [],
            #                                                                               [], [], [], [], [])
            origin, target, bridge, cut, still, manual, extra, moved_lines, seam_lines, \
                seam_seconds, spans = [], [], [], [], [], [], [], [], [], [], []
            selected = self.selected
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
                if q.get('span') is not None and not q['span'].is_empty:
                    spans.append(w.geometry_patch(q['span']))
                if q['bridge'] is not None and not q['bridge'].is_empty:
                    bridge.append(w.geometry_patch(q['bridge']))
                # A cut is a line only where it really opens a fissure: without
                # a bridge the copper is joined anyway and the line would read
                # as a broken trace. A cut the user pinned stays visible even
                # then, so the table's “手动切口未生效” has something to point at.
                if q['bridge'] is not None or q.get('manual') or q.get('frame_seam'):
                    seam = bool(q.get('frame_seam')) and not q.get('manual')
                    source = q.get('moved_cut') if seam else q['cut']
                    for line in _lines(source):
                        (manual if q.get('manual') else
                         seam_lines if seam else cut).append(line)



                # Both lines that bound a bridge are always drawn: the first cut
                # on the moving side and the second one on the copper that stays.
                # Together they are what makes the bridge a stretch of given-up
                # copper instead of a fissure, so neither is hidden.
                if (q['bridge'] is not None or q.get('frame_seam')) and not q.get('manual'):
                    for line in _lines(q.get('extra_cut')):
                        (seam_seconds if q.get('frame_seam') else extra).append(line)
                # The moved copy of the first cut is a guide for the drag, so it
                # is drawn for the selected bridge only.
                if selected is not None and self._key(q) == selected:
                    if q.get('moved_cut') is not None and not q['moved_cut'].is_empty:
                        for line in _lines(q['moved_cut']):
                            moved_lines.append(line)

            self.movable.set_paths(origin)
            self.movable.set_visible(bool(origin))
            self.spans.set_paths(spans)
            self.spans.set_visible(bool(spans))
            self.fixed.set_paths(still)
            self.fixed.set_visible(bool(still))
            self.seams.set_segments(seam_lines)
            self.second_cuts.set_segments(seam_seconds)
            self.moved.set_paths(target)
            self.moved.set_visible(bool(target))
            self.bridges.set_paths(bridge)
            self.bridges.set_visible(bool(bridge))
            self.cuts.set_segments(cut)
            self.cuts.set_visible(bool(cut))
            self.manual_cuts.set_segments(manual)
            self.manual_cuts.set_visible(bool(manual))
            self.extra_cuts.set_segments(extra)
            self.extra_cuts.set_visible(bool(extra))
            self.moved_cuts.set_segments(moved_lines)
            self.moved_cuts.set_visible(bool(moved_lines))
            # Manual 动区/静区 boxes: translucent, dashed, one per row of

            for artist in self.region_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
            self.region_artists = []
            for layer, bounds, role in regions:
                if layer not in w.visible_boxes or not w.visible_boxes[layer].isChecked():
                    continue
                if not _in_view(tuple(bounds), w.view_bounds):
                    continue
                colour = REGION_COLORS.get(role, '#555555')
                x0, y0, x1, y1 = bounds
                rect = Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=colour, alpha=.10,
                                 edgecolor=colour, lw=1.5, linestyle=(0, (5, 3)),
                                 zorder=9.85)
                w.ax.add_patch(rect)
                self.region_artists.append(rect)
            # 链的两个尽头：起点、终点各一色；终点若落在芯片边框外的固定区，
            # 画成实心点，只是“另一端”时画空心圈。
            for artist in self.end_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
            self.end_artists = []
            seen = set()
            for q in (data or {}).get('pieces', []):
                if not w.visible_boxes[q['layer']].isChecked():
                    continue
                ends = list(q.get('chain_ends') or ())
                anchor = q.get('chain_anchor')
                marks = ends + ([anchor] if anchor else [])
                if not (_in_view(q['moved'].bounds, w.view_bounds)
                        or any(_in_view((x, y, x, y), w.view_bounds) for x, y in marks)):
                    continue
                for n, (x, y) in enumerate(ends):
                    key = (round(x, 1), round(y, 1))
                    if key in seen:
                        continue
                    seen.add(key)
                    colour = CHAIN_START_COLOR if n == 0 else CHAIN_END_COLOR
                    self.end_artists.extend(w.ax.plot(
                        [x], [y], ls='none', marker='o', ms=12, mfc='none',
                        mec='#606060', mew=1.6, zorder=12.8))
                    self.end_artists.extend(w.ax.plot(
                        [x], [y], ls='none', marker='o', ms=5.6, mfc=colour,
                        mec='none', zorder=12.9))
                if anchor and len(ends) > 1:
                    ex, ey = ends[1]
                    self.end_artists.append(w.ax.add_line(Line2D(
                        [ex, anchor[0]], [ey, anchor[1]], color=CHAIN_END_COLOR,
                        lw=1.1, ls=(0, (3, 2)), alpha=.9, zorder=12.7)))
                    akey = (round(anchor[0], 1), round(anchor[1], 1))
                    if akey not in seen:
                        seen.add(akey)
                        self.end_artists.append(w.ax.add_patch(Circle(
                            (anchor[0], anchor[1]), CHAIN_END_UM * .8, fill=False,
                            ec=CHAIN_END_COLOR, lw=1.8, ls=':', zorder=12.85)))
            for x, y in (data or {}).get('stays', ()):
                if _in_view((x, y, x, y), w.view_bounds):
                    self.end_artists.append(w.ax.add_patch(Circle(
                        (x, y), CHAIN_END_UM * .5, fill=False, ec='#8c8c8c',
                        lw=.9, ls=':', zorder=12.4)))
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
        stamp = (id(data), self.selected)
        if self._pick_cache[0] != stamp:
            lines = []
            for q in self.pieces():
                for kind, geometry in (('cut', q['cut']), ('extra', q.get('extra_cut')),
                                       ('moved', q.get('moved_cut'))):
                    if geometry is None:
                        continue
                    if kind == 'cut' and q['bridge'] is None and not q.get('manual'):
                        continue
                    # Both cuts of a bridge can be dragged; the moved guide line
                    # belongs to the selected bridge only.
                    if kind == 'extra' and q.get('extra_cut') is None:
                        continue
                    if kind == 'moved' and self._key(q) != self.selected:
                        continue
                    for line in _lines(geometry):
                        points = np.asarray(line, float).reshape(-1, 2)
                        if len(points) >= 2:
                            lines.append((q, points, kind))
            self._pick_cache = (stamp, lines)
        best = None
        for q, points, kind in self._pick_cache[1]:
            # The widened bridge's far edge is a thin helper: it needs a tighter
            # grab radius, otherwise it steals the cut it belongs to.
            distance = _segment_distance(points, x, y) * ({'extra': 1.6, 'moved': 1.3}.get(kind, 1.))
            if best is None or distance < best[0]:
                best = (distance, q, points, kind)
        # Is the click inside the amber patch of some bridge? A two-cut bridge
        # covers its own first cut (the patch runs from the moved cut to the far
        # one), so containment alone would swallow every drag of that line.
        inner = None
        for q in self.pieces():
            area = q.get('bridge')
            if area is None or area.is_empty:
                continue
            if area.contains(shapely.Point(x, y)):
                inner = q
                break
        close = best is not None and best[0] <= tolerance
        if close:
            # The two lines that bound the selected bridge win: they are what the
            # user drags to resize it. Inside a patch a line has to be hit
            # squarely, otherwise a click meant for the bridge would start a cut
            # drag at a zoomed-out view.
            if best[3] in ('extra', 'moved') and self.selected is not None:
                return best[1], best[2], best[3]
            if inner is None or best[0] <= tolerance * .35:
                return best[1], best[2], best[3]
        # Anywhere else on a bridge selects it, which is what reveals its two
        # cuts. The pick radius is 10 pixels, which at a zoomed-out view is far
        # wider than a bridge: preferring the nearest line there would turn every
        # click on a bridge into a cut drag (“只会改变切口”).
        if inner is not None:
            return inner, None, 'bridge'
        if not close:
            return None
        return best[1], best[2], best[3]

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
                            '同一网络、彼此相距不远的多个落盘算作一条串；切口只落在“前后宽度不变”的扇出段上——'
                            '近端第一刀、远端第二刀，两刀之间整段让出当桥接区，宽铜皮（铜皮主体）不切，'
                            '没有扇出段时退回单刀（最远落盘再往外 D）。'
                            '同一块固定RDL跨多个芯片时，移动区只取本 DIE 芯片边框以内的铜；'
                            '移动区按当前姿态移动后，用两个切口面的 4 个端点围成的多边形'
                            '补上这段缝，再用重构后的铜做落盘 air-gap。'
                            '也可以点“框选动区/框选静区”在图上框选，手动覆盖某块铜的动静归属。')
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
            span = '—' if q.get('bridge_span_um') is None else f"{q['bridge_span_um']:.3f}"
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
            elif q.get('manual_lost'):
                state = f"{state}｜手动切口未生效（已回自动）"
            if q.get('tapered'):
                state = f"{state}｜切口在线宽突变处"
            values = [f"{q['layer']} #{q['index'] + 1}", f"DIE{q['die'] + 1}", f"net {q['net']}",
                      f"{q['movable_area_um2']:.1f}", f"{q['shift_um']:.3f}", gap, bridge, span, length,
                      clearance, state]
            for j, value in enumerate(values):
                item = W.QTableWidgetItem(str(value))
                item.setToolTip('移动区＝这条串的过孔下方（再往外量切口距离 D）的固定RDL铜，'
                                '按该 DIE 的刚体变换移动；同一网络彼此相距不远的落盘算一条串；'
                                '切口线垂直于这条串的走线、与走线两侧各成 90°，'
                                '只在“前后宽度不变”的扇出段上切两刀（近端第一刀、远端第二刀，两刀之间整段让出）；'
                                '第二刀只走到补块仍在同一条链的铜内，走线弯折时在转角之前就收口；'
                                '找不到扇出段时退回单刀，量到串里最远的落盘再往外；宽铜皮（铜皮主体）不切；'
                                '同一块固定RDL跨多个芯片时，只有本 DIE 芯片边框（128/0）以内的铜跟着走，'
                                '边框外的铜归相邻 DIE、留在原位，边框边只当自然分界、不画切口线；'
                                '裂隙＝移动后的铜到留在原位的铜的最近距离（两刀时就是这段桥接区的长度）；'
                                '补线＝移动侧切口面与固定侧切口面的 4 个端点围成的多边形，不论裂隙大小都会补上，'
                                '补块沿走线方向至少 30 μm（不足时多让出一小段不动的铜，不越过铜端），'
                                '两刀面扫出的四边形退化时退回切口面直接扫到它移动后的位置；'
                                '移动后压到固定铜上（两面重叠）时同样补：沿着移动后的铜与不动铜相接的那段轮廓、'
                                '在不动的那块铜上铺一条至少 30 μm 宽的带（取带与该铜的交集、再减去移动铜），'
                                '宽度与走向都跟着那条接缝，不会只在接缝旁边切出一条窄条；'
                                '两面只是相切（重叠为零）时才按“贴合（直接相接）”、不补；'
                                '切口缝长＝最长切口面的长度（两刀等宽，就是走线宽度）；'
                '手动区域＝在图上框出来的动区（并入移动区）或静区（留在原位），会覆盖相应铜的自动划分；'
                '间距＝重构后的铜（移动后的铜＋补线）到同层其他网络最近铜的距离，'
                '低于有效线距为“不足”、低于两倍为“临界”，图上用尺寸箭头标出。'
                '选中该行可把视图跳到该区域。')
                table.setItem(i, j, item)
        table.blockSignals(False)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        detail = data['summary']
        if not detail['pieces']:
            summary.setText('当前姿态下没有移动过孔，无需重构。 ' + data['scope'])
            return
        parameters = data['parameters']
        cut_rule = (f"切口垂直于该串的走线，第一刀固定在串里最远的落盘之外 {parameters['length_um']:.3f} μm 处"
                    if parameters['length_um'] > 0. else
                    "-")
        parts=[
            f"按运动过孔自动划分移动区 {detail['pieces']} 段、共 {detail.get('chains', 0)} 条走线"


        ]
        parts.append(f"其中终点落在固定区落盘 {detail.get('fixed_ends', 0)} 条；")
        parts.append(f"（边框刀 {detail.get('seam_pieces', 0)} 块、第二刀共 "
                     f"{detail.get('seam_seconds', 0)} 条，最多一块 ")
        #              f"{detail.get('seam_most', 0)} 条 {detail.get('seam_big')}）")
        # if detail.get('seam_big_n', 0):
        #     parts.append("（多刀块 %d 个：%s）" % (detail['seam_big_n'], detail.get('seam_big', '')))
        # if detail.get('seam_cuts_n', 0):
        #     parts.append("（多刀铜块 %d 个：%s）" % (detail['seam_cuts_n'], detail.get('seam_cuts', '')))
        #
        # if detail.get('seam_debug_n', 0):
        #     parts.append("（第二刀调试 %d：%s）"
        #                  % (detail['seam_debug_n'], detail.get('seam_debug', '')))
        parts.append(
            f"（第二刀已锁定 {detail.get('second_pinned', 0)} 条"
            f"，存了 {detail.get('second_rows', 0)} 条：{detail.get('second_row', '-')}）")

        parts.append(data['scope'])
        summary.setText(''.join(parts))

    # ------------------------------------------------------------ navigation
    def piece_for(self, key):
        """Piece with this selection key, or None when it went away."""
        if key is None:
            return None
        for piece in self.pieces():
            if self._key(piece) == key:
                return piece
        return None

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


def _pin_second(settings, piece, x, y, direction):
    """Store the second cut of this piece as an absolute line."""
    from dataclasses import replace
    if not piece or direction is None:
        return settings
    layer=piece.get('layer'); index=piece.get('index')
    if layer is None or index is None:
        return settings
    key=(layer,int(index),normalize_via_refs(piece.get('via_refs',piece.get('vias') or ())))
    rows=tuple(q for q in (getattr(settings,'reconstruction_second_cuts',()) or ())
               if not (str(q[0])==str(key[0]) and int(q[1])==key[1]
                       and cut_identity_matches(q[2],key[2])))
    rows=rows+((key[0],key[1],key[2],float(direction[0]),float(direction[1]),
                float(x),float(y)),)
    return replace(settings,reconstruction_second_cuts=rows)


def _rotate_second(settings, piece, degrees):
    """Turn this piece's second cut about its own midpoint."""
    if not piece:
        return settings
    line=piece.get('extra_cut')
    if line is None or line.is_empty:
        return settings
    vec=np.asarray(line.coords[-1],float)-np.asarray(line.coords[0],float)
    size=float(np.hypot(*vec))
    if size<=1e-9:
        return settings
    angle=np.radians(float(degrees)); cos_,sin_=np.cos(angle),np.sin(angle)
    turned=np.array([cos_*vec[0]-sin_*vec[1],sin_*vec[0]+cos_*vec[1]])
    anchor=np.asarray(line.centroid.coords[0],float)
    return _pin_second(settings,piece,float(anchor[0]),float(anchor[1]),turned)


def _buttons(window,enabled):
    """Enable the ⟲/⟳ second-cut buttons that stand in for a drag."""
    for name in ('second_left','second_right','cut_left','cut_right'):
        button=getattr(window,name,None)
        if button is not None:button.setEnabled(bool(enabled))





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
        if not w.reconstruction_toggle.isChecked():
            return
        if w.ax is None:
            return
        if event.inaxes is not w.ax:
            return
        if getattr(w.canvas, 'die_move_active', False) or getattr(w, '_in_animation', False):
            return
        overlay = w.reconstruction_overlay
        if event.xdata is None or event.ydata is None:return
        region=getattr(w,"region_select",None)
        if region is not None and region.mode is not None:return
        picked = overlay.pick(event.xdata, event.ydata, self._tolerance())
        if picked is None:
            if overlay.selected is not None:
                overlay.selected = None
                overlay.selected_kind = None
                overlay.stamp = None
                _buttons(w, False)
                w.canvas.draw_idle()
            overlay.clear_hover()
            return
        piece, points, kind = picked
        if kind in ('cut','moved') and len(_lines(piece.get('cut')))!=1:
            w.statusBar().showMessage('多个切口的合并区域不能拖动第一刀')
            return
        if kind in ('cut', 'moved') and not DRAG_FIRST_CUT:
            w.statusBar().showMessage('第一刀不再支持拖动：请拖第二刀（绿色），或用 ⟲/⟳ 旋转')
            return
        if kind == 'bridge':
            # Clicking a bridge shows its two bounding cuts (one at the moved
            # copper, the second cut in the copper that stays) and nothing else:
            # either of them can then be dragged to resize this bridge alone.
            overlay.selected = overlay._key(piece)
            overlay.selected_kind = None
            overlay.stamp = None
            _buttons(w,False)
            overlay.highlight(None)
            w.canvas.draw_idle()
            w.statusBar().showMessage('已选中这条桥接区：拖动它两侧的切口线可加长或缩短桥接区，点击空白处取消')
            return
        overlay.highlight(picked)
        overlay.selected = overlay._key(piece)
        overlay.selected_kind = kind
        _buttons(w,True)
        plane = self._plane(piece, points)
        if plane is None:
            return
        normal, origin = plane
        if kind == 'moved' and piece.get('cut_window'):
            # The line by the moved copper has to travel along the cut's own
            # normal.  Aiming it at the stationary centroid can point the other
            # way, and then a drag only pulls the cut inward and the bridge never
            # grows -- which is exactly “只会改变切口，无法改变桥接区”.
            normal = np.asarray(piece['cut_window']['normal'], float)
        key = (piece['layer'], int(piece['index']),
               normalize_via_refs(piece.get('via_refs',piece['vias'])))
        if event.button == 3 or event.dblclick:
            self.clear(key, piece, kind)
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



        self.state = dict(piece=piece, normal=normal, origin=origin, key=key, kind=kind,
                          start_origin=np.array(origin,float),
                          start=np.array([event.xdata, event.ydata], float),
                          pixel=np.array([event.x, event.y], float), moved=False,
                          along=float(np.dot(np.array([event.xdata, event.ydata], float) - origin, normal)))
        if self.line is None or self.line.axes is not w.ax:
            from matplotlib.lines import Line2D
            self.line = Line2D([], [], color='#b3432f', lw=2.6, zorder=13.0)
            w.ax.add_line(self.line)
        self._paint(origin)
        w.statusBar().showMessage('拖动切口线：松开鼠标固定该串的切口，右键或双击恢复自动搜索')

    def _clamp(self, state, origin):
        if state.get('kind') == 'extra':
            return origin                 # 第二刀只跟手走，不做窗口夹取
        window = state.get('window')
        if window is None:
            return origin
        pivot = np.asarray(window['pivot'], float)
        normal = np.asarray(window['normal'], float)
        low = float(window['low']); high = float(window['high'])
        if state.get('kind') == 'moved':
            # The moved line edits the cut plane: clamp the plane, then put the
            # guide line back where that cut lands.
            delta = float(np.dot(origin - state['start_origin'], normal))
            target = min(max(state.get('cut_along', 0.) + delta, low), high)
            return state['start_origin'] + normal * (target - state.get('cut_along', 0.))
        along = float(np.dot(origin - pivot, normal))
        along = min(max(along, low), high)
        copper = state.get('copper')
        if copper is not None and not copper.is_empty:
            from shapely.geometry import LineString
            tangent = np.array([-normal[1], normal[0]], float)
            size = float(np.hypot(*(np.asarray(copper.bounds)[2:] - np.asarray(copper.bounds)[:2]))) + 2. * abs(along) + 10.
            def cuts(distance):
                point = pivot + normal * distance
                line = LineString([point - tangent * size, point + tangent * size])
                return shapely.intersection(line, copper).length > 1e-9
            if not cuts(along):
                for step in np.linspace(along, low, 12)[1:]:
                    if cuts(float(step)):
                        along = float(step); break
                else:
                    along = low
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
        self.w.canvas.draw_idle()

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
        self.w.canvas.draw_idle()
        if event.inaxes is not self.w.ax or event.xdata is None or event.ydata is None:return
        cursor = np.array([event.xdata, event.ydata], float)
        along = float(np.dot(cursor - state['origin'], state['normal']))
        origin = state['origin'] + state['normal'] * (along - state['along'])
        origin = self._clamp(state, origin)
        self.commit(state, origin)

    def commit(self, state, origin):
        w=self.w
        kind=state.get('kind','cut')
        if kind=='moved':
            cut=state['piece'].get('cut')
            if cut is None or cut.is_empty:return
            delta=float(np.dot(np.asarray(origin)-state['start_origin'],state['normal']))
            origin=np.asarray(cut.centroid.coords[0])+state['normal']*delta
        w.view_settings=_store_cut(w.view_settings,state['piece'],state['normal'],origin,kind)
        w.dirty=True
        w.reconstruction_overlay.stamp=None
        w.statusBar().showMessage('已固定切口位置；右键或双击恢复自动位置')
        w.request_preview(quiet=True)

    def clear(self, key, piece, kind='cut'):
        from dataclasses import replace
        w=self.w
        overlay=w.reconstruction_overlay
        overlay.selected=None;overlay.selected_kind=None;overlay.stamp=None
        _buttons(w,False)
        which='reconstruction_second_cuts' if kind=='extra' else 'reconstruction_cuts'
        def keep(c):
            return not (str(c[0])==str(key[0]) and int(c[1])==int(key[1])
                        and cut_identity_matches(c[2],key[2]))
        changes={which:tuple(c for c in getattr(w.view_settings,which,()) if keep(c))}
        if kind=='extra':
            changes['reconstruction_extra_cuts']=tuple(c for c in getattr(w.view_settings,'reconstruction_extra_cuts',()) if keep(c))
        else:
            changes['reconstruction_dropped_cuts']=tuple(c for c in getattr(w.view_settings,'reconstruction_dropped_cuts',()) if keep(c))
        w.view_settings=replace(w.view_settings,**changes)
        w.dirty=True
        w.statusBar().showMessage('已恢复该切口的自动位置')
        w.request_preview()


def _store_cut(settings,piece,normal,origin,kind='cut'):
    from dataclasses import replace
    key=ReconstructionOverlay._key(piece)
    which='reconstruction_second_cuts' if kind=='extra' else 'reconstruction_cuts'
    def keep(c):
        return not (str(c[0])==str(key[0]) and int(c[1])==key[1]
                    and cut_identity_matches(c[2],key[2]))
    base=settings
    if kind!='extra':
        # Freeze the far edge while moving the first edge independently.
        far=piece.get('extra_cut')
        if far is not None and not far.is_empty:
            coords=shapely.get_coordinates(far)
            direction=coords[-1]-coords[0]
            if np.linalg.norm(direction)>1e-9:
                anchor=np.asarray(far.centroid.coords[0])
                base=_pin_second(base,piece,anchor[0],anchor[1],direction)
    vector=np.array([-normal[1],normal[0]]) if kind=='extra' else normal
    rows=[c for c in getattr(base,which,()) if keep(c)]
    rows.append((key[0],key[1],key[2],float(vector[0]),float(vector[1]),float(origin[0]),float(origin[1])))
    dropped=tuple(c for c in getattr(base,'reconstruction_dropped_cuts',()) if keep(c))
    return replace(base,**{which:tuple(rows)},reconstruction_dropped_cuts=dropped)


def nudge_cut(settings,piece,step,kind='cut'):
    line=piece.get('extra_cut') if kind=='extra' else piece.get('cut')
    if line is None or line.is_empty:return settings
    parts=list(line.geoms) if hasattr(line,'geoms') else [line]
    # A merged, multi-seam piece has no unambiguous first plane.
    if kind!='extra' and len(parts)!=1:return settings
    line=max(parts,key=lambda q:q.length)
    coords=shapely.get_coordinates(line)
    direction=coords[-1]-coords[0]
    length=float(np.linalg.norm(direction))
    if length<=1e-9:return settings
    normal=np.array([-direction[1],direction[0]])/length
    origin=np.asarray(line.centroid.coords[0])+normal*float(step)
    return _store_cut(settings,piece,normal,origin,kind)


class RegionSelect:
    """Box-select a 动区 (movable) or 静区 (stays put) on the reconstruction page.

    The rectangles are stored per fixed-RDL layer in
    ``view_settings.reconstruction_regions`` and applied after the automatic
    split, so the software's guess stays visible wherever the user has not
    overridden it. Commit re-runs the preview quietly, like the cut drag.
    """

    def __init__(self, window):
        self.w = window
        self.mode = None
        self.state = None
        self.rect = None
        for name, fn in [('button_press_event', self.press),
                         ('motion_notify_event', self.move),
                         ('button_release_event', self.release)]:
            window.canvas.mpl_connect(name, fn)

    def set_mode(self, mode):
        """'move', 'stay' or None (off)."""
        self.mode = mode
        if mode is None:
            self._drop()
            self.w.statusBar().showMessage('已退出手动区域框选')

    def _drop(self):
        if self.rect is not None:
            try:
                self.rect.remove()
            except Exception:
                pass
            self.rect = None
        self.state = None

    def _point(self, event):
        if event.xdata is None or event.ydata is None:
            return None
        return float(event.xdata), float(event.ydata)

    def press(self, event):
        if self.mode is None or event.button != 1 or event.inaxes is not self.w.ax:
            return
        point = self._point(event)
        if point is None:
            return
        if not self.w.reconstruction_toggle.isChecked():return
        # The canvas rubber band and the cut drag listen to the same press.
        selection = getattr(self.w, 'selection', None)
        if selection is not None:
            selection.press = None
            if getattr(selection, 'rubber', None) is not None:
                try:
                    selection.rubber.remove()
                except Exception:
                    pass
                selection.rubber = None
        self.state = {'start': point, 'current': point}
        colour = REGION_COLORS.get(self.mode, '#555555')
        self.rect = Rectangle(point, 0., 0., facecolor=colour, alpha=.12,
                              edgecolor=colour, lw=1.5, linestyle=(0, (5, 3)),
                              zorder=12.2)
        self.w.ax.add_patch(self.rect)
        self.w.statusBar().showMessage(f'框选{REGION_LABELS[self.mode]}：松开鼠标即写入')
        return True

    def move(self, event):
        if self.state is None or self.rect is None or event.button != 1:
            return
        point = self._point(event)
        if point is None:
            return
        self.state['current'] = point
        self.w.canvas.draw_idle()
        x0, y0 = self.state['start']
        x1, y1 = point
        self.rect.set_xy((min(x0, x1), min(y0, y1)))
        self.rect.set_width(abs(x1 - x0))
        self.rect.set_height(abs(y1 - y0))
        return True

    def release(self, event):
        if self.state is None:
            return
        state = self.state
        self.state = None
        if self.rect is not None:
            try:
                self.rect.remove()
            except Exception:
                pass
            self.rect = None
        self.w.canvas.draw_idle()
        if event.inaxes is not self.w.ax or self._point(event) is None:return True
        state['current']=self._point(event)
        x0, y0 = state['start']
        x1, y1 = state['current']
        if abs(x1 - x0) <= 0. or abs(y1 - y0) <= 0.:
            self.w.statusBar().showMessage('框选太小，没有写入')
            return True
        layers = self._layers()
        if not layers:
            self.w.statusBar().showMessage('当前没有可划分的固定RDL，先勾选“重构预演”')
            return True
        bounds = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        from dataclasses import replace
        regions = list(getattr(self.w.view_settings, 'reconstruction_regions', ()) or ())
        for layer in layers:
            regions.append((layer, bounds, self.mode))
        self.w.view_settings = replace(self.w.view_settings, reconstruction_regions=tuple(regions))
        self.w.dirty=True
        self.w.statusBar().showMessage(
            f'已写入 {len(layers)} 个图层的{REGION_LABELS[self.mode]}；可继续框选，或点“清除全部手动区域”恢复')
        self.w.request_preview(quiet=True)
        return True

    def _layers(self):
        try:
            data = self.w.reconstruction_overlay.data()
        except Exception:
            data = None
        layers = sorted({q['layer'] for q in (data or {}).get('pieces', [])
                         if q['layer'] in self.w.visible_boxes and self.w.visible_boxes[q['layer']].isChecked()})
        if layers:
            return layers
        return []
