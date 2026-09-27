"""The application icon, drawn with QPainter (no asset file to ship yet)."""
from __future__ import annotations

from .theme import ACCENT


def make_pixmap(size: int = 128):
    """A rounded accent-coloured square with a white trace: two breaths and a spike."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(ACCENT))
    r = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(max(1.5, size * 0.075))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    pts = [(0.12, 0.62), (0.26, 0.62), (0.33, 0.38), (0.40, 0.62), (0.50, 0.62),
           (0.55, 0.20), (0.60, 0.80), (0.66, 0.62), (0.78, 0.62), (0.88, 0.62)]
    path.moveTo(QPointF(pts[0][0] * size, pts[0][1] * size))
    for x, y in pts[1:]:
        path.lineTo(QPointF(x * size, y * size))
    p.drawPath(path)
    p.end()
    return pm


def app_icon():
    """A multi-resolution ``QIcon``; ``None`` when Qt is unavailable."""
    try:
        from PySide6.QtGui import QIcon
        icon = QIcon()
        for s in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(make_pixmap(s))
        return icon
    except Exception:                                   # pragma: no cover - cosmetic
        return None
