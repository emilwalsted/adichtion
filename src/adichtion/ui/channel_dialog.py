"""The "Show channels…" dialog: which channels the stack shows."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)


class ChannelDialog(QDialog):
    def __init__(self, titles: list[str], calculated: list[bool], shown: list[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Show channels")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setModal(True)
        lay = QVBoxLayout(self)
        hint = QLabel("Tick the channels to show. The channel heights are made equal again when you press OK.")
        hint.setWordWrap(True)
        hint.setProperty("status", "muted")
        lay.addWidget(hint)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(4)
        self.boxes: list[QCheckBox] = []
        for i, title in enumerate(titles):
            label = f"Ch {i + 1} · {title}" + ("   (calculated)" if calculated[i] else "")
            box = QCheckBox(label)
            box.setChecked(i in shown)
            col.addWidget(box)
            self.boxes.append(box)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(420, 30 * max(1, len(titles)) + 20))
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        self.btn_all = QPushButton("Show all")
        self.btn_none = QPushButton("Hide all")
        for b in (self.btn_all, self.btn_none):
            b.setProperty("compact", True)
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(420, 520)

    def _set_all(self, on: bool) -> None:
        for b in self.boxes:
            b.setChecked(on)

    def selected(self) -> list[int]:
        return [i for i, b in enumerate(self.boxes) if b.isChecked()]
