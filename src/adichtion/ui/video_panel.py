"""The video panel: the frame of the block's video clip at the cursor, and playback.

Playback is a clock, not a media player: while playing, a timer advances the
*file time* at the chosen rate from the moment play was pressed, emits
``positionChanged`` (the main window moves the cursor, so every overlay and
panel follows) and asks the grabber for the frame at that time.  The grabber
serves only the latest request, so a slow decoder drops frames rather than
falling behind.  Playback stays inside the block whose clip is shown and
pauses at its end; ``play(stop_at=t)`` pauses at file time ``t`` instead when that comes first
(playing a selected interval).
"""
from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QStyle, QToolButton, QVBoxLayout, QWidget

from ..file import AdichtFile
from .timeline import BlockSpan, Timeline
from .workers import FrameGrabber

FRAME_DEBOUNCE_MS = 120
TICK_MS = 40                                     # playback clock
RATES = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _rate_text(rate: float) -> str:
    return f"{rate:g}×"


class VideoPanel(QWidget):
    positionChanged = Signal(float)              # file time, while playing or after start/end
    playingChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self._pixmap: Optional[QPixmap] = None
        self._request = 0
        self._pending: Optional[tuple[str, float, str]] = None
        # the clip under the cursor
        self._block: Optional[BlockSpan] = None
        self._path: Optional[str] = None
        self._clip = None
        self._t: Optional[float] = None          # file time the panel is at
        # playback
        self._playing = False
        self._rate_index = RATES.index(1.0)
        self._anchor_wall = 0.0
        self._anchor_t = 0.0
        self._driving = False                    # True while the panel itself moves the cursor
        self._stop_at: Optional[float] = None    # file time playback pauses at (an interval's end)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.frame = QLabel()
        self.frame.setAlignment(Qt.AlignCenter)
        self.frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.frame.setMinimumSize(120, 80)
        self.frame.setProperty("status", "muted")
        lay.addWidget(self.frame, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(2)
        st = self.style()
        self.btn_start = self._button(st.standardIcon(QStyle.SP_MediaSkipBackward), "Go to the start of the block's video", self.go_start)
        self.btn_play = self._button(st.standardIcon(QStyle.SP_MediaPlay), "Play from the cursor (Space)", self.toggle)
        self.btn_end = self._button(st.standardIcon(QStyle.SP_MediaSkipForward), "Go to the end of the block's video", self.go_end)
        self.btn_slower = self._button(st.standardIcon(QStyle.SP_MediaSeekBackward), "Play slower", self.slower)
        self.rate_label = QLabel(_rate_text(1.0))
        self.rate_label.setProperty("status", "muted")
        self.rate_label.setMinimumWidth(40)
        self.rate_label.setAlignment(Qt.AlignCenter)
        self.btn_faster = self._button(st.standardIcon(QStyle.SP_MediaSeekForward), "Play faster", self.faster)
        for w in (self.btn_start, self.btn_play, self.btn_end):
            bar.addWidget(w)
        bar.addSpacing(10)
        bar.addWidget(self.btn_slower)
        bar.addWidget(self.rate_label)
        bar.addWidget(self.btn_faster)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.caption = QLabel("")
        self.caption.setProperty("status", "muted")
        self.caption.setWordWrap(True)
        lay.addWidget(self.caption)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(FRAME_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire)
        self._clock = QTimer(self)
        self._clock.setInterval(TICK_MS)
        self._clock.timeout.connect(self._tick)
        self.grabber: Optional[FrameGrabber] = None
        if opencv_available():
            self.grabber = FrameGrabber(self)
            self.grabber.frame.connect(self._on_frame)
            self.grabber.error.connect(self._on_error)
            self.grabber.start()
        self._show_text("No video")
        self._update_controls()

    def _button(self, icon, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(slot)
        return b

    def close(self) -> None:
        self.pause()
        if self.grabber is not None:
            self.grabber.stop()

    # -- file and position ----------------------------------------------------------------
    def set_file(self, f: Optional[AdichtFile], timeline: Optional[Timeline]) -> None:
        self.pause()
        self.file, self.timeline = f, timeline
        self._pixmap = None
        self._block = self._path = self._clip = None
        self._t = None
        self._show_text("No video" if f is None or not f.videos else "Place the cursor to see the frame")
        self.caption.setText("")
        self._update_controls()

    @property
    def has_videos(self) -> bool:
        return bool(self.file is not None and any(v.has_video for v in self.file.videos))

    @property
    def has_clip(self) -> bool:
        """Whether the cursor's block has a playable clip."""
        return self._path is not None and self.grabber is not None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def rate(self) -> float:
        return RATES[self._rate_index]

    @property
    def position(self) -> Optional[float]:
        return self._t

    def _show_text(self, text: str) -> None:
        self._pixmap = None
        self.frame.setPixmap(QPixmap())
        self.frame.setText(text)

    def set_position(self, t: Optional[float]) -> None:
        """The cursor moved to file time ``t``: show that frame (debounced), or, while
        playing, continue from there."""
        if self.file is None or self.timeline is None or t is None:
            return
        if self._driving:
            return                                   # our own tick coming back round
        self._locate(t)
        if self._playing:
            self._stop_at = None                     # the cursor was moved from outside: the interval no longer bounds playback
            if self._path is None:
                self.pause()
            else:
                self._anchor_wall = time.monotonic()
                self._anchor_t = t
            return
        self._request_frame(t, debounce=True)

    def _locate(self, t: float) -> None:
        """Resolve ``t`` to a block and its clip; show a message when there is no clip."""
        pos = self.timeline.locate(t)
        self._t = t
        block = pos.block
        if self._block is not None and block.index == self._block.index and self._path is not None:
            return
        self._block, self._path, self._clip = block, None, None
        rec = self.file.records[block.index]
        clip = rec.video
        if clip is None or not clip.has_video:
            self._show_text(f"No video for block {block.number}")
            self.caption.setText("")
        else:
            path = rec.video_file
            expected = os.path.join(self.file.movies_folder, clip.file_name)
            if path is None and os.path.exists(expected):
                self._show_text("Video file cannot be read")
                self.caption.setText(f"{clip.file_name} in {os.path.basename(self.file.movies_folder)} cannot be read "
                                     "(not downloaded from the cloud yet?)")
            elif path is None:
                self._show_text("Video file not found")
                self.caption.setText(f"Expected {clip.file_name} in {os.path.basename(self.file.movies_folder)}")
            elif self.grabber is None:
                self._show_text("OpenCV is not installed (pip install opencv-python-headless)")
                self.caption.setText(clip.file_name)
            else:
                self._path, self._clip = path, clip
        self._update_controls()

    def _request_frame(self, t: float, debounce: bool) -> None:
        if self._path is None or self._clip is None or self._block is None:
            return
        block_time = t - self._block.start_s
        file_time = self._clip.file_time(block_time)
        state = f" · playing {_rate_text(self.rate)}" if self._playing else ""
        timing = ("synchronised by the movie's frame stamps" if self._clip.stamped
                  else f"camera delay {self._clip.delay_s:.3f} s compensated")
        self._pending = (self._path, file_time,
                         f"Block {self._block.number} · {self._clip.file_name} · {file_time:.3f} s in the clip"
                         f" ({timing}){state}")
        if debounce:
            self._debounce.start()
        else:
            self._debounce.stop()
            self._fire()

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

    # -- playback -----------------------------------------------------------------------------
    def _update_controls(self) -> None:
        ok = self.has_clip
        for b in (self.btn_start, self.btn_play, self.btn_end, self.btn_slower, self.btn_faster):
            b.setEnabled(ok)
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if self._playing else QStyle.SP_MediaPlay))
        self.btn_play.setToolTip("Pause (Space)" if self._playing else "Play from the cursor (Space)")
        self.rate_label.setText(_rate_text(self.rate))

    def play(self, stop_at: Optional[float] = None) -> None:
        """Play from the current position; ``stop_at`` (file time) pauses there when it comes
        before the end of the block."""
        if not self.has_clip or self._block is None or self._playing:
            return
        t = self._t if self._t is not None else self._block.start_s
        self._stop_at = stop_at if stop_at is not None and stop_at > t else None
        if t >= self._block_end():
            t = self._block.start_s                  # play again from the start when at the end
            self._stop_at = None
        self._anchor_wall = time.monotonic()
        self._anchor_t = t
        self._playing = True
        self._clock.start()
        self._update_controls()
        self.playingChanged.emit(True)
        self._move_to(t, debounce=False)

    def pause(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._stop_at = None
        self._clock.stop()
        self._update_controls()
        self.playingChanged.emit(False)
        if self._t is not None:
            self._request_frame(self._t, debounce=False)

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def go_start(self) -> None:
        if self._block is None:
            return
        self.pause()
        self._move_to(self._block.start_s, debounce=False)

    def go_end(self) -> None:
        if self._block is None:
            return
        self.pause()
        self._move_to(self._block_end(), debounce=False)

    def slower(self) -> None:
        self._set_rate_index(self._rate_index - 1)

    def faster(self) -> None:
        self._set_rate_index(self._rate_index + 1)

    def set_rate(self, rate: float) -> None:
        if rate in RATES:
            self._set_rate_index(RATES.index(rate))

    def _set_rate_index(self, i: int) -> None:
        i = max(0, min(len(RATES) - 1, i))
        if i == self._rate_index:
            return
        if self._playing and self._t is not None:   # keep the position continuous across the change
            self._anchor_wall = time.monotonic()
            self._anchor_t = self._t
        self._rate_index = i
        self._update_controls()
        if self._t is not None and self._path is not None:
            self._request_frame(self._t, debounce=not self._playing)

    def _block_end(self) -> float:
        b = self._block
        return b.start_s + (b.ticks - 1) / b.tick_rate_hz if b is not None else 0.0

    def _play_end(self) -> float:
        """Where playback pauses: the block's end, or the interval's end when that is sooner."""
        end = self._block_end()
        return min(end, self._stop_at) if self._stop_at is not None else end

    def _tick(self) -> None:
        if not self._playing or self._block is None:
            return
        t = self._anchor_t + (time.monotonic() - self._anchor_wall) * self.rate
        end = self._play_end()
        if t >= end:
            self._move_to(end, debounce=False)
            self.pause()
            return
        self._move_to(t, debounce=False)

    def _move_to(self, t: float, debounce: bool) -> None:
        """Set the panel's own time, fetch the frame and tell the window to move the cursor."""
        self._t = t
        self._request_frame(t, debounce)
        self._driving = True
        try:
            self.positionChanged.emit(t)
        finally:
            self._driving = False
