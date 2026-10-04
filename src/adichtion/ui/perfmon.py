"""GUI-thread stall monitor for ``ADICHTION_PERF`` (see :mod:`adichtion.perf`).

A 5 ms ``QTimer`` that measures the gap between its own ticks: whenever the GUI thread is
busy (decoding on it, a slow paint, a layout pass) the next tick comes late, and the gap
is the stall the user feels as a freeze.  Gaps of ``REPORT_MS`` or more are printed as
``gui.stall`` lines, every gap is aggregated (``gui.gap``: the maximum is the longest
stall of the run).  Created only when timing is on, so a normal run has no extra timer.
"""
from __future__ import annotations

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
