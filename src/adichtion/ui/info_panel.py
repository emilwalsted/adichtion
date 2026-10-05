"""The file panel: what the file is, its blocks, channels and comments."""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableView, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

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


class _RowsModel(QAbstractTableModel):
    """A read-only table over a list of source rows; a cell's text is made when the view asks for it.

    A ``QTableWidget`` builds one ``QTableWidgetItem`` per cell up front (about 44 microseconds per comment
    row), which is what made a file with thousands of comments slow to open. Here the model only keeps the
    list; the view asks for the visible rows alone."""

    def __init__(self, headers: Sequence[str], right_aligned: Sequence[int], cell: Callable[[object, int], object],
                 tooltip_col: Optional[int] = None, parent=None):
        super().__init__(parent)
        self._headers = list(headers)
        self._right = frozenset(right_aligned)
        self._cell = cell
        self._tooltip_col = tooltip_col
        self._rows: Sequence = ()

    def set_rows(self, rows: Sequence) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(self._headers):
            return self._headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        col = index.column()
        if role == Qt.DisplayRole:
            return str(self._cell(self._rows[index.row()], col))
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if col in self._right else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col == self._tooltip_col:
            return str(self._cell(self._rows[index.row()], col))
        return None


class _RowsView(QTableView):
    """A ``QTableView`` with the few ``QTableWidget`` entry points the panel and the tests use
    (``cellClicked``, ``cellDoubleClicked``, ``rowCount``), for a model that makes its cells lazily."""

    cellClicked = Signal(int, int)
    cellDoubleClicked = Signal(int, int)

    def __init__(self, model: _RowsModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.verticalHeader().setVisible(False)
        # fixed row height: the default would ask the delegate about every row to size it
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setWordWrap(False)
        self.setShowGrid(True)
        hh = self.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setResizeContentsPrecision(200)       # size columns from a sample of rows, not every row
        self.clicked.connect(lambda i: self.cellClicked.emit(i.row(), i.column()))
        self.doubleClicked.connect(lambda i: self.cellDoubleClicked.emit(i.row(), i.column()))

    def rowCount(self) -> int:
        return self.model().rowCount()


_VIDEO_COLUMN = 4                               # the Video column of the Blocks table


class InfoPanel(QWidget):
    blockActivated = Signal(int)                # record index (a click)
    blockFitRequested = Signal(int)             # record index (a double-click)
    commentActivated = Signal(float)            # file time
    _video_counted = Signal(int, int)           # (token, clips found), from the background lookup

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
        self._blocks_model = _RowsModel(["Block", "Start", "Duration", "Comments", "Video"], (0, 2, 3), self._block_cell)
        self._comments_model = _RowsModel(["#", "Block", "Time (s)", "Text"], (0, 1, 2), self._comment_cell,
                                          tooltip_col=3)
        self.blocks = _RowsView(self._blocks_model)
        self.channels = _table(["Ch", "Title", "Type", "Rate (Hz)", "Units", "Range", "Definition"])
        self.comments = _RowsView(self._comments_model)
        self.tabs.addTab(self.comments, "Comments")
        self.tabs.addTab(self.channels, "Channels")
        self.tabs.addTab(self.blocks, "Blocks")
        lay.addWidget(self.tabs, 1)
        self.blocks.cellClicked.connect(lambda r, _c: self.blockActivated.emit(r))
        self.blocks.cellDoubleClicked.connect(lambda r, _c: self.blockFitRequested.emit(r))
        self.comments.cellClicked.connect(self._on_comment_clicked)
        self._comment_times: list[float] = []
        self._video_token = 0                    # which file the background clip count belongs to
        self._video_counted.connect(self._on_video_counted)
        self._summary_parts: list[str] = []
        self._video_part = -1                    # where the clip count sits in the summary

    @staticmethod
    def _block_cell(r, col: int):
        if col == 0:
            return r.index + 1
        if col == 1:
            return f"{r.start_local:%Y-%m-%d %H:%M:%S}"
        if col == 2:
            return _hms(r.duration_s)
        if col == 3:
            return len(r.comments)
        v = r.video
        return "-" if v is None else (v.file_name if v.has_video else "none")

    @staticmethod
    def _comment_cell(m, col: int):
        if col == 0:
            return m.comment.id
        if col == 1:
            return m.block.number
        if col == 2:
            return f"{m.comment.tick / m.block.tick_rate_hz:.3f}"
        return m.comment.text

    def set_file(self, f: Optional[AdichtFile], timeline: Optional[Timeline]) -> None:
        self.file, self.timeline = f, timeline
        self.channels.setRowCount(0)
        self._blocks_model.set_rows(())
        self._comments_model.set_rows(())
        self._comment_times = []
        self._video_token += 1                   # a lookup still running for the previous file is dropped
        if f is None:
            self.name.setText("No file open")
            self.path.setText("")
            self.summary.setText("")
            return
        self.name.setText(os.path.basename(f.path))
        self.path.setText(f.path)
        n_video = sum(1 for v in f.videos if v.has_video)
        size_mb = os.path.getsize(f.path) / 1e6 if os.path.exists(f.path) else 0.0
        parts = [f.format_label, f"{len(f.records)} block{'s' if len(f.records) != 1 else ''}",
                 f"{f.n_channels} channels", _hms(timeline.duration_s) + " recorded", f"{size_mb:.1f} MB"]
        self._video_part = -1
        if f.videos:
            self._video_part = len(parts)
            parts.append(f"{n_video} video clip{'s' if n_video != 1 else ''}")
        n_missing = sum(1 for c in range(f.n_channels) if not f.has_data(c))
        if n_missing:
            parts.append(f"{n_missing} calculated channel{'s' if n_missing != 1 else ''} not stored")
        if f.parse_mode != "sequential":
            parts.append("read by the fallback scanner: block index and LSBs unknown")
        self._summary_parts = parts
        self.summary.setText(" · ".join(parts))
        self._count_videos(f, n_video)
        # blocks and comments: the models make a cell's text when the view shows it
        self._blocks_model.set_rows(f.records)
        self.blocks.setColumnHidden(_VIDEO_COLUMN, n_video == 0)   # no clip attached: no Video column
        # channels
        rec0 = f.records[0] if f.records else None
        self.channels.setRowCount(f.n_channels)
        for c in range(f.n_channels):
            dtype = f.index.channel_dtype(c)
            rng = f.range_volts(c)
            if not f.has_data(c):
                kind = "calculated, not stored" if f.channel_definition(c) else "no data"
            else:
                kind = "calculated" if dtype == "float32" else "raw"
            stored = f.has_data(c) and rec0 is not None
            vals = [c + 1, f.channel_titles[c], kind,
                    f"{rec0.sample_rate_hz(c):g}" if stored else "", rec0.value_label(c) if stored else "",
                    f"±{rng:g} V" if rng is not None else "", ""]
            for col, val in enumerate(vals):
                self.channels.setItem(c, col, _item(val, Qt.AlignRight if col in (0, 3) else Qt.AlignLeft))
        # comments
        self._comment_times = [m.file_time_s for m in timeline.comments]
        self._comments_model.set_rows(timeline.comments)
        self.tabs.setTabText(0, f"Comments ({len(timeline.comments)})")
        self.tabs.setTabText(1, f"Channels ({f.n_channels})")
        self.tabs.setTabText(2, f"Blocks ({len(f.records)})")

    def _count_videos(self, f: AdichtFile, n_video: int) -> None:
        """How many of the clips exist next to the document. One file-system lookup per clip, which on a
        network drive is slow, so it runs on a thread and the summary gains "(n found)" when it is done
        (only when some are missing, as before)."""
        if not f.videos or n_video == 0:
            return
        paths = [p for p in (r.video_candidate for r in f.records) if p is not None]
        token = self._video_token

        def work() -> None:
            n_found = sum(1 for p in paths if os.path.exists(p))
            try:
                self._video_counted.emit(token, n_found)
            except RuntimeError:                 # the panel was deleted meanwhile
                pass

        threading.Thread(target=work, name="adichtion-video-count", daemon=True).start()

    def _on_video_counted(self, token: int, n_found: int) -> None:
        if token != self._video_token or self.file is None:
            return
        n_video = sum(1 for v in self.file.videos if v.has_video)
        if n_found != n_video and 0 <= self._video_part < len(self._summary_parts):
            parts = list(self._summary_parts)
            parts[self._video_part] += f" ({n_found} found)"
            self.summary.setText(" · ".join(parts))

    def set_definition(self, channel: int, text: Optional[str]) -> None:
        if 0 <= channel < self.channels.rowCount():
            self.channels.setItem(channel, 6, _item(text or ""))

    def _on_comment_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._comment_times):
            self.commentActivated.emit(self._comment_times[row])
