"""Apply a window-local scale to Qt's logical pixels, on top of monitor DPI."""
import re

from .qt_compat import QtCore, QtWidgets as W


def pixels(value, percent):
    return max(1, round(value * percent / 100)) if value else 0


def stylesheet(source, percent):
    return re.sub(r'(\d+)px\b', lambda match: f'{pixels(int(match[1]), percent)}px', source)


class InterfaceScale:
    def __init__(self, window, constrained):
        self.window = window
        self.percent = 100
        self.sheet = window.styleSheet()
        widgets = window.findChildren(W.QWidget)
        self.styles = [(widget, widget.styleSheet()) for widget in widgets
                       if widget is not window.width_yield.label and 'px' in widget.styleSheet()]
        self.bounds = [(w, w.minimumSize(), w.maximumSize()) for w in constrained]
        self.rows = [(t.verticalHeader(), t.verticalHeader().minimumSectionSize(),
                      t.verticalHeader().defaultSectionSize()) for t in window.findChildren(W.QTableWidget)]

    def apply(self, percent):
        if percent == self.percent:
            return
        self.percent = percent
        window = self.window

        def px(value):
            return pixels(value, percent)

        sheet = stylesheet(self.sheet, percent)
        if percent != 100:
            # These native affordances use style metrics, independently of font size.
            style = W.QApplication.style()
            metrics = getattr(W.QStyle, 'PixelMetric', W.QStyle)
            mark_w = px(style.pixelMetric(metrics.PM_IndicatorWidth))
            mark_h = px(style.pixelMetric(metrics.PM_IndicatorHeight))
            scroll = px(style.pixelMetric(metrics.PM_ScrollBarExtent))
            sheet += (f'\nQCheckBox::indicator {{ width: {mark_w}px; height: {mark_h}px; }}'
                      f'\nQScrollBar:vertical {{ width: {scroll}px; }}'
                      f'\nQScrollBar:horizontal {{ height: {scroll}px; }}')

        updates_enabled = window.updatesEnabled()
        window.setUpdatesEnabled(False)
        try:
            window.setStyleSheet(sheet)
            for widget, original in self.styles:
                widget.setStyleSheet(stylesheet(original, percent))
            for widget, low, high in self.bounds:
                widget.setMinimumSize(QtCore.QSize(px(low.width()), px(low.height())))
                width = high.width() if high.width() == 16777215 else px(high.width())
                height = high.height() if high.height() == 16777215 else px(high.height())
                widget.setMaximumSize(QtCore.QSize(width, height))
            for header, minimum, default in self.rows:
                header.setMinimumSectionSize(px(minimum))
                header.setDefaultSectionSize(px(default))
            window.scale_table_columns()
            window.plot_header.scale_interface(px)
            window.manual_preview.table.resizeRowsToContents()
            window.per_die_table.resizeRowsToContents()
            window.scheme_table.resizeRowsToContents()
            if hasattr(getattr(window, 'mouse_die', None), 'text'):
                window.mouse_die.update_text()
        finally:
            window.setUpdatesEnabled(updates_enabled)
