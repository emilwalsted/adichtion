"""The data panel: the values under the cursor, or the statistics of the selected interval."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..file import AdichtFile
from . import lod
from .timeline import Timeline


def _num(v: Optional[float]) -> str:
    if v is None:
        return "–"
    return f"{v:.5g}"


class DataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self._units: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.where = QLabel("Click a channel to place the cursor; drag, or Shift+click, to select an interval.")
        self.where.setWordWrap(True)
        self.where.setProperty("status", "muted")
        lay.addWidget(self.where)
        self.table = QTableWidget(0, 2)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)
        self._mode = "none"
        self._set_headers(["Channel", "Value"])

    def _set_headers(self, headers: list[str]) -> None:
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        hh = self.table.horizontalHeader()
        for i in range(len(headers)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setMinimumSectionSize(56)
        hh.setStretchLastSection(True)

    def set_file(self, f: Optional[AdichtFile], timeline: Optional[Timeline]) -> None:
        self.file, self.timeline = f, timeline
        self.table.setRowCount(0)
        self._mode = "none"
        self._units = []
        # one row per channel with stored samples (a Mac document's calculated channels have none)
        self._rows = [c for c in range(f.n_channels) if f.has_data(c)] if f is not None else []
        self._row_of = {c: i for i, c in enumerate(self._rows)}
        if f is not None and f.records:
            rec0 = f.records[0]
            self._units = [rec0.value_label(c) for c in range(f.n_channels)]
        self.where.setText("Click a channel to place the cursor; drag, or Shift+click, to select an interval."
                           if f is not None else "")

    def _channel_name(self, c: int) -> str:
        return f"Ch {c + 1} · {self.file.channel_titles[c]}" if self.file else f"Ch {c + 1}"

    def show_point(self, t: Optional[float]) -> None:
        """The values of every channel at file time ``t``."""
        if self.file is None or self.timeline is None:
            return
        if t is None:
            self.table.setRowCount(0)
            self._mode = "none"
            self.where.setText("Click a channel to place the cursor; drag, or Shift+click, to select an interval.")
            return
        pos = self.timeline.locate(t)
        self.where.setText(f"Cursor: {self.timeline.describe(t)}   (file time {t:.3f} s)")
        self._mode = "point"
        self._set_headers(["Channel", "Value", "Units"])
        self.table.setRowCount(len(self._rows))
        for row, c in enumerate(self._rows):
            v = lod.value_at(self.file, self.timeline, c, t)
            self._set(row, [self._channel_name(c), _num(v), self._units[c]])

    def show_interval(self, t0: float, t1: float) -> None:
        """Header for an interval; the statistics arrive per channel via :meth:`set_stats`."""
        if self.file is None or self.timeline is None:
            return
        self._mode = "interval"
        a, b = self.timeline.locate(t0), self.timeline.locate(t1)
        blocks = f"Block {a.block.number}" if a.block is b.block else f"Blocks {a.block.number}–{b.block.number}"
        self.where.setText(f"Selection: {t0:.3f} s → {t1:.3f} s  ({t1 - t0:.3f} s, {blocks})")
        self._set_headers(["Channel", "Min", "Max", "Mean", "Units"])
        self.table.setRowCount(len(self._rows))
        for row, c in enumerate(self._rows):
            self._set(row, [self._channel_name(c), "…", "…", "…", self._units[c]])

    def set_stats(self, channel: int, stats: dict) -> None:
        row = self._row_of.get(channel)
        if self._mode != "interval" or row is None or row >= self.table.rowCount():
            return
        mean = _num(stats.get("mean")) if stats.get("decoded", True) else "(too long)"
        self._set(row, [self._channel_name(channel), _num(stats.get("min")), _num(stats.get("max")), mean,
                        self._units[channel]])

    def _set(self, row: int, values: list[str]) -> None:
        for col, val in enumerate(values):
            it = QTableWidgetItem(val)
            it.setTextAlignment((Qt.AlignRight if 0 < col < len(values) - 1 else Qt.AlignLeft) | Qt.AlignVCenter)
            self.table.setItem(row, col, it)
