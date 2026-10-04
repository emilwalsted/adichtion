"""GUI-thread stall monitor for ``ADICHTION_PERF`` (see :mod:`adichtion.perf`).

A 5 ms ``QTimer`` that measures the gap between its own ticks: whenever the GUI thread is
busy (decoding on it, a slow paint, a layout pass) the next tick comes late, and the gap
is the stall the user feels as a freeze.  Gaps of ``REPORT_MS`` or more are printed as
``gui.gap`` lines, every gap is aggregated (also ``gui.gap``: the maximum is the longest
stall of the run).  Created only when timing is on, so a normal run has no extra timer.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer

from .. import perf

INTERVAL_MS = 5
REPORT_MS = 50.0


class StallMonitor(QObject):
    def __init__(self, parent=None, interval_ms: int = INTERVAL_MS, report_ms: float = REPORT_MS):
        super().__init__(parent)
        self._interval = interval_ms
        self._report = report_ms
        self._last = time.perf_counter()
        self.longest_ms = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        gap = (now - self._last) * 1000.0
        self._last = now
        self.longest_ms = max(self.longest_ms, gap)
        perf.event("gui.gap", gap, quiet=gap < self._report)


# -- what the GUI thread spends a stall on: every top-level Qt event, by type and receiver class ----------------------
SLOW_EVENT_MS = 20.0
#: the event types named in the timeline; any other type is lumped under ``Other`` so the set of names stays small.
_EVENT_TYPES = frozenset({
    "Paint", "UpdateRequest", "UpdateLater", "Timer", "MetaCall", "LayoutRequest", "Resize", "Move", "Show", "Hide",
    "Wheel", "MouseMove", "MouseButtonPress", "MouseButtonRelease", "KeyPress", "KeyRelease",
    "GraphicsSceneWheel", "GraphicsSceneMouseMove", "GraphicsSceneMousePress", "GraphicsSceneMouseRelease",
    "StyleChange", "PolishRequest", "DeferredDelete", "Enter", "Leave", "HoverMove", "ToolTip", "SockAct",
})
_in_event = False
_CODE_MODULES = frozenset({"adichtion", "pyqtgraph", "PySide6", "shiboken6"})
_MAX_NAMES = 200
_seen: set[str] = set()


def timed_notify(call, receiver, ev):
    """``QApplication.notify`` with a clock around it (installed by the application class when ``ADICHTION_PERF`` is on).

    Only the outermost event of the GUI thread is timed (an event sent from inside another handler is part of it), and
    the name is ``ev.<Type>.<receiver class>``: both are code, never data.  This is what splits a GUI stall into its
    parts: repaints of which widgets, queued results from the worker (``MetaCall``), timers, layout passes.
    """
    global _in_event
    if _in_event or threading.current_thread() is not threading.main_thread():
        return call(receiver, ev)
    _in_event = True
    t = time.perf_counter()
    try:
        return call(receiver, ev)
    finally:
        ms = (time.perf_counter() - t) * 1000.0
        _in_event = False
        try:
            kind = ev.type().name
        except Exception:                                # noqa: BLE001 - an unnamed event type is not worth an error
            kind = "Other"
        cls = type(receiver)
        # only classes of this program or its toolkits are named, and at most _MAX_NAMES distinct names in all
        known = cls.__module__.split(".")[0] in _CODE_MODULES
        name = f"ev.{kind}.{cls.__name__}" if kind in _EVENT_TYPES and known else "ev.Other"
        if name not in _seen:
            if len(_seen) >= _MAX_NAMES:
                name = "ev.Other"
            else:
                _seen.add(name)
        perf.event(name, ms, quiet=ms < SLOW_EVENT_MS)
