"""A value axis whose major ticks thin out when the plot row gets short (from RespMech's
``MinPitchAxis``): pyqtgraph draws its top tick level unconditionally, so a 50 px row
prints its labels through one another.  ``tickSpacing`` walks the chosen spacing up the
1/2/5 ladder until the labels have room."""
from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6.QtGui import QFontMetrics


def next_nice(v: float) -> float:
    """The next value up pyqtgraph's 1 / 2 / 5 ladder (0.2 -> 0.5 -> 1 -> 2 -> 5 -> 10)."""
    if v <= 0:
        return v
    dec = 10.0 ** math.floor(math.log10(v))
    if dec <= 0:
        return v
    m = v / dec
    if m < 1.999:
        return 2.0 * dec
    if m < 4.999:
        return 5.0 * dec
    return 10.0 * dec


class MinPitchAxis(pg.AxisItem):
    AIR = 3     # pixels between two labels on top of the text height
    TEXT_GAP = 6   # pixels of air between the tick labels and the axis edge / the rotated unit

    def tickSpacing(self, minVal, maxVal, size):
        levels = super().tickSpacing(minVal, maxVal, size)
        span = abs(maxVal - minVal)
        if not levels or self.orientation not in ("left", "right") or span <= 0 or size <= 0:
            return levels
        pitch = self._min_pitch()
        if pitch <= 0:
            return levels
        need = pitch * span / size
        major = levels[0][0]
        for _ in range(64):
            if major >= need or major >= span:
                break
            nxt = next_nice(major)
            if not nxt > major:
                break
            major = nxt
        if major == levels[0][0]:
            return levels
        return [(major, levels[0][1])] + [lv for lv in levels[1:] if lv[0] < major]

    def _min_pitch(self) -> float:
        font = self.style.get("tickFont") or self.font()
        try:
            return QFontMetrics(font).height() * 0.8 + self.AIR
        except Exception:                        # pragma: no cover
            return 0.0

    def resizeEvent(self, ev=None):
        super().resizeEvent(ev)
        # a wider shared axis leaves the tick labels right against the plot; bring the rotated unit
        # up to them instead of leaving it at the far left edge
        if self.orientation == "left" and self.label.isVisible() and self.labelText:
            spare = self.width() - self.needed_width()
            if spare > 0:
                self.label.setPos(self.label.pos().x() + spare, self.label.pos().y())

    def needed_width(self) -> int:
        """Pixels this (left) axis needs to show its tick labels and, when visible, its
        rotated label, without the loose gap a fixed width leaves; 0 when nothing is known yet."""
        vb = self.linkedView()
        size = self.height()
        if vb is None or size <= 0:
            return 0
        lo, hi = vb.viewRange()[1]
        if not (hi > lo):
            return 0
        strings: list[str] = []
        try:
            for spacing, values in self.tickValues(lo, hi, size)[:2]:
                strings += list(self.tickStrings(values, 1.0, spacing))
        except Exception:                        # pragma: no cover - a pyqtgraph internals change
            return 0
        tick_font = self.style.get("tickFont") or self.font()
        text = max((QFontMetrics(tick_font).horizontalAdvance(s) for s in strings), default=0)
        label = 0
        if self.label.isVisible() and self.labelText:
            label = QFontMetrics(self.label.font()).height() + 4
        tick = abs(self.style.get("tickLength", 5))
        return int(text + label + tick + self.TEXT_GAP)
