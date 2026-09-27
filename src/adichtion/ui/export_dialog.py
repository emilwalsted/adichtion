"""The export dialog: format, scope, channels and options, then the file name."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..export import XLSX_MAX_ROWS
from ..file import AdichtFile, Selection
from .timeline import Timeline

FORMATS = [("Text, LabChart style (.txt)", "txt", "txt"), ("CSV (.csv)", "csv", "csv"), ("Excel workbook (.xlsx)", "xlsx", "xlsx")]


@dataclass
class ExportRequest:
    path: str
    fmt: str
    selections: list[Selection]
    channels: list[int]
    decimal: str = "."
    header: bool = True
    comments: bool = True
    units: bool = True
    scope_label: str = ""
    extra: dict = field(default_factory=dict)


class ExportDialog(QDialog):
    def __init__(self, f: AdichtFile, timeline: Timeline, shown: list[int], cursor: Optional[float],
                 selection: Optional[tuple[float, float]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setModal(True)
        self.file, self.timeline = f, timeline
        self.request: Optional[ExportRequest] = None
        self._shown = list(shown)
        self._cursor, self._selection = cursor, selection
        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.format = QComboBox()
        for label, _fmt, _ext in FORMATS:
            self.format.addItem(label)
        form.addRow("Format", self.format)
        lay.addLayout(form)

        scope = QGroupBox("What to export")
        sl = QVBoxLayout(scope)
        self.scope_group = QButtonGroup(self)
        self.rb_file = QRadioButton(f"Whole file ({len(f.records)} block{'s' if len(f.records) != 1 else ''})")
        pos = timeline.locate(cursor) if cursor is not None else None
        self._block = pos.block if pos is not None else (timeline.blocks[0] if timeline.blocks else None)
        self.rb_block = QRadioButton(f"Block {self._block.number} (the block under the cursor)" if self._block
                                     else "Block (place the cursor first)")
        self.rb_block.setEnabled(self._block is not None)
        if selection is not None:
            self.rb_sel = QRadioButton(f"Selection ({selection[0]:.3f} s → {selection[1]:.3f} s, {selection[1] - selection[0]:.3f} s)")
        else:
            self.rb_sel = QRadioButton("Selection (none: Shift+click a channel to select an interval)")
            self.rb_sel.setEnabled(False)
        for rb in (self.rb_file, self.rb_block, self.rb_sel):
            self.scope_group.addButton(rb)
            sl.addWidget(rb)
        (self.rb_sel if selection is not None else self.rb_block if self._block else self.rb_file).setChecked(True)
        lay.addWidget(scope)

        chans = QGroupBox("Channels")
        cl = QVBoxLayout(chans)
        self.ch_group = QButtonGroup(self)
        self.rb_shown = QRadioButton(f"The channels shown ({len(shown)})")
        self.rb_all = QRadioButton(f"All channels ({f.n_channels})")
        self.rb_pick = QRadioButton("Choose:")
        for rb in (self.rb_shown, self.rb_all, self.rb_pick):
            self.ch_group.addButton(rb)
            cl.addWidget(rb)
        self.rb_shown.setChecked(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(24, 0, 0, 0)
        self.boxes: list[QCheckBox] = []
        for c in range(f.n_channels):
            b = QCheckBox(f"Ch {c + 1} · {f.channel_titles[c]}")
            b.setChecked(c in shown)
            b.toggled.connect(lambda _on: self.rb_pick.setChecked(True))
            il.addWidget(b)
            self.boxes.append(b)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFixedHeight(min(180, 26 * f.n_channels + 12))
        cl.addWidget(scroll)
        lay.addWidget(chans)

        opts = QGroupBox("Options")
        ol = QFormLayout(opts)
        self.decimal = QComboBox()
        self.decimal.addItems([". (point)", ", (comma, e.g. Danish Excel)"])
        ol.addRow("Decimal separator", self.decimal)
        self.cb_header = QCheckBox("Header lines (interval, start time, titles, units)")
        self.cb_header.setChecked(True)
        self.cb_comments = QCheckBox("Comments as a trailing column")
        self.cb_comments.setChecked(True)
        self.cb_units = QCheckBox("Values in units (off: stored ADC counts / raw values)")
        self.cb_units.setChecked(True)
        for cb in (self.cb_header, self.cb_comments, self.cb_units):
            ol.addRow(cb)
        lay.addWidget(opts)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setProperty("status", "muted")
        lay.addWidget(self.note)
        buttons = QDialogButtonBox()
        self.btn_export = buttons.addButton("Export…", QDialogButtonBox.AcceptRole)
        self.btn_export.setProperty("primary", True)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for w in (self.format, self.rb_file, self.rb_block, self.rb_sel, self.rb_shown, self.rb_all, self.rb_pick):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.toggled).connect(lambda *_a: self._update_note())
        self._update_note()
        self.resize(520, 640)

    # -- what the choices amount to -------------------------------------------------
    def fmt(self) -> str:
        return FORMATS[self.format.currentIndex()][1]

    def selections(self) -> list[Selection]:
        if self.rb_sel.isChecked() and self._selection is not None:
            return self.timeline.selections(*self._selection)
        if self.rb_block.isChecked() and self._block is not None:
            return [Selection(self._block.index, 0, self._block.ticks)]
        return [Selection(r.index, 0, r.ticks) for r in self.file.records]

    def channels(self) -> list[int]:
        if self.rb_all.isChecked():
            return list(range(self.file.n_channels))
        if self.rb_pick.isChecked():
            return [i for i, b in enumerate(self.boxes) if b.isChecked()]
        return list(self._shown)

    def scope_label(self) -> str:
        if self.rb_sel.isChecked():
            return "selection"
        if self.rb_block.isChecked() and self._block is not None:
            return f"block {self._block.number}"
        return "whole file"

    def _rows(self) -> int:
        chans = self.channels()
        c0 = chans[0] if chans else 0
        return sum(self.file.records[s.record].tick_to_sample(s.stop, c0) - self.file.records[s.record].tick_to_sample(s.start, c0)
                   for s in self.selections())

    def _update_note(self) -> None:
        rows = self._rows()
        n_sel = len(self.selections())
        text = f"{rows:,} rows × {len(self.channels())} channels"
        if self.fmt() == "xlsx":
            biggest = max((self.file.records[s.record].tick_to_sample(s.stop, 0) - self.file.records[s.record].tick_to_sample(s.start, 0)
                           for s in self.selections()), default=0)
            text += f", one sheet per block ({n_sel})"
            if biggest + 1 > XLSX_MAX_ROWS:
                text += f". A block of {biggest:,} rows does not fit an Excel sheet ({XLSX_MAX_ROWS - 1:,} rows): choose text or CSV, or a shorter selection."
        elif n_sel > 1:
            text += ", " + ("the header repeated at every block" if self.fmt() == "txt" else "with a block column")
        self.note.setText(text)

    def _accept(self) -> None:
        chans = self.channels()
        if not chans:
            self.note.setText("Choose at least one channel.")
            return
        rates = {self.file.records[0].sample_rate_hz(c) for c in chans} if self.file.records else set()
        if len(rates) > 1:
            self.note.setText("The chosen channels have different sampling rates; export them separately.")
            return
        _label, fmt, ext = FORMATS[self.format.currentIndex()]
        base = os.path.splitext(os.path.basename(self.file.path))[0]
        suggested = os.path.join(os.path.dirname(self.file.path), f"{base} - {self.scope_label()}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Export to", suggested, f"{_label} (*.{ext})")
        if not path:
            return
        if not path.lower().endswith("." + ext):
            path += "." + ext
        self.request = ExportRequest(path=path, fmt=fmt, selections=self.selections(), channels=chans,
                                     decimal="," if self.decimal.currentIndex() == 1 else ".",
                                     header=self.cb_header.isChecked(), comments=self.cb_comments.isChecked(),
                                     units=self.cb_units.isChecked(), scope_label=self.scope_label())
        self.accept()
