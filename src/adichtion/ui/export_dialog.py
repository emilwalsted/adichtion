"""The export dialog: format, scope, channels and options, then the file name.

The choices are remembered between exports (``QSettings`` group ``export``: format, scope,
channel choice, decimal separator, the three options and the folder last exported to).  The scope
starts as the selection when there is one; the context menu on a plot without one passes
``default_scope="file"`` (the whole recording); otherwise the remembered scope applies.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..export import XLSX_MAX_ROWS, table_rows, upsampling_notice
from ..file import AdichtFile, Selection
from .timeline import Timeline

SETTINGS_GROUP = "export"
SCOPES = ("file", "block", "selection")
FORMATS = [("Text, LabChart style (.txt)", "txt", "txt"), ("CSV (.csv)", "csv", "csv"),
           ("Tab-separated table (.txt)", "tsv", "txt"), ("Excel workbook (.xlsx)", "xlsx", "xlsx"),
           ("MATLAB (.mat)", "mat", "mat")]


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
                 selection: Optional[tuple[float, float]], parent=None, settings: Optional[QSettings] = None,
                 default_scope: Optional[str] = None):
        super().__init__(parent)
        self._settings = settings
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
            self.rb_sel = QRadioButton("Selection (none: drag, or Shift+click, on a channel to select an interval)")
            self.rb_sel.setEnabled(False)
        for rb in (self.rb_file, self.rb_block, self.rb_sel):
            self.scope_group.addButton(rb)
            sl.addWidget(rb)
        self._rb_scope = {"file": self.rb_file, "block": self.rb_block, "selection": self.rb_sel}
        self._initial_scope(default_scope)
        lay.addWidget(scope)

        chans = QGroupBox("Channels")
        cl = QVBoxLayout(chans)
        self.ch_group = QButtonGroup(self)
        self.rb_shown = QRadioButton(f"The channels shown ({len(shown)})")
        n_stored = sum(1 for c in range(f.n_channels) if f.has_data(c))
        self.rb_all = QRadioButton(f"All channels ({n_stored})")
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
            b = QCheckBox(f"Ch {c + 1} · {f.channel_titles[c]}" + ("" if f.has_data(c) else "   (not stored)"))
            b.setChecked(c in shown and f.has_data(c))
            b.setEnabled(f.has_data(c))
            b.toggled.connect(lambda _on: self.rb_pick.setChecked(True))
            b.toggled.connect(lambda _on: self._update_note() if hasattr(self, "note") else None)
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
        self._restore_options()
        for w in (self.format, self.rb_file, self.rb_block, self.rb_sel, self.rb_shown, self.rb_all, self.rb_pick):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.toggled).connect(lambda *_a: self._update_note())
        self.format.currentIndexChanged.connect(lambda *_a: self._sync_options())
        self.cb_header.toggled.connect(lambda *_a: self._update_note())
        self._sync_options()
        self._update_note()
        self.resize(520, 640)

    # -- what is remembered ----------------------------------------------------------
    def _get(self, key: str, default=None):
        if self._settings is None:
            return default
        return self._settings.value(f"{SETTINGS_GROUP}/{key}", default)

    def _get_bool(self, key: str, default: bool) -> bool:
        v = self._get(key, default)
        return v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")

    def _initial_scope(self, explicit: Optional[str]) -> None:
        """The scope the dialog opens on: an explicit request, else the selection when there is
        one, else what was chosen last time (when it still applies), else the block under the
        cursor, else the whole file."""
        usable = {"file": True, "block": self._block is not None, "selection": self._selection is not None}
        remembered = str(self._get("scope", "") or "")
        for choice in (explicit, "selection" if self._selection is not None else None, remembered,
                       "block", "file"):
            if choice in SCOPES and usable[choice]:
                self._rb_scope[choice].setChecked(True)
                return

    def _restore_options(self) -> None:
        if self._settings is None:
            return
        fmt = str(self._get("format", "") or "")
        for i, (_label, key, _ext) in enumerate(FORMATS):
            if key == fmt:
                self.format.setCurrentIndex(i)
        mode = str(self._get("channels", "") or "")
        picked = self._get("picked", []) or []
        picked = [picked] if isinstance(picked, (str, int)) else list(picked)
        try:
            picked = [int(c) for c in picked]
        except (TypeError, ValueError):
            picked = []
        if mode == "all":
            self.rb_all.setChecked(True)
        elif mode == "pick" and picked and all(0 <= c < self.file.n_channels for c in picked):
            for i, b in enumerate(self.boxes):
                b.blockSignals(True)
                b.setChecked(i in picked)
                b.blockSignals(False)
            self.rb_pick.setChecked(True)
        self.decimal.setCurrentIndex(1 if str(self._get("decimal", ".")) == "," else 0)
        self.cb_header.setChecked(self._get_bool("header", True))
        self.cb_comments.setChecked(self._get_bool("comments", True))
        self.cb_units.setChecked(self._get_bool("units", True))

    def _remember(self, path: str) -> None:
        if self._settings is None:
            return
        g = SETTINGS_GROUP
        st = self._settings
        st.setValue(f"{g}/format", self.fmt())
        st.setValue(f"{g}/scope", next(k for k, rb in self._rb_scope.items() if rb.isChecked()))
        st.setValue(f"{g}/channels", "all" if self.rb_all.isChecked() else "pick" if self.rb_pick.isChecked() else "shown")
        st.setValue(f"{g}/picked", [i for i, b in enumerate(self.boxes) if b.isChecked()])
        st.setValue(f"{g}/decimal", "," if self.decimal.currentIndex() == 1 else ".")
        st.setValue(f"{g}/header", self.cb_header.isChecked())
        st.setValue(f"{g}/comments", self.cb_comments.isChecked())
        st.setValue(f"{g}/units", self.cb_units.isChecked())
        st.setValue(f"{g}/folder", os.path.dirname(path))

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
            return [c for c in range(self.file.n_channels) if self.file.has_data(c)]
        if self.rb_pick.isChecked():
            return [i for i, b in enumerate(self.boxes) if b.isChecked() and b.isEnabled()]
        return list(self._shown)

    def scope_label(self) -> str:
        if self.rb_sel.isChecked():
            return "selection"
        if self.rb_block.isChecked() and self._block is not None:
            return f"block {self._block.number}"
        return "whole file"

    def _table_rows(self) -> list[int]:
        chans = self.channels()
        if not chans:
            return [0 for _s in self.selections()]
        return [table_rows(self.file.records[s.record], s, chans) for s in self.selections()]

    def _rows(self) -> int:
        return sum(self._table_rows())

    def upsampling_note(self) -> Optional[str]:
        chans = self.channels()
        return upsampling_notice(self.file, self.selections(), chans) if chans else None

    def _sync_options(self) -> None:
        """A MATLAB file has no decimal separator, header lines or comment column; Excel no decimal
        separator (Excel applies its own); only LabChart-style text has the header lines of its own kind."""
        fmt = self.fmt()
        self.decimal.setEnabled(fmt in ("txt", "csv", "tsv"))
        self.cb_header.setEnabled(fmt != "mat")
        self.cb_comments.setEnabled(fmt != "mat")

    def _update_note(self) -> None:
        per_table = self._table_rows()
        rows = sum(per_table)
        n_sel = len(per_table)
        text = f"{rows:,} rows × {len(self.channels())} channels"
        if self.fmt() == "mat":
            text += f", one matrix per block ({n_sel}) with the channels as rows (data_block1, data_block2 …)"
        elif self.fmt() == "xlsx":
            biggest = max(per_table, default=0)
            text += f", one sheet per block ({n_sel})"
            if biggest + 1 > XLSX_MAX_ROWS:
                text += f". A block of {biggest:,} rows does not fit an Excel sheet ({XLSX_MAX_ROWS - 1:,} rows): choose text or CSV, or a shorter selection."
        elif n_sel > 1:
            text += ", " + ("the header repeated at every block" if self.fmt() == "txt" else "with a block column first (RespMech reads the first block only: export one block at a time for it)")
        if not self.cb_header.isChecked() and self.cb_header.isEnabled() and self.fmt() in ("csv", "tsv", "xlsx"):
            text += ". Without the header line RespMech takes the first row of data as column titles."
        note = self.upsampling_note()
        if note:
            text += ".\n" + note
        self.note.setText(text)

    def _accept(self) -> None:
        chans = self.channels()
        if not chans:
            self.note.setText("Choose at least one channel.")
            return
        _label, fmt, ext = FORMATS[self.format.currentIndex()]
        base = os.path.splitext(os.path.basename(self.file.path))[0]
        folder = str(self._get("folder", "") or "")
        if not folder or not os.path.isdir(folder):
            folder = os.path.dirname(self.file.path)
        suggested = os.path.join(folder, f"{base} - {self.scope_label()}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Export to", suggested, f"{_label} (*.{ext})")
        if not path:
            return
        if not path.lower().endswith("." + ext):
            path += "." + ext
        self.request = ExportRequest(path=path, fmt=fmt, selections=self.selections(), channels=chans,
                                     decimal="," if self.decimal.currentIndex() == 1 else ".",
                                     header=self.cb_header.isChecked(), comments=self.cb_comments.isChecked(),
                                     units=self.cb_units.isChecked(), scope_label=self.scope_label())
        self._remember(path)
        self.accept()
