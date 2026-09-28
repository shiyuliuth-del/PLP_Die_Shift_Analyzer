"""One grouped range chart for X/Y/angle, shared by Qt and the research figure.

All bar lengths use (value - input mean) / input half-width.  Actual certified
endpoints retain their physical units on the right, so degrees never share a
micrometre axis.  Uncertified rows never receive a coloured safety interval.
"""
from __future__ import annotations

import math
import numpy as np

COLORS = ('#659bb6', '#79a68f', '#b08eac')
INK = '#344e62'


def range_rows(ranges):
    """Prepare auditable drawing values without rounding a certified endpoint out."""
    items = []
    for row in ranges.get('rows', []):
        low = np.asarray(row['input_min'], float).copy()
        high = np.asarray(row['input_max'], float).copy()
        low[2] = math.degrees(low[2]); high[2] = math.degrees(high[2])
        mean = (low + high) / 2; half = (high - low) / 2
        certified = bool(row.get('certified') and row.get('minimum_display') is not None)
        for axis in range(3):
            a = row['minimum_display'][axis] if certified else None
            b = row['maximum_display'][axis] if certified else None
            normalized = ([(a - mean[axis]) / half[axis] * 100,
                           (b - mean[axis]) / half[axis] * 100]
                          if certified and half[axis] > 0 else [0., 0.])
            digits = 6 if axis < 2 else 8
            items.append(dict(die=row['die'], axis=axis, certified=certified,
                              normalized=normalized, input=[low[axis], high[axis]],
                              fixed=half[axis] == 0, minimum=a, maximum=b,
                              label=f'[{a:.{digits}f}, {b:.{digits}f}]' if certified else '—',
                              status=row.get('status', ''), volume=row.get('volume_fraction')))
    return items


def draw_safe_ranges(figure, ranges):
    """Draw one axes, return each plotted interval for UI inspection/export."""
    figure.clear(); figure.set_facecolor('white')
    items = range_rows(ranges)
    ax = figure.add_subplot(111)
    figure.subplots_adjust(left=.17, right=.67, bottom=.16, top=.88)
    if not items:
        ax.axis('off'); ax.text(.5, .5, '运行方案评估后显示连续安全范围',
                               transform=ax.transAxes, ha='center', va='center', color=INK)
        return items
    count = len(items) // 3
    for group in range(count):
        start = group * 3.65
        ax.axhspan(start - .48, start + 2.48, color='#f5f8fa', lw=0, zorder=0)
        row = items[group * 3]
        ax.text(-.18, start + .8, f"DIE {row['die']}", transform=ax.get_yaxis_transform(),
                ha='right', va='center', fontsize=10, fontweight='bold', color=INK)
        volume = f"体积 {row['volume']:.2%}" if row['certified'] and row['volume'] is not None else '未认证'
        ax.text(-.18, start + 1.48, volume, transform=ax.get_yaxis_transform(),
                ha='right', va='center', fontsize=7.5, color='#728593')
        if not row['certified']:
            status=row['status'].replace('；','\n')
            status='\n'.join(part[i:i+19] for part in status.splitlines() for i in range(0,len(part),19))
            ax.text(1.055, start + 1, status, transform=ax.get_yaxis_transform(),
                    color='#a06d48', fontsize=8, va='center')
    for i, item in enumerate(items):
        axis = item['axis']; y = (i // 3) * 3.65 + axis
        color = COLORS[axis]
        ax.text(-.035, y, ('X / μm', 'Y / μm', 'O / °')[axis], ha='right', va='center',
                transform=ax.get_yaxis_transform(), color=color, fontsize=9)
        if item['fixed']:
            ax.plot([0], [y], marker='|', color='#c9d3dc', ms=12, mew=2)
        else:
            ax.plot([-100, 100], [y, y], color='#e1e7ec', lw=8, solid_capstyle='round', zorder=1)
        if not item['certified']:continue
        a, b = item['normalized']
        ax.plot([a, b], [y, y], color=color, lw=7, solid_capstyle='round', zorder=3)
        ax.plot([a, b], [y, y], linestyle='none', marker='|', color=INK, ms=11, mew=.9, zorder=4)
        ax.text(1.055, y, item['label'], transform=ax.get_yaxis_transform(),
                color=INK, fontsize=8.5, va='center')
    ax.axvline(0, ls=(0, (3, 3)), color='#bac7d0', lw=.8, zorder=2)
    ax.set(xlim=(-104, 104), ylim=((count - 1) * 3.65 + 2.7, -.65), yticks=[],
           xticks=[-100, -50, 0, 50, 100], xticklabels=['−100%', '−50%', '0', '+50%', '+100%'])
    ax.set_xlabel('相对输入均值的位置 / 各轴单边幅度', fontsize=9, color=INK, labelpad=7)
    ax.tick_params(axis='x', labelsize=8, colors='#6c818f', length=3)
    ax.spines[['top', 'right', 'left']].set_visible(False); ax.spines['bottom'].set_color('#d6dfe5')
    figure.text(.025, .96, '各 DIE 连续安全内范围', va='top', fontsize=12, fontweight='bold', color=INK)
    figure.text(.42, .96, '浅灰：输入范围    彩色：已证安全范围', va='top', fontsize=9, color='#728593')
    figure.text(.697, .90, '安全端点  [最小值, 最大值]', va='bottom', fontsize=9, color=INK)
    return items
