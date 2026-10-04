"""The channel stack: one pyqtgraph plot per channel, x-linked, on the file's time axis.

* one ``ChannelPlot`` per channel in a vertical ``QSplitter`` (drag the handles to
  give a channel more height), a ``MarkerStrip`` above them with the block labels
  and comment flags;
* the trace is a min/max envelope at pixel resolution, or the samples themselves
  when zoomed in far enough; the first picture after a pan/zoom comes from the
  file's block index at once, the decoded one follows from the worker thread;
* click = cursor on every channel (the overlay of each channel shows its value),
  Shift+click or a drag = interval; scroll = x zoom, Alt+drag (or the middle button, or
  a sideways swipe) = pan; the wheel over a y-axis (or the small buttons in a channel's
  corner) zooms that channel's y alone.  A right click opens the context menu.
"""
from __future__ import annotations

from functools import partial
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QScrollArea, QScrollBar, QSizePolicy, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

import time

from .. import perf
from ..file import AdichtFile
from . import lod, theme
from .plot_axis import MinPitchAxis, TimedAxis
from .timeline import Timeline
from .workers import JobRunner

RENDER_DEBOUNCE_MS = 60
COARSE_SYNC_BUDGET_MS = 8.0     # the coarse picture is drawn with the move itself only while a pass takes less than this
COARSE_SYNC_MIN_GAP_S = 0.016   # ...and at most once per frame: a burst of queued inputs leaves the rest to the debounced pass
AXIS_MIN_WIDTH = 44             # floor (px) of the shared left-axis width
AXIS_STEP = 4                   # the shared width moves in steps, and only shrinks past AXIS_HYSTERESIS,
AXIS_HYSTERESIS = 12            # so it does not shift the plots on every y-zoom
CLICK_SLOP_PX = 4               # a drag shorter than this is a click, not an interval
RENDER_MARGIN = 0.25            # fraction of the visible span decoded on each side, for small pans
MIN_X_RANGE_S = 0.002


def _pen(colour, width: float = 1.0, style=Qt.SolidLine) -> QPen:
    pen = pg.mkPen(colour, width=width, style=style)
    pen.setCosmetic(True)
    return pen


class _input_span:
    """``ADICHTION_PERF``: one user input that moves the view (wheel zoom, drag, sideways swipe).  Times the whole
    synchronous handling, which includes every range signal it fires, and counts the inputs and the range signals
    they cause (``input.<kind>``, ``input.<kind>.x_range`` in the summary's counts), so the cascade per input shows
    as a ratio.  The repaints come later (the event loop), see ``paint.plot`` against ``input.*``."""
    __slots__ = ("kind", "_span", "_before")

    def __init__(self, kind: str):
        self.kind = kind

    def __enter__(self):
        if perf.enabled():
            self._before = perf.count("signal.x_range.all", 0)
            self._span = perf.quiet_span(f"input.{self.kind}", slow_ms=30.0)
            self._span.__enter__()
        else:
            self._span = None
        return self

    def __exit__(self, *exc) -> None:
        if self._span is not None:
            self._span.__exit__(*exc)
            perf.count(f"input.{self.kind}")
            perf.count(f"input.{self.kind}.x_range", perf.count("signal.x_range.all", 0) - self._before)


