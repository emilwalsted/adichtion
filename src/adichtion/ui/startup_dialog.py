"""The start dialog: what the app asks when it opens without a file.

A button to browse for a ``.adicht`` file and, below it, the recently opened files (newest
first, the folder under each name); double-click or Enter opens one.  Files that have gone
missing are listed but cannot be chosen.  ``path`` holds the chosen recent file, ``browse``
is True when the user asked for the file dialog; both are unset on Cancel.
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from .. import __version__


def recent_entries(paths: list[str]) -> list[tuple[str, str, bool]]:
    """``(file name, folder, exists)`` for each recent path, in the given order (Qt-free)."""
    return [(os.path.basename(p) or p, os.path.dirname(p), os.path.isfile(p)) for p in paths]


class StartupDialog(QDialog):
    def __init__(self, recent: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open a LabChart file")
        self.setModal(True)
        self.path: Optional[str] = None
        self.browse = False
        self._paths = list(recent)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.logo = QLabel()
        try:
            from .logo import logo_pixmap
            pm = logo_pixmap(64, self.devicePixelRatioF())
            if pm is not None:
                self.logo.setPixmap(pm)
        except Exception:                            # noqa: BLE001 - cosmetic
            pass
        head.addWidget(self.logo)
        title = QLabel(f"<b>adichtion {__version__}</b><br>Open a LabChart .adicht file, or pick up where you left off.")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        lay.addLayout(head)

        self.btn_browse = QPushButton("Open a file…")
        self.btn_browse.setProperty("primary", True)
        self.btn_browse.setDefault(False)
        self.btn_browse.clicked.connect(self._on_browse)
        lay.addWidget(self.btn_browse)

        self.heading = QLabel("Recent files")
        self.heading.setProperty("role", "heading")
        lay.addWidget(self.heading)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setMinimumHeight(180)
        for name, folder, exists in recent_entries(self._paths):
            item = QListWidgetItem(f"{name}\n{folder}" + ("" if exists else "   (not found)"))
            item.setToolTip(os.path.join(folder, name))
            if not exists:
                item.setFlags(item.flags() & ~(Qt.ItemIsEnabled | Qt.ItemIsSelectable))
            self.list.addItem(item)
        self.list.itemActivated.connect(self._on_activated)
        lay.addWidget(self.list, 1)
        if self.list.count():
            for i in range(self.list.count()):
                if self.list.item(i).flags() & Qt.ItemIsEnabled:
                    self.list.setCurrentRow(i)
                    break
            self.list.setFocus()
        self.hint = QLabel("Double-click a recent file, or press Enter, to open it.")
        self.hint.setProperty("status", "muted")
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_open = QPushButton("Open")
        self.btn_open.setDefault(True)
        self.btn_open.clicked.connect(self._on_open_selected)
        self.btn_cancel = QPushButton("Start without a file")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.list.currentRowChanged.connect(lambda _r: self._update_open())
        self._update_open()
        self.resize(560, 460)

    def _update_open(self) -> None:
        item = self.list.currentItem()
        self.btn_open.setEnabled(item is not None and bool(item.flags() & Qt.ItemIsEnabled))

    def _on_browse(self) -> None:
        self.browse = True
        self.accept()

    def _on_activated(self, item: QListWidgetItem) -> None:
        row = self.list.row(item)
        if 0 <= row < len(self._paths) and item.flags() & Qt.ItemIsEnabled:
            self.path = self._paths[row]
            self.accept()

    def _on_open_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._on_activated(item)
