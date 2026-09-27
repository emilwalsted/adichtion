"""Background work for the viewer: one queue-fed thread for everything that decodes
samples (rendering, cursor statistics, inference), and one for video frames.

Jobs are plain callables ``fn(stale, report)``: ``stale()`` tells a running job
that a newer job of the same kind has been queued (so it can stop early), and
``report(payload)`` posts a partial result to the GUI thread.  Results arrive
through Qt signals, always on the GUI thread; the receiver checks the job id
against the one it last submitted and drops anything older.
"""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal


class JobRunner(QThread):
    """A worker thread with a FIFO of jobs, coalesced per *kind*: when several jobs
    of one kind are queued, only the newest runs (the older ones are skipped)."""

    progress = Signal(int, str, object)          # job id, kind, payload
    done = Signal(int, str, object, object)      # job id, kind, result, error (str or None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q: "queue.Queue[Optional[tuple[int, str, Callable, bool]]]" = queue.Queue()
        self._latest: dict[str, int] = {}
        self._lock = threading.Lock()
        self._next = 0
        self._stopping = False

    def submit(self, kind: str, fn: Callable, coalesce: bool = True) -> int:
        """Queue ``fn(stale, report)``; returns its job id."""
        with self._lock:
            self._next += 1
            job_id = self._next
            if coalesce:
                self._latest[kind] = job_id
        self._q.put((job_id, kind, fn, coalesce))
        return job_id

    def is_current(self, kind: str, job_id: int) -> bool:
        with self._lock:
            if self._stopping:
                return False
            return self._latest.get(kind, job_id) == job_id

    def stop(self, timeout_ms: int = 60_000) -> bool:
        """Mark every job stale, let the running one wind down and join the thread."""
        with self._lock:
            self._stopping = True
        self._q.put(None)
        return self.wait(timeout_ms)

    def run(self) -> None:                       # pragma: no cover - exercised through the GUI tests
        while True:
            item = self._q.get()
            if item is None:
                return
            job_id, kind, fn, coalesce = item
            if self._stopping or (coalesce and not self.is_current(kind, job_id)):
                continue                          # superseded before it started (or shutting down)

            def stale(job_id=job_id, kind=kind, coalesce=coalesce) -> bool:
                return self._stopping or (coalesce and not self.is_current(kind, job_id))

            def report(payload, job_id=job_id, kind=kind) -> None:
                self.progress.emit(job_id, kind, payload)

            try:
                result = fn(stale, report)
                self.done.emit(job_id, kind, result, None)
            except Exception:                     # noqa: BLE001 - a job must never kill the thread
                self.done.emit(job_id, kind, None, traceback.format_exc())


class FrameGrabber(QThread):
    """Decodes one video frame at a time with OpenCV; only the latest request is served."""

    frame = Signal(int, object, float)           # request id, QImage (or None), actual time (s)
    error = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cv = threading.Condition()
        self._request: Optional[tuple[int, str, float]] = None
        self._stop = False
        self._cap = None
        self._cap_path: Optional[str] = None

    def request(self, request_id: int, path: str, time_s: float) -> None:
        with self._cv:
            self._request = (request_id, path, time_s)
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        self.wait(5000)

    def _open(self, path: str):
        import cv2                                 # noqa: PLC0415 - optional at import time
        if self._cap is None or self._cap_path != path:
            if self._cap is not None:
                self._cap.release()
            self._cap = cv2.VideoCapture(path)
            self._cap_path = path
        return self._cap

    def run(self) -> None:                       # pragma: no cover - needs a video file
        while True:
            with self._cv:
                while self._request is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    break
                request_id, path, time_s = self._request
                self._request = None
            try:
                img, actual = grab_frame(self._open(path), time_s)
                self.frame.emit(request_id, img, actual)
            except Exception as exc:              # noqa: BLE001
                self.error.emit(request_id, str(exc))
        if self._cap is not None:
            self._cap.release()


def grab_frame(cap, time_s: float):
    """The frame at ``time_s`` (seconds into the file) as a ``QImage``, plus the time the
    decoder actually landed on.  ``None`` when the file yields nothing there."""
    import cv2                                     # noqa: PLC0415
    from PySide6.QtGui import QImage

    if cap is None or not cap.isOpened():
        raise RuntimeError("the video file could not be opened")
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None, time_s
    actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return img, actual