class ChannelViewBox(pg.ViewBox):
    """A ViewBox whose body works on the time axis only (y belongs to the axis and the corner
    buttons): a plain left click reports the cursor; a left drag selects an interval and
    Alt+drag pans (``drag_selects`` False swaps the two); the middle button always pans and
    the wheel zooms.  A right click asks for the context menu."""

    sigClicked = Signal(float, object)          # x in view coordinates, keyboard modifiers
    sigSelectDrag = Signal(float, float, bool)  # x where the drag began, x now, whether it is the last event
    sigContextMenu = Signal(object)             # global QPoint

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("enableMenu", False)
        kwargs.setdefault("defaultPadding", 0.0)
        super().__init__(*args, **kwargs)
        self.setMouseMode(self.PanMode)
        self.drag_selects = True

    def linkedViewChanged(self, view, axis):
        with perf.quiet_span("vb.linked"):
            super().linkedViewChanged(view, axis)

    def updateViewRange(self, *args, **kwargs):
        with perf.quiet_span("vb.update_range"):
            super().updateViewRange(*args, **kwargs)
            if self.width() > 0 and self.height() > 0:
                self.updateMatrix()

    def wheelEvent(self, ev, axis=None):
        with _input_span("wheel"):
            super().wheelEvent(ev, axis=0 if axis is None else axis)

    def mouseDragEvent(self, ev, axis=None):
        if ev.isStart():                        # the role is fixed for the whole drag (Alt pressed midway changes nothing)
            self._drag_selecting = self.drag_selects != bool(ev.modifiers() & Qt.AltModifier)
        if ev.button() == Qt.LeftButton and getattr(self, "_drag_selecting", True):
            ev.accept()
            x0 = self.mapSceneToView(ev.buttonDownScenePos(Qt.LeftButton)).x()
            x1 = self.mapSceneToView(ev.scenePos()).x()
            self.sigSelectDrag.emit(float(x0), float(x1), bool(ev.isFinish()))
            return
        with _input_span("drag"):
            super().mouseDragEvent(ev, axis=0 if axis is None else axis)

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            pos = self.mapSceneToView(ev.scenePos())
            self.sigClicked.emit(float(pos.x()), ev.modifiers())
        elif ev.button() == Qt.RightButton:
            ev.accept()
            self.sigContextMenu.emit(ev.screenPos().toPoint())
        else:
            super().mouseClickEvent(ev)

    def pan_fraction(self, fraction: float) -> None:
        """Move the view by ``fraction`` of its width (positive = later in time)."""
        x0, x1 = self.viewRange()[0]
        with _input_span("swipe"):
            self.translateBy(x=fraction * (x1 - x0))

    def pixels_per_second(self) -> float:
        x0, x1 = self.viewRange()[0]
        return float(self.width()) / max(x1 - x0, 1e-9)


