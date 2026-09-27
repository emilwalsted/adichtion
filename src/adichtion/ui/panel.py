"""Small widget helpers shared by the panels (the titled card, an eliding label)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class ElidingLabel(QLabel):
    """A label that shortens its text with an ellipsis instead of widening its parent."""

    def __init__(self, text: str = "", parent=None, mode=Qt.ElideMiddle):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(24)
        self.setText(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def fullText(self) -> str:
        return self._full

    def setText(self, text: str) -> None:               # noqa: N802 - Qt naming
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide()

    def _elide(self) -> None:
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, self._mode, max(24, self.width() - 4)))


def titled_panel(title: str, widget: QWidget, corner: QWidget | None = None) -> QFrame:
    """A card with a small muted title row and the content below it."""
    box = QFrame()
    box.setObjectName("panel")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 6, 10, 8)
    lay.setSpacing(4)
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    lab = ElidingLabel(title.upper(), mode=Qt.ElideRight)
    lab.setProperty("role", "panelTitle")
    box.title_label = lab
    header.addWidget(lab, 1)
    if corner is not None:
        header.addWidget(corner, 0, Qt.AlignRight)
    lay.addLayout(header)
    lay.addWidget(widget, 1)
    return box
