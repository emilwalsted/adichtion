"""PySide6 6.12.0 on Python 3.10/3.11 gives back one reference to None per QPainter.setPen/setRenderHint call."""
import sys

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QColor, QImage, QPainter, QPen


def test_painter_calls_do_not_release_references_to_none(qapp):
    img = QImage(32, 32, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        calls = (
            lambda: p.setRenderHint(QPainter.RenderHint.Antialiasing, False),
            lambda: p.setPen(QPen(QColor("red"))),
        )
        for call in calls:
            call()
            before = sys.getrefcount(None)
            for _ in range(1000):
                call()
            # None is immortal from Python 3.12 (the count never moves); before that a leak shows as a drop
            # a leak costs one reference per call (1000); ordinary churn elsewhere is far smaller
            assert before - sys.getrefcount(None) < 500
    finally:
        p.end()