class ChannelPlotWidget(pg.PlotWidget):
    """A PlotWidget whose sideways wheel (a trackpad swipe, a tilt wheel) pans the time axis."""

    PAN_PER_NOTCH = 0.1                         # fraction of the view per 120 units of angle delta
    FRAME_BURST_S = 0.5                         # repaints further apart than this are separate bursts, not frames
    SLOW_FRAME_MS = 40.0                        # frames (gaps) at least this long are printed, all are aggregated

    def paintEvent(self, ev):
        if not perf.enabled():
            super().paintEvent(ev)
            return
        t = time.perf_counter()
        super().paintEvent(ev)
        done = time.perf_counter()
        perf.count("paint.plot")
        last = getattr(self, "_perf_last_paint", None)
        self._perf_last_paint = t
        perf.event("frame.paint", (done - t) * 1000.0, quiet=(done - t) * 1000.0 < self.SLOW_FRAME_MS)
        if last is not None and t - last < self.FRAME_BURST_S:       # time between two repaints within one pan/zoom
            gap = (t - last) * 1000.0
            perf.event("frame.gap", gap, quiet=gap < self.SLOW_FRAME_MS)

    def wheelEvent(self, ev):
        d = ev.angleDelta()
        if abs(d.x()) > abs(d.y()):
            vb = self.getPlotItem().getViewBox()
            if hasattr(vb, "pan_fraction"):
                vb.pan_fraction(-d.x() / 120.0 * self.PAN_PER_NOTCH)
                ev.accept()
                return
        super().wheelEvent(ev)


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
        with perf.quiet_span("markers.paint"):
            self._paint(p)

    def _paint(self, p) -> None:
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
        with perf.quiet_span("cursor.paint"):
            self._paint(p)

    def _paint(self, p) -> None:
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
        self.coarse_key: Optional[tuple[float, float, int]] = None   # (x0, x1, buckets) of the coarse picture on screen
        self._label_text = ""
        self._calc_text = ""
        self._curve_x = np.zeros(0)
        self._curve_y = np.zeros(0)
        self.level = ""
        self.axis_width = theme.PLOT_AXIS_WIDTH
        self.setMinimumHeight(theme.PLOT_ROW_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.vb = ChannelViewBox()
        self.widget = ChannelPlotWidget(viewBox=self.vb, axisItems={"left": MinPitchAxis(orientation="left"),
                                                                                 "bottom": TimedAxis(orientation="bottom")})
        self.widget.setFocusPolicy(Qt.NoFocus)
        self.item = self.widget.getPlotItem()
        self.item.hideButtons()
        self.vb.disableAutoRange()               # the stack owns both ranges
        self.item.showGrid(x=True, y=True, alpha=pal["grid_alpha"])
        left = self.item.getAxis("left")
        left.setWidth(self.axis_width)
        left.enableAutoSIPrefix(False)            # the tick values stay in the channel's own units
        if self.units:
            # the unit as the axis label, rotated, left of the tick values (text, not
            # pyqtgraph's ``units=``, which would rescale the ticks with an SI prefix)
            left.setLabel(text=self.units, **{"font-size": "9pt"})
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
        self.vb.sigXRangeChanged.connect(self._count_x_range)
        self.set_value(None)
        self.set_definition(None)

    @staticmethod
    def _count_x_range(*args) -> None:
        perf.count("signal.x_range.all")           # every visible plot's own range signal (the master's and the linked ones)

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

    def set_axis_width(self, width: int) -> None:
        """The stack gives every channel the same left-axis width, so the plots' x-axes line up."""
        if width == self.axis_width:
            return
        self.axis_width = width
        self.item.getAxis("left").setWidth(width)
        self._place_overlays()

    def needed_axis_width(self) -> int:
        """Width the left axis needs for its current tick labels (and the unit label if shown)."""
        axis = self.item.getAxis("left")
        return axis.needed_width() if isinstance(axis, MinPitchAxis) else self.axis_width

    def _place_overlays(self) -> None:
        x = self.axis_width + 8
        limit = max(120, int(self.widget.width() * 0.62))
        if self.units:                                # a rotated unit needs the row's height; hide it when squeezed
            left = self.item.getAxis("left")
            needed = QFontMetrics(left.font()).horizontalAdvance(self.units) + 20
            left.showLabel(self.widget.height() >= needed)
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
        if cover is not None:
            self.coarse_key = None
        with perf.quiet_span("curve.set_data"):
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
        self.coarse_key = None

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
        with perf.quiet_span("plot.auto_y"):
            r = self.visible_y_range()
            if r is not None:
                self.set_y_range(*r)


class MarkerStrip(pg.PlotWidget):
    """The thin strip above the channels: block labels and comment flags on the file axis."""

    def __init__(self, pal: dict, parent=None):
        self.vb = ChannelViewBox()
        super().__init__(parent, viewBox=self.vb)
        self.setFixedHeight(38)                   # two rows: block labels above, comment flags below
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
        self._labels: dict[int, pg.TextItem] = {}     # block index -> label, made when it is first shown
        self._shown: set[int] = set()                 # the blocks whose label is visible
        self._label_w: dict[int, float] = {}          # digits in the number -> width of a label plus its gap
        self._comment_labels: dict[int, pg.TextItem] = {}
        self._timeline: Optional[Timeline] = None
        self._pal = pal
        self.vb.sigXRangeChanged.connect(self._relayout)

    def set_axis_width(self, width: int) -> None:
        self.getPlotItem().setContentsMargins(width, 0, 8, 0)

    def set_timeline(self, timeline: Optional[Timeline]) -> None:
        item = self.getPlotItem()
        for lbl in list(self._labels.values()) + list(self._comment_labels.values()):
            item.removeItem(lbl)
        self._labels = {}
        self._shown = set()
        self._comment_labels = {}
        self._timeline = timeline
        if timeline is None:
            self.markers.set_positions([], [])
            self.flags.setData([])
            return
        self.markers.set_positions(timeline.boundaries, [c.file_time_s for c in timeline.comments])
        self.flags.setData(x=[c.file_time_s for c in timeline.comments],
                           y=[0.22] * len(timeline.comments),
                           data=[f"#{c.comment.id}  {c.comment.text}" for c in timeline.comments])
        self._relayout()                # the block labels are made as they come into view, a few dozen at most

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()

    def _block_label(self, i: int) -> pg.TextItem:
        lbl = self._labels.get(i)
        if lbl is None:
            b = self._timeline.blocks[i]
            lbl = pg.TextItem(f"Block {b.number}", color=self._pal["fg"], anchor=(0, 0))
            lbl.setPos(b.start_s, 1.0)
            self.getPlotItem().addItem(lbl, ignoreBounds=True)
            self._labels[i] = lbl
        return lbl

    def _label_width(self, i: int) -> float:
        """Width of block ``i``'s label plus a gap, measured once per number of digits."""
        digits = len(str(self._timeline.blocks[i].number))
        w = self._label_w.get(digits)
        if w is None:
            w = self._label_w[digits] = self._block_label(i).boundingRect().width() + 6
        return w

    def _relayout(self, *args) -> None:
        with perf.quiet_span("strip.relayout"):
            self._relayout_labels()

    def _relayout_labels(self) -> None:
        """Hide block labels that would overlap their neighbour; name the comments when few are in view."""
        tl = self.__dict__.get("_timeline")          # resizeEvent fires inside PlotWidget.__init__
        if tl is None or not tl.blocks:
            return
        x0, x1 = self.vb.viewRange()[0]
        width = max(1.0, float(self.vb.width()))
        px_per_s = width / max(x1 - x0, 1e-9)
        # the labels that fit side by side, first come first served: walk the visible blocks, and after
        # a shown label jump straight to the first block that starts clear of it
        starts = tl.starts
        i = int(np.searchsorted(starts, x0 - 1 / px_per_s, side="left"))
        end = int(np.searchsorted(starts, x1, side="right"))
        shown: set[int] = set()
        last_right = -1e9
        while i < end:
            px = (starts[i] - x0) * px_per_s
            if px >= last_right:
                shown.add(i)
                last_right = px + self._label_width(i)
                i += 1
            else:
                i = max(i + 1, int(np.searchsorted(starts, x0 + last_right / px_per_s, side="left")))
        for j in self._shown - shown:
            self._labels[j].setVisible(False)
        for j in shown:
            self._block_label(j).setVisible(True)
        self._shown = shown
        visible = [i for i, c in enumerate(tl.comments) if x0 <= c.file_time_s <= x1]
        show_text = len(visible) <= 40
        for i, lbl in self._comment_labels.items():
            lbl.setVisible(show_text and i in visible)
        if show_text:
            last_right = -1e9
            for i in visible:
                c = tl.comments[i]
                lbl = self._comment_labels.get(i)
                if lbl is None:
                    lbl = pg.TextItem(f"#{c.comment.id} {c.comment.text}", color=self._pal["comment_flag"], anchor=(0, 0.5))
                    lbl.setPos(c.file_time_s, 0.25)
                    self.getPlotItem().addItem(lbl, ignoreBounds=True)
                    self._comment_labels[i] = lbl
                px = (c.file_time_s - x0) * px_per_s + 8
                if px < last_right:
                    lbl.setVisible(False)
                    continue
                lbl.setVisible(True)
                last_right = px + lbl.boundingRect().width() + 4


class ChannelStack(QWidget):
    cursorChanged = Signal(object)              # file time (float) or None
    selectionChanged = Signal(object)           # (t0, t1) or None
    rangeChanged = Signal(float, float)         # visible file-time range
    contextRequested = Signal(object)           # global QPoint of a right click on a plot
    autoZoomChanged = Signal(bool)              # the "fit y after every move" toggle

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self.plots: list[ChannelPlot] = []
        self._y_unset: set[int] = set()        # channels whose y-range waits for their first decoded curve
        self._visible: list[int] = []
        self._cursor: Optional[float] = None
        self._selection: Optional[tuple[float, float]] = None
        self._render_job = 0
        self._range_source: Optional[ChannelPlot] = None
        self._auto_zoom = False
        self._drag_selects = True
        self._axis_fresh = True                 # the next measured axis width is taken as it is (no hysteresis)
        self._coarse_end = 0.0                  # when the last coarse pass finished (perf_counter)
        self._coarse_ms = 0.0                   # smoothed duration of a coarse pass (decides whether one runs with the move)
        self._axis_width = theme.PLOT_AXIS_WIDTH
        self._drag_anchor: Optional[float] = None
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
        self.strip.vb.sigSelectDrag.connect(self._on_select_drag)
        self.strip.vb.sigContextMenu.connect(self.contextRequested)
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
        # a horizontal scroll bar for the time axis: the same move as Alt+drag, for the mouse
        # and the trackpad users who reach for a bar (enabled only while the view is zoomed)
        self.hbar = QScrollBar(Qt.Horizontal)
        self.hbar.setObjectName("timeScroll")
        self.hbar.setEnabled(False)
        self._hbar_scale = 1.0                  # scroll-bar steps per second of file time
        self._hbar_busy = False                 # True while the bar is being set from the view
        self.hbar.valueChanged.connect(self._on_hbar)
        lay.addWidget(self.hbar)
        self.empty = QLabel("Open a LabChart file (.adicht, or a LabChart for Mac document) to see its channels.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("status", "muted")
        lay.addWidget(self.empty, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RENDER_DEBOUNCE_MS)
        self._timer.timeout.connect(self._render)
        self._axis_timer = QTimer(self)
        self._axis_timer.setSingleShot(True)
        self._axis_timer.setInterval(30)
        self._axis_timer.timeout.connect(self._update_axis_width)

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
        self.hbar.setVisible(f is not None)
        if f is None:
            self._sync_hbar(0.0, 0.0)
            self.cursorChanged.emit(None)
            self.selectionChanged.emit(None)
            return
        rec0 = f.records[0] if f.records else None
        for c in range(f.n_channels):
            rate = rec0.sample_rate_hz(c) if rec0 else 0.0
            units = rec0.value_label(c) if rec0 else ""
            colour = self._pal["cycle"][c % len(self._pal["cycle"])]
            p = ChannelPlot(c, f.channel_titles[c], rate, units, f.index.channel_dtype(c), colour, self._pal, self)
            p.vb.drag_selects = self._drag_selects
            p.vb.sigClicked.connect(self._on_clicked)
            p.vb.sigSelectDrag.connect(self._on_select_drag)
            p.vb.sigContextMenu.connect(self.contextRequested)
            p.vb.sigYRangeChanged.connect(self._schedule_axis_width)
            p.set_axis_width(self._axis_width)
            p.markers.set_positions(self.timeline.boundaries, [m.file_time_s for m in self.timeline.comments])
            with perf.span("stack.global_range", channel=c):
                r = lod.global_range(f, c)
            if r is not None:
                p.set_y_range(*r)
            elif f.has_data(c):
                self._y_unset.add(c)            # no stored min/max: the first decoded curve sets the range
            self.plots.append(p)
        self._axis_fresh = True
        self.set_visible_channels([c for c in range(f.n_channels) if f.has_data(c)], equalize=True)
        self.zoom_reset()
        self._sync_hbar(*self.view_range())     # a file whose reset fires no range change (1 s long) still resets the bar

    def _clear_plots(self) -> None:
        self._timer.stop()
        self._axis_timer.stop()
        self._coarse_ms = 0.0
        if self._range_source is not None:
            self._range_source.vb.sigXRangeChanged.disconnect(self._on_x_range)
            self._range_source = None
        for p in self.plots:
            p.setParent(None)
            p.deleteLater()
        self.plots = []
        self._visible = []
        self._y_unset = set()

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
        self._axis_fresh = True
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
            self._apply_limits()
        if equalize:
            self.equalize()
        self._schedule_axis_width()
        self._schedule_render()

    def equalize(self) -> None:
        n = len(self._visible)
        if n:
            h = max(self.scroll.viewport().height(), n * theme.PLOT_ROW_MIN_HEIGHT)
            self.splitter.setSizes([h // n] * n)

    def _master(self) -> Optional[ChannelPlot]:
        return self.plots[self._visible[0]] if self._visible else None

    def _apply_limits(self) -> None:
        """The same x limits on EVERY view (the shown plots, the hidden ones a later channel choice
        brings back, and the marker strip): the file is the widest view, never a hair before its
        start or past its end, whichever plot the mouse is over.  Limits on the master alone let a
        drag on another plot leave that plot's range out of step with the rest."""
        if self.timeline is None:
            return
        d = max(self.timeline.duration_s, MIN_X_RANGE_S)
        for vb in [p.vb for p in self.plots] + [self.strip.vb]:
            vb.setLimits(xMin=0.0, xMax=d, minXRange=MIN_X_RANGE_S, maxXRange=d)

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
        self._sync_ranges()

    def _sync_ranges(self) -> None:
        """Make every view show exactly the master's x range (a safety net: the views are linked,
        but a limit hit on a linked view can leave it a pixel or a page off the others)."""
        m = self._master()
        if m is None:
            return
        want = m.vb.viewRange()[0]
        for c in self._visible:
            vb = self.plots[c].vb
            if vb is not m.vb and max(abs(a - b) for a, b in zip(vb.viewRange()[0], want)) > 1e-9:
                vb.setXRange(*want, padding=0)

    def zoom_reset(self) -> None:
        if self.timeline is not None:
            self.set_range(0.0, self.timeline.duration_s)

    def center_on(self, t: float) -> None:
        """Scroll the view (its zoom unchanged) so ``t`` is in the middle; near the start or the
        end of the file the view stops at the edge instead, ``t`` still on screen."""
        if self.timeline is None or self._master() is None:
            return
        x0, x1 = self.view_range()
        w = x1 - x0
        d = self.timeline.duration_s
        a = min(max(0.0, t - w / 2), max(0.0, d - w))
        self.set_range(a, a + w)

    def scroll_view(self, fraction: float) -> None:
        """Move the view by ``fraction`` of its width (negative = earlier), at most to the file's edges."""
        if self.timeline is None or self._master() is None:
            return
        x0, x1 = self.view_range()
        w = x1 - x0
        a = min(max(0.0, x0 + fraction * w), max(0.0, self.timeline.duration_s - w))
        self.set_range(a, a + w)

    def zoom_x(self, factor: float) -> None:
        x0, x1 = self.view_range()
        c = self._cursor if self._cursor is not None and x0 <= self._cursor <= x1 else (x0 + x1) / 2
        half_l, half_r = (c - x0) * factor, (x1 - c) * factor
        self.set_range(c - half_l, c + half_r)

    def zoom_in(self) -> None:
        self.zoom_x(0.5)

    def zoom_out(self) -> None:
        self.zoom_x(2.0)

    def zoom_to_fit(self) -> None:
        """Fit every shown channel's y-axis to the data in the visible x-range (once)."""
        for c in self._visible:
            self.plots[c].auto_y()

    @property
    def drag_selects(self) -> bool:
        """What a plain left drag does: select an interval (True) or pan (False); Alt+drag does the other."""
        return self._drag_selects

    def set_drag_selects(self, on: bool) -> None:
        self._drag_selects = bool(on)
        for vb in [p.vb for p in self.plots] + [self.strip.vb]:
            vb.drag_selects = self._drag_selects

    @property
    def auto_zoom(self) -> bool:
        """Whether every finished move (zoom, scroll, a new file range) fits the y-axes again."""
        return self._auto_zoom

    def set_auto_zoom(self, on: bool) -> None:
        on = bool(on)
        if on == self._auto_zoom:
            return
        self._auto_zoom = on
        if on:
            self.zoom_to_fit()
        self.autoZoomChanged.emit(on)

    def _on_x_range(self, *args) -> None:
        perf.count("signal.x_range")                   # the master's signal: one per move (the linked views follow it)
        with perf.quiet_span("stack.x_range"):
            x0, x1 = self.view_range()
            with perf.quiet_span("stack.sync_hbar"):
                self._sync_hbar(x0, x1)
            with perf.quiet_span("stack.range_changed"):         # the slots of every listener, e.g. the main window's
                self.rangeChanged.emit(x0, x1)
            if (self.file is not None and self._coarse_ms <= COARSE_SYNC_BUDGET_MS
                    and time.perf_counter() - self._coarse_end >= COARSE_SYNC_MIN_GAP_S):
                self._coarse_pass()             # in the same event-loop turn as the move: one repaint per plot, not two
            self._schedule_render()

    #: Most steps the time scroll bar is given (QScrollBar is int32; the scale keeps hours-long files inside it).
    HBAR_STEPS = 1_000_000

    def _sync_hbar(self, x0: float, x1: float) -> None:
        """Make the scroll bar show the view: handle = the visible span, position = its start.
        Disabled while the whole file is in view."""
        d = self.timeline.duration_s if self.timeline is not None else 0.0
        span = x1 - x0
        bar = self.hbar
        self._hbar_busy = True
        try:
            if d <= 0 or span >= d - 1e-9:
                bar.setEnabled(False)
                bar.setRange(0, 0)
                bar.setPageStep(self.HBAR_STEPS)
                return
            self._hbar_scale = self.HBAR_STEPS / d
            k = self._hbar_scale
            page = max(1, int(round(span * k)))
            top = max(0, int(round(d * k)) - page)
            bar.setEnabled(top > 0)
            bar.setRange(0, top)
            bar.setPageStep(page)
            bar.setSingleStep(max(1, page // 10))
            bar.setValue(int(round(x0 * k)))
        finally:
            self._hbar_busy = False

    def _on_hbar(self, value: int) -> None:
        if self._hbar_busy or self.timeline is None or self._master() is None:
            return
        x0, x1 = self.view_range()
        t0 = value / self._hbar_scale
        self.set_range(t0, t0 + (x1 - x0))

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

    def _on_select_drag(self, x0: float, x1: float, finished: bool) -> None:
        """A left drag over a plot: the cursor goes where it began, the interval follows the mouse.
        A drag under a few pixels is a click (the cursor alone)."""
        if self.timeline is None:
            return
        d = self.timeline.duration_s
        a, b = min(max(0.0, x0), d), min(max(0.0, x1), d)
        m = self._master()
        slop = CLICK_SLOP_PX / m.vb.pixels_per_second() if m is not None else 0.0
        if self._drag_anchor is None or abs(a - self._drag_anchor) > 1e-9 and abs(x0 - self._drag_anchor) > 1e-9:
            self._drag_anchor = a                # a new drag (also after one that never got its release)
            self.set_cursor(a)
        if abs(b - a) <= slop and self._selection is None:
            if finished:
                self._drag_anchor = None
            return
        if abs(b - a) <= slop and finished:
            self.clear_selection()
        else:
            self.set_selection(a, b)
        if finished:
            self._drag_anchor = None

    def _on_flag_clicked(self, scatter, points, *args) -> None:
        if len(points):
            self.set_cursor(float(points[0].pos().x()))

    def set_cursor(self, t: Optional[float], follow: bool = False, keep_selection: bool = False,
                   center: bool = False) -> None:
        """Place the cursor at file time ``t`` (clears any interval unless ``keep_selection``, which
        interval playback uses).  With ``follow`` the view pages along so the cursor stays on
        screen, as playback needs; with ``center`` the view scrolls so the cursor is in the middle
        (a comment or a block picked from a list)."""
        had_selection = self._selection is not None and not keep_selection
        self._cursor = t
        if not keep_selection:
            self._selection = None
        if t is not None and center:
            self.center_on(t)
        elif follow and t is not None:
            x0, x1 = self.view_range()
            if not x0 <= t <= x1:
                w = x1 - x0
                self.set_range(t - 0.1 * w, t + 0.9 * w)
        self._paint_cursor()
        self._update_values()
        self.cursorChanged.emit(t)
        if had_selection:
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
        self.set_cursor(t, follow=True)

    def clear_selection(self) -> None:
        if self._selection is not None:
            self._selection = None
            self._paint_cursor()
            self.selectionChanged.emit(None)

    def _paint_cursor(self) -> None:
        with perf.quiet_span("cursor.set"):
            for p in self.plots:
                p.cursor.set(self._cursor, self._selection)
            self.strip.cursor.set(self._cursor, self._selection)

    def _update_values(self) -> None:
        if self.file is None or self.timeline is None:
            return
        with perf.quiet_span("cursor.values"):
            for c in range(len(self.plots)):
                v = lod.value_at(self.file, self.timeline, c, self._cursor) if self._cursor is not None else None
                self.plots[c].set_value(v)

    # -- rendering -------------------------------------------------------------------------
    def _schedule_render(self) -> None:
        if self.file is not None:
            self._timer.start()

    def _render(self) -> None:
        with perf.quiet_span("stack.render"):               # the debounced pass: the coarse curves, the auto-zoom, the job submit
            self._render_now()

    def _coarse_pass(self) -> list[int]:
        """Draw the block-index envelope for every shown channel whose decoded curve does not cover the view, and
        return those channels (their decoded curve is still to come).  A channel already showing the coarse picture of
        this very view is left alone, so the picture is set once and the plot repaints once, not again when the
        debounced pass finds the same view."""
        x0, x1 = self.view_range()
        m = self._master()
        width = max(200, int(m.vb.width())) if m is not None else 800
        f, tl = self.file, self.timeline
        key = (x0, x1, width)
        pending = []
        drawn = 0
        started = time.perf_counter()
        with perf.span("render.coarse", buckets=width) as sp:
            for c in self._visible:
                p = self.plots[c]
                if p.detail_covers(x0, x1, width):
                    continue
                pending.append(c)
                if p.coarse_key == key:
                    continue
                x, y, level = lod.curve_for_range(f, tl, c, x0, x1, width, coarse=True)
                p.set_curve(x, y, level, None)
                p.coarse_key = key
                drawn += 1
            sp.set(channels=len(pending), span_s=float(x1 - x0))
        if drawn:                               # a pass that drew nothing says nothing about the cost
            took = (time.perf_counter() - started) * 1000.0
            self._coarse_ms = took if not self._coarse_ms else 0.5 * (self._coarse_ms + took)   # one slow pass is no verdict
            self._coarse_end = time.perf_counter()
        return pending

    def _render_now(self) -> None:
        if self.file is None or self.timeline is None or not self._visible:
            return
        x0, x1 = self.view_range()
        m = self._master()
        width = max(200, int(m.vb.width())) if m is not None else 800
        f, tl = self.file, self.timeline
        pending = self._coarse_pass()
        if self._auto_zoom:
            self.zoom_to_fit()                  # the move is over (the render is debounced): fit what is drawn now
        if not pending:
            return
        margin = (x1 - x0) * RENDER_MARGIN
        a, b = max(0.0, x0 - margin), min(tl.duration_s, x1 + margin)
        n = int(width * (b - a) / max(x1 - x0, 1e-9))
        self._render_job = self.runner.submit("render", partial(_render_job, f, tl, list(pending), a, b, n))

    def _on_progress(self, job_id: int, kind: str, payload) -> None:
        if kind != "render" or job_id != self._render_job:
            perf.count("render.result_stale")
            return
        c, a, b, x, y, level, n = payload
        x0, x1 = self.view_range()
        if a > x0 + 1e-9 or b < x1 - 1e-9:        # the view moved on since the job began: its curve would replace
            perf.count("render.result_stale")     # the fresh coarse picture with one that misses the view's edge
            return
        if 0 <= c < len(self.plots):
            with perf.quiet_span("stack.on_progress"):    # a decoded curve arriving from the worker, on the GUI thread
                self.plots[c].set_curve(x, y, level, (a, b), n / max(b - a, 1e-9))
                if (self._auto_zoom and c in self._visible) or c in self._y_unset:
                    self._y_unset.discard(c)
                    self.plots[c].auto_y()          # the decoded curve replaces the coarse one: fit to that

    def _on_done(self, job_id: int, kind: str, result, error) -> None:
        if error and kind == "render":
            print(error)

    # -- shared left-axis width ---------------------------------------------------------
    def _schedule_axis_width(self, *args) -> None:
        if self.plots:
            self._axis_timer.start()

    def _update_axis_width(self) -> None:
        with perf.quiet_span("stack.axis_width"):
            self._update_axis_width_now()

    def _update_axis_width_now(self) -> None:
        """Every channel gets the width the widest tick labels among them need (no loose gap
        between the unit and the trace), so the plots keep lining up."""
        if not self._visible:
            return
        need = max(self.plots[c].needed_axis_width() for c in self._visible)
        if need <= 0:
            return
        w = max(AXIS_MIN_WIDTH, -(-need // AXIS_STEP) * AXIS_STEP)
        if w < self._axis_width and self._axis_width - w < AXIS_HYSTERESIS and not self._axis_fresh:
            return
        self._axis_fresh = False
        if w != self._axis_width:
            self.set_axis_width(w)

    @property
    def axis_width(self) -> int:
        return self._axis_width

    def set_axis_width(self, w: int) -> None:
        self._axis_width = int(w)
        for p in self.plots:
            p.set_axis_width(self._axis_width)
        self.strip.set_axis_width(self._axis_width)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._schedule_axis_width()

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
        with perf.span("render.detail", channel=c, buckets=int(n), span_s=float(b - a)) as sp:
            # the counters are the file's, so the delta also holds anything the GUI thread decoded meanwhile
            h0, m0 = f.cache_stats() if perf.enabled() else (0, 0)
            x, y, level = lod.curve_for_range(f, tl, c, a, b, n, coarse=False, stale=stale)
            if perf.enabled():
                h1, m1 = f.cache_stats()
                sp.set(decoded_blocks=m1 - m0, cache_hits=h1 - h0, level=level, points=len(x))
        if stale():
            return None
        report((c, a, b, x, y, level, n))
    return len(channels)
