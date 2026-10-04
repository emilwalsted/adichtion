"""The video panel: frames at the cursor, playback with the cursor following, rates."""
import os
import time
import shutil

import numpy as np
import pytest

from adichtion.video import movies_folder
from helpers import pump, wait_until

cv2 = pytest.importorskip("cv2")

FPS = 25
SECONDS = 41            # the synthetic document's block 1 lasts 40 s; its clip starts 0.46 s in


BITS = 11               # 2**11 > 25 * 41 frames


def write_test_video(path: str) -> None:
    """An MJPG stream (OpenCV/FFmpeg probe the content, the .wmv name is cosmetic) whose
    frame number is written in binary as a row of white squares, so a frame can be identified
    exactly despite the compression."""
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (320, 240))
    assert w.isOpened()
    for i in range(FPS * SECONDS):
        frame = np.full((240, 320, 3), 40, np.uint8)
        for k in range(BITS):
            if i >> k & 1:
                frame[100:124, 10 + 26 * k: 30 + 26 * k] = 255
        w.write(frame)
    w.release()


def frame_number(qimage) -> int:
    from PySide6.QtGui import QImage
    qimage = qimage.convertToFormat(QImage.Format_RGB888)
    ptr = qimage.constBits()
    arr = np.frombuffer(ptr, np.uint8).reshape(qimage.height(), qimage.bytesPerLine())
    n = 0
    for k in range(BITS):
        if arr[112, (20 + 26 * k) * 3] > 128:
            n |= 1 << k
    return n


@pytest.fixture(scope="module")
def video_document(documents, tmp_path_factory):
    """The v29 synthetic document copied next to a movies folder holding block 1's clip."""
    folder = tmp_path_factory.mktemp("video")
    src = documents["v29"]["path"]
    path = str(folder / "with_video.adicht")
    shutil.copy(src, path)
    movies = movies_folder(path)
    os.makedirs(movies)
    write_test_video(os.path.join(movies, "Record1.wmv"))
    return path


def test_grab_frame_seeks_reads_forward_and_survives_the_tail(qapp, video_document):
    from adichtion.ui.workers import grab_frame
    cap = cv2.VideoCapture(os.path.join(movies_folder(video_document), "Record1.wmv"))
    img, t = grab_frame(cap, 4.0)
    assert t == pytest.approx(4.0) and frame_number(img) == 100
    img, t = grab_frame(cap, 4.08, last_pos=t)           # two frames on: read forward
    assert t == pytest.approx(4.08) and frame_number(img) == 102
    img, t = grab_frame(cap, 2.0, last_pos=t)            # backwards: a seek
    assert t == pytest.approx(2.0) and frame_number(img) == 50
    img, t = grab_frame(cap, SECONDS - 0.01, last_pos=t)  # the tail, where a seek fails
    assert img is not None and frame_number(img) >= FPS * SECONDS - 3
    img, t = grab_frame(cap, SECONDS + 30.0)             # beyond the end: the last frame
    assert img is not None and frame_number(img) >= FPS * SECONDS - 3
    img, t = grab_frame(cap, 0.0, last_pos=t)
    assert t == 0.0 and frame_number(img) == 0


def test_frame_follows_the_cursor(qapp, window, video_document):
    window.open_file_sync(video_document)
    v = window.video
    assert window.video_box.isVisible() and v.has_videos and not v.has_clip
    window.stack.set_cursor(5.0)
    assert v.has_clip and v.btn_play.isEnabled()
    assert wait_until(qapp, lambda: v._pixmap is not None, timeout=20)
    # the clip starts 0.46 s into the file (the camera delay already in the offset)
    assert frame_number(v._pixmap.toImage()) == pytest.approx(round((0.46 + 5.0) * FPS), abs=1)
    assert "5.460 s in the clip" in v.caption.text()
    window.stack.set_cursor(42.5)                         # block 2 has no clip
    pump(qapp, 0.2)
    assert not v.has_clip and not v.btn_play.isEnabled() and "No video for block 2" in v.frame.text()


def test_playback_moves_the_cursor_and_the_panels(qapp, window, video_document):
    window.open_file_sync(video_document)
    v = window.video
    window.stack.set_cursor(5.0)
    started = time.monotonic()
    v.play()
    assert v.playing and window.act_play.text() == "Pause video"
    pump(qapp, 0.6)
    t1 = window.stack.cursor
    # The clock is wall time × rate, read at the last 40 ms tick: measure the wall time
    # instead of assuming the pump took 0.6 s (a slow CI runner overran it by 30 %).
    elapsed = time.monotonic() - started
    assert 0.3 <= t1 - 5.0 <= elapsed + 0.05 and t1 - 5.0 >= elapsed - 0.15
    assert window.readout.text().startswith("Block 1 · 5.")
    assert window.data.table.item(0, 0) is not None and window.data.where.text().startswith("Cursor: Block 1 · 5.")
    assert "playing 1×" in v.caption.text()
    v.pause()
    assert not v.playing and window.act_play.text() == "Play video"
    pump(qapp, 0.2)
    assert window.stack.cursor == t1
    assert wait_until(qapp, lambda: v._pixmap is not None and abs(frame_number(v._pixmap.toImage()) - (0.46 + t1) * FPS) <= 2, timeout=20)
    # faster: four times the distance in the same time (rates step 1 -> 2 -> 4)
    v.faster(); v.faster()
    assert v.rate == 4.0 and v.rate_label.text() == "4×"
    started = time.monotonic()
    v.play()
    pump(qapp, 0.5)
    v.pause()
    elapsed = time.monotonic() - started
    moved = window.stack.cursor - t1
    assert 1.2 <= moved <= 4 * elapsed + 0.05 and moved >= 4 * (elapsed - 0.15)
    v.slower(); v.slower(); v.slower()
    assert v.rate == 0.5
    # start, end and playing again from the end
    v.go_end()
    assert not v.playing and window.stack.cursor == pytest.approx(39.999)
    assert wait_until(qapp, lambda: v._pixmap is not None and frame_number(v._pixmap.toImage()) >= round((0.46 + 39.9) * FPS), timeout=20)
    v.go_start()
    assert window.stack.cursor == 0.0
    v.go_end()
    v.play()
    pump(qapp, 0.2)
    assert v.playing and window.stack.cursor < 1.0        # restarted from the beginning
    # a click elsewhere while playing continues from there
    window.stack.set_cursor(20.0)
    pump(qapp, 0.3)
    assert v.playing and 20.0 <= window.stack.cursor <= 20.4
    v.pause()


def test_playback_stops_at_the_end_of_the_block(qapp, window, video_document):
    window.open_file_sync(video_document)
    v = window.video
    v.set_rate(8.0)
    window.stack.set_cursor(39.0)
    v.play()
    assert wait_until(qapp, lambda: not v.playing, timeout=10)
    assert window.stack.cursor == pytest.approx(39.999)
    assert not v.playing and "Paused" in window.statusBar().currentMessage()


def test_view_pages_along_with_the_playing_cursor(qapp, window, video_document):
    window.open_file_sync(video_document)
    window.stack.set_range(0.0, 2.0)
    window.stack.set_cursor(1.9)
    window.video.set_rate(2.0)
    window.video.play()
    pump(qapp, 0.4)
    window.video.pause()
    x0, x1 = window.stack.view_range()
    assert x0 < window.stack.cursor < x1 and x0 > 0.5 and (x1 - x0) == pytest.approx(2.0, abs=0.01)
