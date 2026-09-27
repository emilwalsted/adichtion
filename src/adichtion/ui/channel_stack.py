"""The channel stack: one pyqtgraph plot per channel, x-linked, on the file's time axis.

* one ``ChannelPlot`` per channel in a vertical ``QSplitter`` (drag the handles to
  give a channel more height), a ``MarkerStrip`` above them with the block labels
  and comment flags;
* the trace is a min/max envelope at pixel resolution, or the samples themselves
  when zoomed in far enough; the first picture after a pan/zoom comes from the
  file's block index at once, the decoded one follows from the worker thread;
* click = cursor on every channel (the overlay of each channel shows its value),
  Shift+click = interval from the cursor to the click; scroll = x zoom, drag =
  pan; the wheel over a y-axis (or the small buttons in a channel's corner)
  zooms that channel's y alone.
"""
from __future__ import annotations

from functools import partial
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

from ..file import AdichtFile
from . import lod, theme
from .plot_axis import MinPitchAxis
from .timeline import Timeline
from .workers import JobRunner

RENDER_DEBOUNCE_MS = 60
RENDER_MARGIN = 0.25            # fraction of the visible span decoded on each side, for small pans
MIN_X_RANGE_S = 0.002


def _pen(colour, width: float = 1.0, style=Qt.SolidLine) -> QPen:
    pen = pg.mkPen(colour, width=width, style=style)
    pen.setCosmetic(True)
    return pen


class ChannelViewBox(pg.ViewBox):
    """A ViewBox whose body pans and zooms x only (y belongs to the axis and the corner
    buttons) and which reports plain left clicks."""

    sigClicked = Signal(float, object)          # x in view coordinates, keyboard modifiers

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("enableMenu", False)
        kwargs.setdefault("defaultPadding", 0.0)
        super().__init__(*args, **kwargs)
        self.setMouseMode(self.PanMode)

    def wheelEvent(self, ev, axis=None):
        super().wheelEvent(ev, axis=0 if axis is None else axis)

    def mouseDragEvent(self, ev, axis=None):
        super().mouseDragEvent(ev, axis=0 if axis is None else axis)

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            pos = self.mapSceneToView(ev.scenePos())
            self.sigClicked.emit(float(pos.x()), ev.modifiers())
        else:
            super().mouseClickEvent(ev)


class MarkersItem(pg.GraphicsObject):
    """Vertical lines at the block boundaries (solid) and comments (dotted), painted in one go."""

    def __init__(self, pal: dict):
        super().__init__()
        self._blocks = np.zeros(0)
        self._comments = np.zeros(0)
        self._pen_block = _pen(pal["block_line"], 1.2)
        self._pen_comment = _pen(pal["comment_line"], 1.0, Qt.DotLine)
        self.setZValue(-5)

    def set_positions(self, blocks, comments) -> None:
        self._blocks = np.asarray(blocks, dtype=np.float64)
        self._comments = np.asarray(comments, dtype=np.float64)
        self.update()

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        return None

    def boundingRect(self) -> QRectF:
        return self.viewRect()

    def paint(self, p, *args):
        r = self.viewRect()
        x0, x1 = r.left(), r.right()
        top, bottom = r.top(), r.bottom()
        for xs, pen in ((self._comments, self._pen_comment), (self._blocks, self._pen_block)):
            if not len(xs):
                continue
            vis = xs[(xs >= x0) & (xs <= x1)]
            if len(vis) > 2000:                    # denser than pixels: thin out
                vis = vis[:: int(np.ceil(len(vis) / 2000))]
            p.setPen(pen)
            for x in vis:
                p.drawLine(QPointF(x, top), QPointF(x, bottom))


class CursorItem(pg.GraphicsObject):
    """The cursor line and the selected interval, full height whatever the y-zoom."""

    def __init__(self, pal: dict):
        super().__init__()
        self._cursor: Optional[float] = None
        self._sel: Optional[tuple[float, float]] = None
        self._pen = _pen(pal["cursor"], 1.4)
        self._edge = _pen(pal["selection_edge"], 1.0)
        self._brush = QBrush(QColor(*pal["selection"]))
        self.setZValue(20)

    def set(self, cursor: Optional[float], selection: Optional[tuple[float, float]]) -> None:
        self._cursor, self._sel = cursor, selection
        self.update()

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        return None

    def boundingRect(self) -> QRectF:
        return self.viewRect()

    def paint(self, p, *args):
        r = self.viewRect()
        top, bottom = r.top(), r.bottom()
        if self._sel is not None:
            a, b = self._sel
            p.setPen(Qt.NoPen)
            p.setBrush(self._brush)
            p.drawRect(QRectF(a, top, b - a, bottom - top))
            p.setPen(self._edge)
            p.drawLine(QPointF(a, top), QPointF(a, bottom))
            p.drawLine(QPointF(b, top), QPointF(b, bottom))
        if self._cursor is not None:
            p.setPen(self._pen)
            p.drawLine(QPointF(self._cursor, top), QPointF(self._cursor, bottom))


