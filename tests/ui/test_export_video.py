"""The export dialog asks about a video clip, and the export writes it beside the data file."""
import os
import shutil

import pytest

from adichtion import AdichtFile, videoclip
from adichtion.video import movies_folder
from PySide6.QtWidgets import QFileDialog, QMessageBox
from adichtion.ui.export_dialog import ExportDialog
from helpers import wait_until

cv2 = pytest.importorskip("cv2")
pytestmark = pytest.mark.skipif(videoclip.find_ffmpeg() is None, reason="no ffmpeg program")
from test_videoclip import marker_video, read_clip, FPS          # noqa: E402  (tests/ is on the path)


@pytest.fixture(scope="module")
def video_document(documents, tmp_path_factory):
    """The v29 synthetic document with block 1's clip (an 8 s WMV; the block's clip starts 0.46 s in)."""
    folder = tmp_path_factory.mktemp("exportvideo")
    path = str(folder / "with_video.adicht")
    shutil.copy(documents["v29"]["path"], path)
    os.makedirs(movies_folder(path))
    marker_video(os.path.join(movies_folder(path), "Record1.wmv"), seconds=41)
    return path


def make_dialog(window, path, selection=(10.0, 12.0)):
    assert window.open_file_sync(path)
    window.stack.set_cursor(selection[0])
    window.stack.set_selection(*selection)
    dlg = ExportDialog(window.file, window.timeline, window.stack.visible_channels, window.stack.cursor,
                       window.stack.selection, window, settings=window.settings, default_scope="selection")
    return dlg


def test_the_video_choice_appears_only_when_the_interval_has_video(qapp, window, video_document, documents):
    dlg = make_dialog(window, video_document)
    assert not dlg.video_box.isHidden() and not dlg.cb_video.isChecked()
    assert dlg.video_mode() is None and dlg.rb_vid_h264.isChecked()
    dlg.rb_block.setChecked(False)
    dlg.deleteLater()
    # block 2 has no clip: nothing to ask about
    window.close_file()
    dlg = make_dialog(window, video_document, selection=(41.0, 43.0))
    assert dlg.video_box.isHidden() and dlg.video_mode() is None
    dlg.deleteLater()
    window.close_file()
    dlg = make_dialog(window, documents["v26"]["path"])
    assert dlg.video_box.isHidden()
    dlg.deleteLater()


def test_ticking_the_box_puts_the_clip_beside_the_data_file(qapp, window, video_document, tmp_path, monkeypatch):
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    assert "Video clip: named like the data file, with .mp4" in dlg.note.text()
    dlg.rb_vid_original.setChecked(True)
    assert "with .wmv" in dlg.note.text() and "key frame" in dlg.note.text()
    target = str(tmp_path / "interval.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    dlg._accept()
    req = dlg.request
    assert req.video_mode == "original" and req.video_path == str(tmp_path / "interval.wmv")
    assert len(req.video_segments) == 1 and req.video_missing == []
    assert dlg._get("video_mode") == "original"                       # the kind of clip is remembered
    dlg.deleteLater()
    dlg2 = make_dialog(window, video_document)
    assert dlg2.rb_vid_original.isChecked() and not dlg2.cb_video.isChecked()   # ... the box is not
    dlg2.deleteLater()


def test_an_existing_clip_is_only_replaced_when_the_user_says_so(qapp, window, video_document, tmp_path, monkeypatch):
    (tmp_path / "interval.mp4").write_bytes(b"old")
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "interval.txt"), "")))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    dlg._accept()
    assert dlg.request is None
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg._accept()
    assert dlg.request is not None and dlg.request.video_path == str(tmp_path / "interval.mp4")
    dlg.deleteLater()


