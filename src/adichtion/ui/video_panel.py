"""The video panel: the frame of the block's video clip at the cursor."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..file import AdichtFile
from .timeline import Timeline
from .workers import FrameGrabber

FRAME_DEBOUNCE_MS = 120


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


class VideoPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self._pixmap: Optional[QPixmap] = None
        self._request = 0
        self._pending: Optional[tuple[str, float, str]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.frame = QLabel()
        self.frame.setAlignment(Qt.AlignCenter)
        self.frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.frame.setMinimumSize(120, 80)
        self.frame.setProperty("status", "muted")
        lay.addWidget(self.frame, 1)
        self.caption = QLabel("")
        self.caption.setProperty("status", "muted")
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FRAME_DEBOUNCE_MS)
        self._timer.timeout.connect(self._fire)
        self.grabber: Optional[FrameGrabber] = None
        if opencv_available():
            self.grabber = FrameGrabber(self)
            self.grabber.frame.connect(self._on_frame)
            self.grabber.error.connect(self._on_error)
            self.grabber.start()
        self._show_text("No video")

    def close(self) -> None:
        if self.grabber is not None:
            self.grabber.stop()

    def set_file(self, f: Optional[AdichtFile], timeline: Optional[Timeline]) -> None:
        self.file, self.timeline = f, timeline
        self._pixmap = None
        self._show_text("No video" if f is None or not f.videos else "Place the cursor to see the frame")
        self.caption.setText("")

    @property
    def has_videos(self) -> bool:
        return bool(self.file is not None and any(v.has_video for v in self.file.videos))

    def _show_text(self, text: str) -> None:
        self._pixmap = None
        self.frame.setPixmap(QPixmap())
        self.frame.setText(text)

    def set_position(self, t: Optional[float]) -> None:
        """Ask for the frame at file time ``t`` (debounced)."""
        if self.file is None or self.timeline is None or t is None:
            return
        pos = self.timeline.locate(t)
        rec = self.file.records[pos.block.index]
        clip = rec.video
        if clip is None or not clip.has_video:
            self._show_text(f"No video for block {pos.block.number}")
            self.caption.setText("")
            return
        path = rec.video_file
        if path is None:
            self._show_text("Video file not found")
            self.caption.setText(f"Expected {clip.file_name} in {os.path.basename(self.file.movies_folder)}")
            return
        if self.grabber is None:
            self._show_text("OpenCV is not installed (pip install opencv-python-headless)")
            self.caption.setText(clip.file_name)
            return
        file_time = clip.file_time(pos.block_time_s)
        self._pending = (path, file_time, f"Block {pos.block.number} · {clip.file_name} · {file_time:.3f} s in the clip"
                                          f" (camera delay {clip.delay_s:.3f} s compensated)")
        self._timer.start()

    def _fire(self) -> None:
        if self._pending is None or self.grabber is None:
            return
        path, file_time, caption = self._pending
        self._request += 1
        self.caption.setText(caption)
        self.grabber.request(self._request, path, file_time)

    def _on_frame(self, request_id: int, img, actual: float) -> None:
        if request_id != self._request:
            return
        if img is None:
            self._show_text("No frame at this position")
            return
        self._pixmap = QPixmap.fromImage(img)
        self.frame.setText("")
        self._rescale()

    def _on_error(self, request_id: int, message: str) -> None:
        if request_id == self._request:
            self._show_text(f"Could not decode the video: {message}")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        self.frame.setPixmap(self._pixmap.scaled(self.frame.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