class ChannelPlot(QWidget):
    """One channel: its plot, the overlay labels and the y-zoom buttons."""

    def __init__(self, index: int, title: str, rate_hz: float, units: str, dtype: str,
                 colour, pal: dict, parent=None):
        super().__init__(parent)
        self.index = index
        self.title = title
        self.rate_hz = rate_hz
        self.units = units
        self.dtype = dtype
        self.is_calculated = dtype == "float32"
        self.definition: Optional[str] = None
        self._detail_cover: Optional[tuple[float, float]] = None
        self._detail_density = 0.0
        self._label_text = ""
        self._calc_text = ""
        self._curve_x = np.zeros(0)
        self._curve_y = np.zeros(0)
        self.level = ""
        self.setMinimumHeight(theme.PLOT_ROW_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.vb = ChannelViewBox()
        self.widget = pg.PlotWidget(viewBox=self.vb, axisItems={"left": MinPitchAxis(orientation="left")})
        self.widget.setFocusPolicy(Qt.NoFocus)
        self.item = self.widget.getPlotItem()
        self.item.hideButtons()
        self.vb.disableAutoRange()               # the stack owns both ranges
        self.item.showGrid(x=True, y=True, alpha=pal["grid_alpha"])
        left = self.item.getAxis("left")
        left.setWidth(theme.PLOT_AXIS_WIDTH)
        self.item.setContentsMargins(0, 2, 8, 0)
        if self.is_calculated:
            self.widget.setBackground(pal["bg_calc"])
        self.curve = pg.PlotCurveItem(pen=_pen(colour, 1.0))
        self.item.addItem(self.curve)
        self.zero = pg.InfiniteLine(pos=0, angle=0, pen=_pen(pal["zero_line"], 1.0, Qt.DashLine), movable=False)
        self.zero.setZValue(-10)
        self.item.addItem(self.zero, ignoreBounds=True)
        self.markers = MarkersItem(pal)
        self.item.addItem(self.markers, ignoreBounds=True)
        self.cursor = CursorItem(pal)
        self.item.addItem(self.cursor, ignoreBounds=True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget)

        # overlays: children of the plot widget, positioned by hand in resizeEvent
        self.label = QLabel(self.widget)
        self.label.setObjectName("plotOverlay")
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        bg = pal["overlay_bg"]
        self.label.setStyleSheet(f"QLabel#plotOverlay {{ background: rgba({bg[0]},{bg[1]},{bg[2]},{bg[3] / 255:.2f}); "
                                 f"border-radius: 4px; padding: 1px 6px; font-weight: 600; }}")
        self.calc_label = QLabel(self.widget)
        self.calc_label.setObjectName("plotOverlayCalc")
        self.calc_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        t = theme.active_theme()
        self.calc_label.setStyleSheet(f"QLabel#plotOverlayCalc {{ background: {t['calc_bg']}; color: {t['calc_fg']}; "
                                      f"border-radius: 4px; padding: 1px 6px; }}")
        self.calc_label.setVisible(self.is_calculated)
        self.buttons = QWidget(self.widget)
        hb = QHBoxLayout(self.buttons)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(2)
        self.btn_y_in = self._small_button("+", "Zoom this channel's y-axis in")
        self.btn_y_out = self._small_button("−", "Zoom this channel's y-axis out")
        self.btn_y_auto = self._small_button("A", "Fit this channel's y-axis to the visible data")
        for b in (self.btn_y_in, self.btn_y_out, self.btn_y_auto):
            hb.addWidget(b)
        self.btn_y_in.clicked.connect(lambda: self.zoom_y(0.8))
        self.btn_y_out.clicked.connect(lambda: self.zoom_y(1.25))
        self.btn_y_auto.clicked.connect(self.auto_y)
        self.set_value(None)
        self.set_definition(None)

    def _small_button(self, text: str, tip: str) -> QToolButton:
        b = QToolButton(self.buttons)
        b.setObjectName("plotBtn")
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFixedSize(24, 22)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    # -- layout of the overlays -----------------------------------------------------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_overlays()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._place_overlays()

    def _place_overlays(self) -> None:
        x = theme.PLOT_AXIS_WIDTH + 8
        limit = max(120, int(self.widget.width() * 0.62))
        for lbl, full in ((self.label, self._label_text), (self.calc_label, self._calc_text)):
            fm = lbl.fontMetrics()
            lbl.setText(fm.elidedText(full, Qt.ElideRight, limit))
            lbl.adjustSize()
        self.label.move(x, 4)
        self.calc_label.move(x, 4 + self.label.height() + 2)
        self.buttons.adjustSize()
        self.buttons.move(max(x, self.widget.width() - self.buttons.width() - 12), 4)
        for w in (self.label, self.calc_label, self.buttons):
            w.raise_()

    # -- text ---------------------------------------------------------------------
    def set_value(self, value: Optional[float]) -> None:
        base = f"Ch {self.index + 1} · {self.title}   {self.rate_hz:g} Hz"
        if self.units:
            base += f"   {self.units}"
        if value is not None and np.isfinite(value):
            base += f"      {value:.5g}" + (f" {self.units}" if self.units else "")
        self._label_text = base
        self._place_overlays()

    def set_definition(self, text: Optional[str]) -> None:
        self.definition = text
        if self.is_calculated:
            self._calc_text = "Calculated channel · " + (text or "inferring the definition…")
            self.calc_label.setToolTip(self._calc_text)
        self._place_overlays()

    # -- data ----------------------------------------------------------------------
    def set_curve(self, x: np.ndarray, y: np.ndarray, level: str, cover: Optional[tuple[float, float]],
                  density: float = 0.0) -> None:
        """``cover`` is the file-time span the decoded curve is valid for (None for a coarse
        picture) and ``density`` its resolution in buckets per second."""
        self._curve_x, self._curve_y, self.level = x, y, level
        self._detail_cover = cover
        self._detail_density = density
        self.curve.setData(x, y, connect="finite", skipFiniteCheck=False)

    def detail_covers(self, t0: float, t1: float, width_px: int) -> bool:
        """Whether the decoded curve already shows ``[t0, t1]`` at (nearly) pixel resolution."""
        c = self._detail_cover
        if c is None or c[0] > t0 or c[1] < t1:
            return False
        if self.level == "samples":
            return True
        return self._detail_density >= 0.9 * width_px / max(t1 - t0, 1e-9)

    def invalidate(self) -> None:
        self._detail_cover = None

    def visible_y_range(self) -> Optional[tuple[float, float]]:
        """Min/max of the drawn curve inside the current x range."""
        if not len(self._curve_x):
            return None
        x0, x1 = self.vb.viewRange()[0]
        m = (self._curve_x >= x0) & (self._curve_x <= x1) & np.isfinite(self._curve_y)
        if not m.any():
            return None
        y = self._curve_y[m]
        return float(y.min()), float(y.max())

    # -- y zoom -----------------------------------------------------------------------
    def set_y_range(self, lo: float, hi: float) -> None:
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return
        if hi <= lo:
            pad = abs(lo) * 0.05 or 1.0
            lo, hi = lo - pad, hi + pad
        else:
            pad = (hi - lo) * 0.06
            lo, hi = lo - pad, hi + pad
        self.vb.setYRange(lo, hi, padding=0)

    def zoom_y(self, factor: float) -> None:
        lo, hi = self.vb.viewRange()[1]
        c = (lo + hi) / 2
        half = (hi - lo) / 2 * factor
        self.vb.setYRange(c - half, c + half, padding=0)

    def auto_y(self) -> None:
        r = self.visible_y_range()
        if r is not None:
            self.set_y_range(*r)


class MarkerStrip(pg.PlotWidget):
    """The thin strip above the channels: block labels and comment flags on the file axis."""

    def __init__(self, pal: dict, parent=None):
        self.vb = ChannelViewBox()
        super().__init__(parent, viewBox=self.vb)
        self.setFixedHeight(28)
        item = self.getPlotItem()
        item.hideButtons()
        item.hideAxis("left")
        item.hideAxis("bottom")
        item.setContentsMargins(theme.PLOT_AXIS_WIDTH, 0, 8, 0)
        self.vb.setMouseEnabled(x=False, y=False)
        self.vb.disableAutoRange()
        self.vb.setYRange(0, 1, padding=0)
        self.setFocusPolicy(Qt.NoFocus)
        self.markers = MarkersItem(pal)
        item.addItem(self.markers, ignoreBounds=True)
        self.cursor = CursorItem(pal)
        item.addItem(self.cursor, ignoreBounds=True)
        self.flags = pg.ScatterPlotItem(size=9, symbol="t", pen=_pen(pal["comment_flag"]),
                                        brush=pg.mkBrush(*pal["comment_flag"]), hoverable=True,
                                        tip=lambda x, y, data: str(data))
        item.addItem(self.flags, ignoreBounds=True)
        self._labels: list[pg.TextItem] = []
        self._pal = pal

    def set_timeline(self, timeline: Optional[Timeline]) -> None:
        for lbl in self._labels:
            self.getPlotItem().removeItem(lbl)
        self._labels = []
        if timeline is None:
            self.markers.set_positions([], [])
            self.flags.setData([])
            return
        self.markers.set_positions(timeline.boundaries, [c.file_time_s for c in timeline.comments])
        self.flags.setData(x=[c.file_time_s for c in timeline.comments],
                           y=[0.35] * len(timeline.comments),
                           data=[f"#{c.comment.id}  {c.comment.text}" for c in timeline.comments])
        for b in timeline.blocks:
            lbl = pg.TextItem(f"Block {b.number}", color=self._pal["fg"], anchor=(0, 0))
            lbl.setPos(b.start_s, 1.0)
            self.getPlotItem().addItem(lbl, ignoreBounds=True)
            self._labels.append(lbl)


class ChannelStack(QWidget):
    cursorChanged = Signal(object)              # file time (float) or None
    selectionChanged = Signal(object)           # (t0, t1) or None
    rangeChanged = Signal(float, float)         # visible file-time range

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self.plots: list[ChannelPlot] = []
        self._visible: list[int] = []
        self._cursor: Optional[float] = None
        self._selection: Optional[tuple[float, float]] = None
        self._render_job = 0
        self._range_source: Optional[ChannelPlot] = None
        self._pal = theme.plot_palette()
        self.runner = JobRunner(self)
        self.runner.progress.connect(self._on_progress)
        self.runner.done.connect(self._on_done)
        self.runner.start()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.strip = MarkerStrip(self._pal)
        self.strip.vb.sigClicked.connect(self._on_clicked)
        self.strip.flags.sigClicked.connect(self._on_flag_clicked)
        lay.addWidget(self.strip)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setObjectName("channelStack")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        # many channels at their minimum height scroll rather than squash
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.splitter)
        lay.addWidget(self.scroll, 1)
        self.empty = QLabel("Open a .adicht file to see its channels.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("status", "muted")
        lay.addWidget(self.empty, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RENDER_DEBOUNCE_MS)
        self._timer.timeout.connect(self._render)

    # -- file ------------------------------------------------------------------------
    def set_file(self, f: Optional[AdichtFile]) -> None:
        self._clear_plots()
        self.file = f
        self.timeline = Timeline(f) if f is not None else None
        self._cursor = None
        self._selection = None
        self.strip.set_timeline(self.timeline)
        self.empty.setVisible(f is None)
        self.strip.setVisible(f is not None)
        self.scroll.setVisible(f is not None)
        if f is None:
            self.cursorChanged.emit(None)
            self.selectionChanged.emit(None)
            return
        rec0 = f.records[0] if f.records else None
        for c in range(f.n_channels):
            rate = rec0.sample_rate_hz(c) if rec0 else 0.0
            units = rec0.value_label(c) if rec0 else ""
            colour = self._pal["cycle"][c % len(self._pal["cycle"])]
            p = ChannelPlot(c, f.channel_titles[c], rate, units, f.index.channel_dtype(c), colour, self._pal, self)
            p.vb.sigClicked.connect(self._on_clicked)
            p.markers.set_positions(self.timeline.boundaries, [m.file_time_s for m in self.timeline.comments])
            r = lod.global_range(f, c)
            if r is not None:
                p.set_y_range(*r)
            self.plots.append(p)
        self.set_visible_channels(list(range(f.n_channels)), equalize=True)
        self.zoom_reset()

    def _clear_plots(self) -> None:
        self._timer.stop()
        if self._range_source is not None:
            self._range_source.vb.sigXRangeChanged.disconnect(self._on_x_range)
            self._range_source = None
        for p in self.plots:
            p.setParent(None)
            p.deleteLater()
        self.plots = []
        self._visible = []

    def close(self) -> None:
        self.runner.stop()

    # -- channels shown --------------------------------------------------------------
    @property
    def visible_channels(self) -> list[int]:
        return list(self._visible)

    def set_visible_channels(self, channels, equalize: bool = True) -> None:
        wanted = [c for c in range(len(self.plots)) if c in set(channels)]
        for p in self.plots:
            if p.parent() is not None and p.index not in wanted:
                p.setParent(None)
        master = None
        for c in wanted:
            p = self.plots[c]
            if p.parent() is not self.splitter:
                self.splitter.addWidget(p)
            p.show()
        self._visible = wanted
        # order in the splitter follows the channel order
        for pos, c in enumerate(wanted):
            self.splitter.insertWidget(pos, self.plots[c])
        # x-link everything to the first shown plot; the bottom axis only on the last
        for i, c in enumerate(wanted):
            p = self.plots[c]
            last = i == len(wanted) - 1
            ax = p.item.getAxis("bottom")
            ax.setStyle(showValues=last)
            if last:
                p.item.setLabel("bottom", "Time (s)")
                ax.setHeight(None)
            else:
                ax.setLabel(None)
                ax.setHeight(6)
            if master is None:
                master = p
                p.vb.setXLink(None)
                if self._range_source is not None and self._range_source is not p:
                    self._range_source.vb.sigXRangeChanged.disconnect(self._on_x_range)
                if self._range_source is not p:
                    p.vb.sigXRangeChanged.connect(self._on_x_range)
                    self._range_source = p
            else:
                p.vb.setXLink(master.vb)
        if master is not None:
            self.strip.vb.setXLink(master.vb)
            self._apply_limits(master)
        if equalize:
            self.equalize()
        self._schedule_render()

    def equalize(self) -> None:
        n = len(self._visible)
        if n:
            h = max(self.scroll.viewport().height(), n * theme.PLOT_ROW_MIN_HEIGHT)
            self.splitter.setSizes([h // n] * n)

    def _master(self) -> Optional[ChannelPlot]:
        return self.plots[self._visible[0]] if self._visible else None

    def _apply_limits(self, master: ChannelPlot) -> None:
        if self.timeline is None:
            return
        master.vb.setLimits(xMin=0.0, xMax=max(self.timeline.duration_s, MIN_X_RANGE_S), minXRange=MIN_X_RANGE_S)

    # -- x range ---------------------------------------------------------------------
    def view_range(self) -> tuple[float, float]:
        m = self._master()
        if m is None:
            return 0.0, 0.0
        x0, x1 = m.vb.viewRange()[0]
        return float(x0), float(x1)

    def set_range(self, t0: float, t1: float) -> None:
        m = self._master()
        if m is None or self.timeline is None:
            return
        d = self.timeline.duration_s
        t0, t1 = max(0.0, t0), min(d, t1)
        if t1 - t0 < MIN_X_RANGE_S:
            t1 = min(d, t0 + MIN_X_RANGE_S)
        m.vb.setXRange(t0, t1, padding=0)

    def zoom_reset(self) -> None:
        if self.timeline is not None:
            self.set_range(0.0, self.timeline.duration_s)

    def zoom_x(self, factor: float) -> None:
        x0, x1 = self.view_range()
        c = self._cursor if self._cursor is not None and x0 <= self._cursor <= x1 else (x0 + x1) / 2
        half_l, half_r = (c - x0) * factor, (x1 - c) * factor
        self.set_range(c - half_l, c + half_r)

    def zoom_in(self) -> None:
        self.zoom_x(0.5)

    def zoom_out(self) -> None:
        self.zoom_x(2.0)

    def auto_zoom(self) -> None:
        """Fit every shown channel's y-axis to the data in the visible x-range."""
        for c in self._visible:
            self.plots[c].auto_y()

    def _on_x_range(self, *args) -> None:
        x0, x1 = self.view_range()
        self.rangeChanged.emit(x0, x1)
        self._schedule_render()

    # -- cursor and selection --------------------------------------------------------------
    @property
    def cursor(self) -> Optional[float]:
        return self._cursor

    @property
    def selection(self) -> Optional[tuple[float, float]]:
        return self._selection

    def _on_clicked(self, x: float, modifiers) -> None:
        if self.timeline is None:
            return
        x = min(max(0.0, x), self.timeline.duration_s)
        if modifiers & Qt.ShiftModifier and self._cursor is not None:
            self.set_selection(self._cursor, x)
        else:
            self.set_cursor(x)

    def _on_flag_clicked(self, scatter, points, *args) -> None:
        if len(points):
            self.set_cursor(float(points[0].pos().x()))

    def set_cursor(self, t: Optional[float]) -> None:
        self._cursor = t
        self._selection = None
        self._paint_cursor()
        self._update_values()
        self.cursorChanged.emit(t)
        self.selectionChanged.emit(None)

    def set_selection(self, t0: float, t1: float) -> None:
        if t1 < t0:
            t0, t1 = t1, t0
        self._selection = (t0, t1)
        self._paint_cursor()
        self.selectionChanged.emit(self._selection)

    def nudge_cursor(self, seconds: float = 0.0, samples: int = 0) -> None:
        """Move the cursor by a time and/or a number of ticks of the block it is in."""
        if self._cursor is None or self.timeline is None:
            return
        pos = self.timeline.locate(self._cursor)
        t = self._cursor + seconds + samples / pos.block.tick_rate_hz
        t = min(max(0.0, t), self.timeline.duration_s)
        x0, x1 = self.view_range()
        self.set_cursor(t)
        if not x0 <= t <= x1:                       # keep the cursor on screen
            w = x1 - x0
            self.set_range(t - w / 2, t + w / 2)

    def clear_selection(self) -> None:
        if self._selection is not None:
            self._selection = None
            self._paint_cursor()
            self.selectionChanged.emit(None)

    def _paint_cursor(self) -> None:
        for p in self.plots:
            p.cursor.set(self._cursor, self._selection)
        self.strip.cursor.set(self._cursor, self._selection)

    def _update_values(self) -> None:
        if self.file is None or self.timeline is None:
            return
        for c in range(len(self.plots)):
            v = lod.value_at(self.file, self.timeline, c, self._cursor) if self._cursor is not None else None
            self.plots[c].set_value(v)

    # -- rendering -------------------------------------------------------------------------
    def _schedule_render(self) -> None:
        if self.file is not None:
            self._timer.start()

    def _render(self) -> None:
        if self.file is None or self.timeline is None or not self._visible:
            return
        x0, x1 = self.view_range()
        m = self._master()
        width = max(200, int(m.vb.width())) if m is not None else 800
        f, tl = self.file, self.timeline
        pending = []
        for c in self._visible:
            p = self.plots[c]
            if p.detail_covers(x0, x1, width):
                continue
            x, y, level = lod.curve_for_range(f, tl, c, x0, x1, width, coarse=True)
            p.set_curve(x, y, level, None)
            pending.append(c)
        if not pending:
            return
        margin = (x1 - x0) * RENDER_MARGIN
        a, b = max(0.0, x0 - margin), min(tl.duration_s, x1 + margin)
        n = int(width * (b - a) / max(x1 - x0, 1e-9))
        self._render_job = self.runner.submit("render", partial(_render_job, f, tl, list(pending), a, b, n))

    def _on_progress(self, job_id: int, kind: str, payload) -> None:
        if kind != "render" or job_id != self._render_job:
            return
        c, a, b, x, y, level, n = payload
        if 0 <= c < len(self.plots):
            self.plots[c].set_curve(x, y, level, (a, b), n / max(b - a, 1e-9))

    def _on_done(self, job_id: int, kind: str, result, error) -> None:
        if error and kind == "render":
            print(error)

    def set_definition(self, channel: int, text: Optional[str]) -> None:
        if 0 <= channel < len(self.plots):
            self.plots[channel].set_definition(text)

    def refresh(self) -> None:
        """Redraw everything (after the units or the file changed under the plots)."""
        for p in self.plots:
            p.invalidate()
        self._schedule_render()


def _render_job(f: AdichtFile, tl: Timeline, channels: list[int], a: float, b: float, n: int, stale, report):
    for c in channels:
        if stale():
            return None
        x, y, level = lod.curve_for_range(f, tl, c, a, b, n, coarse=False, stale=stale)
        if stale():
            return None
        report((c, a, b, x, y, level, n))
    return len(channels)