@pytest.mark.parametrize("mode, ext", [("h264", ".mp4"), ("original", ".wmv")])
def test_the_export_writes_data_and_clip_with_the_same_name(qapp, window, video_document, tmp_path, monkeypatch, mode, ext):
    dlg = make_dialog(window, video_document, selection=(10.0, 12.0))
    dlg.cb_video.setChecked(True)
    (dlg.rb_vid_original if mode == "original" else dlg.rb_vid_h264).setChecked(True)
    target = str(tmp_path / "interval.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    dlg._accept()
    req = dlg.request
    dlg.deleteLater()
    window.run_export(req)
    clip = str(tmp_path / ("interval" + ext))
    assert wait_until(qapp, lambda: window._export_cancel is None and os.path.exists(clip)
                      and "Exported" in window.statusBar().currentMessage(), timeout=60)
    assert "Video clip: interval" + ext in window.statusBar().currentMessage()
    assert os.path.exists(target) and sorted(os.listdir(tmp_path)) == sorted(["interval.txt", "interval" + ext])
    first, n, _fps = read_clip(clip)
    want = round((0.46 + 10.0) * FPS)                                  # the clip starts 0.46 s into the file
    if mode == "h264":
        assert abs(first - want) <= 1 and abs(n - 2 * FPS) <= 2
    else:
        assert first <= want and first + n >= want + 2 * FPS - 2


def test_a_cancelled_export_leaves_neither_file(qapp, window, video_document, tmp_path, monkeypatch):
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    target = str(tmp_path / "interval.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    dlg._accept()
    req = dlg.request
    dlg.deleteLater()
    from adichtion.ui import main_window as mw
    real = videoclip.write_clip

    def cancelling(*a, **k):                      # the user presses Cancel as the clip starts
        window._export_cancel.set()
        return real(*a, **k)

    monkeypatch.setattr(videoclip, "write_clip", cancelling)
    window.run_export(req)
    assert wait_until(qapp, lambda: window._export_cancel is None, timeout=60)
    assert window.statusBar().currentMessage() == "Export cancelled." and os.listdir(tmp_path) == []
    assert mw is not None


def test_a_clip_that_fails_does_not_stop_the_data_export(qapp, window, video_document, tmp_path, monkeypatch):
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    target = str(tmp_path / "interval.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    dlg._accept()
    req = dlg.request
    dlg.deleteLater()
    monkeypatch.setattr(videoclip, "find_ffmpeg", lambda: str(tmp_path / "no-such-ffmpeg"))
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append((self.text(), self.informativeText())) or 0)
    window.run_export(req)
    assert wait_until(qapp, lambda: window._export_cancel is None and shown, timeout=60)
    assert os.listdir(tmp_path) == ["interval.txt"]
    assert "could not be made" in shown[0][0] and "could not be started" in shown[0][1]
    assert "The video clip was not made" in window.statusBar().currentMessage()


def test_without_ffmpeg_the_box_explains_itself(qapp, window, video_document, monkeypatch):
    monkeypatch.setattr(videoclip, "find_ffmpeg", lambda: None)
    dlg = make_dialog(window, video_document)
    assert not dlg.video_box.isHidden() and not dlg.cb_video.isEnabled()
    assert "ffmpeg program was not found" in dlg.cb_video.text() and dlg.video_mode() is None
    dlg.deleteLater()


def test_a_clip_that_cannot_take_its_name_is_a_warning_and_the_data_stays(qapp, window, video_document, tmp_path, monkeypatch):
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    target = str(tmp_path / "interval.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    dlg._accept()
    req = dlg.request
    dlg.deleteLater()
    os.makedirs(req.video_path)                            # a folder appears where the clip should go
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.informativeText()) or 0)
    window.run_export(req)
    assert wait_until(qapp, lambda: window._export_cancel is None and shown, timeout=60)
    assert os.path.isfile(target) and os.path.isdir(req.video_path) and "could not be saved" in shown[0]
    assert not any(n.endswith(".part.mp4") for n in os.listdir(tmp_path))


def test_a_data_name_that_is_the_original_videos_is_refused(qapp, window, video_document, monkeypatch):
    dlg = make_dialog(window, video_document)
    dlg.cb_video.setChecked(True)
    dlg.rb_vid_original.setChecked(True)
    source = os.path.join(movies_folder(video_document), "Record1.txt")       # its clip would be Record1.wmv
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (source, "")))
    dlg._accept()
    assert dlg.request is None and "original video file" in dlg.note.text()
    dlg.deleteLater()
