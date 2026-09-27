"""The file panel: what the file is, its blocks, channels and comments."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ..file import AdichtFile
from .panel import ElidingLabel
from .timeline import Timeline


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    h, m, s = s // 3600, (s // 60) % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.setWordWrap(False)
    return t


def _item(text, align=Qt.AlignLeft) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align | Qt.AlignVCenter)
    return it


class InfoPanel(QWidget):
    blockActivated = Signal(int)                # record index
    commentActivated = Signal(float)            # file time

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.name = QLabel("No file open")
        self.name.setProperty("role", "heading")
        self.path = ElidingLabel("")
        self.path.setProperty("status", "muted")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.name)
        lay.addWidget(self.path)
        lay.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.blocks = _table(["Block", "Start", "Duration", "Comments", "Video"])
        self.channels = _table(["Ch", "Title", "Type", "Rate (Hz)", "Units", "Range", "Definition"])
        self.comments = _table(["#", "Block", "Time (s)", "Text"])
        self.tabs.addTab(self.blocks, "Blocks")
        self.tabs.addTab(self.channels, "Channels")
        self.tabs.addTab(self.comments, "Comments")
        lay.addWidget(self.tabs, 1)
        self.blocks.cellClicked.connect(lambda r, _c: self.blockActivated.emit(r))
        self.comments.cellClicked.connect(self._on_comment_clicked)
        self._comment_times: list[float] = []

    def set_file(self, f: Optional[AdichtFile], timeline: Optional[Timeline]) -> None:
        self.file, self.timeline = f, timeline
        for t in (self.blocks, self.channels, self.comments):
            t.setRowCount(0)
        self._comment_times = []
        if f is None:
            self.name.setText("No file open")
            self.path.setText("")
            self.summary.setText("")
            return
        self.name.setText(os.path.basename(f.path))
        self.path.setText(f.path)
        n_video = sum(1 for v in f.videos if v.has_video)
        size_mb = os.path.getsize(f.path) / 1e6 if os.path.exists(f.path) else 0.0
        parts = [f"LabChart format v{f.format_version}", f"{len(f.records)} block{'s' if len(f.records) != 1 else ''}",
                 f"{f.n_channels} channels", _hms(timeline.duration_s) + " recorded", f"{size_mb:.1f} MB"]
        if f.videos:
            parts.append(f"{n_video} video clip{'s' if n_video != 1 else ''}")
        if f.parse_mode != "sequential":
            parts.append("read by the fallback scanner: block index and LSBs unknown")
        self.summary.setText(" · ".join(parts))
        # blocks
        self.blocks.setRowCount(len(f.records))
        for i, r in enumerate(f.records):
            v = r.video
            vid = "-" if v is None else (v.file_name if v.has_video else "none")
            for col, val in enumerate([r.index + 1, f"{r.start_local:%Y-%m-%d %H:%M:%S}", _hms(r.duration_s),
                                       len(r.comments), vid]):
                self.blocks.setItem(i, col, _item(val, Qt.AlignRight if col in (0, 2, 3) else Qt.AlignLeft))
        # channels
        rec0 = f.records[0] if f.records else None
        self.channels.setRowCount(f.n_channels)
        for c in range(f.n_channels):
            dtype = f.index.channel_dtype(c)
            rng = f.range_volts(c)
            vals = [c + 1, f.channel_titles[c], "calculated" if dtype == "float32" else "raw",
                    f"{rec0.sample_rate_hz(c):g}" if rec0 else "", rec0.value_label(c) if rec0 else "",
                    f"±{rng:g} V" if rng is not None else "", ""]
            for col, val in enumerate(vals):
                self.channels.setItem(c, col, _item(val, Qt.AlignRight if col in (0, 3) else Qt.AlignLeft))
        # comments
        self.comments.setRowCount(len(timeline.comments))
        for i, m in enumerate(timeline.comments):
            self._comment_times.append(m.file_time_s)
            vals = [m.comment.id, m.block.number, f"{m.comment.tick / m.block.tick_rate_hz:.3f}", m.comment.text]
            for col, val in enumerate(vals):
                self.comments.setItem(i, col, _item(val, Qt.AlignRight if col < 3 else Qt.AlignLeft))
        self.tabs.setTabText(2, f"Comments ({len(timeline.comments)})")
        self.tabs.setTabText(0, f"Blocks ({len(f.records)})")
        self.tabs.setTabText(1, f"Channels ({f.n_channels})")

    def set_definition(self, channel: int, text: Optional[str]) -> None:
        if 0 <= channel < self.channels.rowCount():
            self.channels.setItem(channel, 6, _item(text or ""))

    def _on_comment_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._comment_times):
            self.commentActivated.emit(self._comment_times[row])
