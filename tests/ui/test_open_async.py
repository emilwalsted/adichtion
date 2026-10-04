"""Opening a file runs on a worker thread: the event loop keeps answering, a newer open wins, a cancelled or
failed open leaves the window as it was, and a failure still shows the "Could not open the file" box."""
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QPushButton

from adichtion.ui import main_window as mw
from helpers import pump, wait_until


@pytest.fixture
def slow_open(monkeypatch):
    """Make AdichtFile's constructor slow (a big file) and record the files it made and the ones closed unused."""
    real = mw.AdichtFile
    class Made(list):
        started = None

    made = Made()
    started = made.started = []

    def factory(path, delay=0.6):
        started.append(path)
        time.sleep(delay)
        f = real(path)
        orig_close = f.close
        f.closed = False

        def close():
            f.closed = True
            orig_close()
        f.close = close
        made.append(f)
        return f

    monkeypatch.setattr(mw, "AdichtFile", factory)
    return made


def test_the_event_loop_keeps_answering_while_a_file_opens(qapp, window, synthetic, slow_open):
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    t0 = time.monotonic()
    assert window.open_file(synthetic["path"]) is True
    assert time.monotonic() - t0 < 0.3                       # it returned at once, not after the 0.6 s open
    assert window.file is None
    assert wait_until(qapp, lambda: window.file is not None, timeout=20)
    timer.stop()
    assert len(ticks) >= 4                                    # ~30 expected over 0.6 s; none if the GUI thread had blocked
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.5
    assert window._open_dialog is None and window._open_job == 0
    assert [p.index for p in window.stack.plots] == [0, 1, 2, 3]
    assert window.statusBar().currentMessage().startswith("Opened ")
    assert window.recent_files() == [synthetic["path"]]


def test_the_opening_dialog_shows_for_a_slow_open_and_can_cancel_it(qapp, window, synthetic, slow_open):
    window.open_file(synthetic["path"])
    assert wait_until(qapp, lambda: window._open_dialog is not None and window._open_dialog.isVisible(), timeout=5)
    assert "Opening" in window._open_dialog.labelText()
    window._open_dialog.findChild(QPushButton).click()        # the Cancel button
    assert window._open_dialog is None and window.statusBar().currentMessage() == "Open cancelled."
    assert wait_until(qapp, lambda: len(slow_open) == 1, timeout=10)
    pump(qapp, 0.2)
    assert window.file is None and slow_open[0].closed        # the file that arrived late was closed unused
    assert window.recent_files() == []


def test_a_newer_open_replaces_the_one_running(qapp, window, documents, slow_open):
    a, b = documents["v29"]["path"], documents[next(k for k in documents if k != "v29")]["path"]
    window.open_file(a)
    assert wait_until(qapp, lambda: slow_open.started == [a], timeout=5)
    window.open_file(b)                                       # a is still being read: the newest request wins
    assert wait_until(qapp, lambda: window.file is not None, timeout=20)
    pump(qapp, 1.0)
    assert window.file.path == b
    assert [f.path for f in slow_open if not f.closed] == [b]
    assert [f.path for f in slow_open if f.closed] == [a]
    assert window.recent_files() == [b]


def test_a_file_that_cannot_be_read_gives_the_error_box(qapp, window, tmp_path, monkeypatch):
    bad = tmp_path / "bad.adicht"
    bad.write_bytes(b"not a labchart file at all" * 100)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append((self.windowTitle(), self.text())) or 0)
    assert window.open_file(str(bad)) is True                 # started; the failure comes with the result
    assert wait_until(qapp, lambda: bool(shown), timeout=10)
    assert shown[0][0] == "Could not open the file" and "bad.adicht could not be read" in shown[0][1]
    assert window.file is None and not window.act_export.isEnabled()
    assert window._open_dialog is None and window._open_job == 0
    assert window.recent_files() == []


def test_a_failed_open_keeps_the_open_file(qapp, window, synthetic, tmp_path, monkeypatch):
    assert window.open_file_sync(synthetic["path"])
    bad = tmp_path / "bad.adicht"
    bad.write_bytes(b"x" * 500)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    window.open_file(str(bad))
    assert wait_until(qapp, lambda: window._open_job == 0, timeout=10)
    assert window.file is not None and window.file.path == synthetic["path"]


def test_closing_the_window_while_a_file_opens_is_clean(qapp, synthetic, slow_open):
    w = mw.MainWindow()
    w.show()
    qapp.processEvents()
    w.open_file(synthetic["path"])
    assert wait_until(qapp, lambda: slow_open.started, timeout=5)      # the job is running when the window closes
    w.close()                                                 # joins the threads; the late result is not installed
    w.deleteLater()
    pump(qapp, 0.3)
    assert w.file is None
    assert len(slow_open) == 1 and slow_open[0].closed


def test_close_file_cancels_a_pending_open(qapp, window, synthetic, slow_open):
    window.open_file(synthetic["path"])
    window.close_file()
    assert wait_until(qapp, lambda: len(slow_open) == 1, timeout=10)
    pump(qapp, 0.2)
    assert window.file is None and slow_open[0].closed


def test_the_recent_menu_opens_through_the_same_path(qapp, window, synthetic, monkeypatch):
    window.open_file_sync(synthetic["path"])
    window.close_file()
    calls = []
    real_open = window.open_file
    monkeypatch.setattr(window, "open_file", lambda p: calls.append(p) or real_open(p))
    window._rebuild_recent()
    window.recent_menu.actions()[0].trigger()
    assert calls == [synthetic["path"]]
    assert wait_until(qapp, lambda: window.file is not None, timeout=10)


def test_export_and_settings_wait_for_a_file_that_is_opening(qapp, window, synthetic, slow_open, monkeypatch):
    window.open_file(synthetic["path"])
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    window.load_mac_settings("/x/settings")
    assert not shown and window.statusBar().currentMessage() == "Wait for the file to open first."
    assert wait_until(qapp, lambda: window.file is not None, timeout=20)
