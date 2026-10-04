"""The viewer on the synthetic documents (headless Qt)."""
import os
import time

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.export import read_labchart_text
from PySide6.QtWidgets import QMessageBox, QPushButton

from adichtion.ui.export_dialog import ExportDialog, ExportRequest
from helpers import pump, wait_until


def test_open_shows_every_channel_and_the_panels(qapp, window, synthetic):
    assert window.open_file_sync(synthetic["path"])
    assert [p.index for p in window.stack.plots] == [0, 1, 2, 3]
    assert window.stack.visible_channels == [0, 1, 2, 3]
    assert window.stack.view_range() == pytest.approx((0.0, 45.0))
    assert window.video_box.isVisible()                      # the document has a video stream
    assert "not found" in window.video.frame.text() or window.video.frame.text() == "Place the cursor to see the frame"
    assert window.info.blocks.rowCount() == 2 and window.info.channels.rowCount() == 4
    assert window.info.comments.rowCount() == 4
    assert window.info.channels.item(2, 2).text() == "calculated"
    assert window.windowTitle().endswith(os.path.basename(synthetic["path"]))
    # the calculated channels are marked and, once inferred, described
    assert window.stack.plots[2].is_calculated and not window.stack.plots[0].is_calculated
    assert window.stack.plots[2].calc_label.isVisible() and not window.stack.plots[0].calc_label.isVisible()
    assert wait_until(qapp, lambda: len(window.definitions) == 2, timeout=60)
    assert window.stack.plots[3].definition
    # every plot got a decoded curve at pixel resolution
    assert wait_until(qapp, lambda: all(p.level == "envelope" for p in window.stack.plots), timeout=20)


def test_cursor_and_selection_drive_the_data_panel(qapp, window, synthetic):
    window.open_file_sync(synthetic["path"])
    window.stack.set_cursor(42.5)
    pump(qapp, 0.05)
    assert window.readout.text().startswith("Block 2 · 2.500 s")
    assert window.data.table.rowCount() == 4
    with AdichtFile(synthetic["path"]) as f:
        expected = float(f.records[1].read(1, 2500, 2501)[0])
    assert float(window.data.table.item(1, 1).text()) == pytest.approx(expected, rel=1e-4)
    assert "Pmo" in window.stack.plots[1].label.text() and f"{expected:.5g}"[:4] in window.stack.plots[1]._label_text
    # Shift+click semantics: an interval from the cursor
    window.stack.set_selection(42.5, 44.0)
    assert window.stack.selection == (42.5, 44.0)
    assert wait_until(qapp, lambda: window.data.table.item(1, 3) is not None and window.data.table.item(1, 3).text() != "…")
    with AdichtFile(synthetic["path"]) as f:
        ref = f.records[1].read(1, 2500, 4000)
    assert float(window.data.table.item(1, 1).text()) == pytest.approx(ref.min(), rel=1e-4)
    assert float(window.data.table.item(1, 3).text()) == pytest.approx(ref.mean(), rel=1e-3)
    window.stack.clear_selection()
    assert window.stack.selection is None and window.stack.cursor == 42.5
    # the export dialog offers the selection only while there is one
    dlg = ExportDialog(window.file, window.timeline, window.stack.visible_channels, 42.5, None, window)
    assert not dlg.rb_sel.isEnabled() and dlg.rb_block.isChecked()
    assert [(s.record, s.start, s.stop) for s in dlg.selections()] == [(1, 0, 5000)]
    dlg.deleteLater()


def test_zoom_and_channel_selection(qapp, window, synthetic):
    window.open_file_sync(synthetic["path"])
    window.stack.set_cursor(20.0)
    window.stack.zoom_in()
    x0, x1 = window.stack.view_range()
    assert x1 - x0 == pytest.approx(22.5) and x0 < 20.0 < x1
    window.stack.zoom_out()
    assert window.stack.view_range() == pytest.approx((0.0, 45.0))
    window.stack.set_range(10.0, 10.5)
    assert wait_until(qapp, lambda: all(p.level == "samples" for p in window.stack.plots))
    window.stack.zoom_to_fit()
    lo, hi = window.stack.plots[1].vb.viewRange()[1]
    with AdichtFile(synthetic["path"]) as f:
        seg = f.records[0].read(1, 10000, 10500)
    assert lo < seg.min() < seg.max() < hi and (hi - lo) < 1.5 * (seg.max() - seg.min()) + 1e-9
    window.stack.zoom_reset()
    assert window.stack.view_range() == pytest.approx((0.0, 45.0))
    # show a subset, then all again
    window.stack.set_visible_channels([2, 0])
    pump(qapp, 0.05)
    assert window.stack.visible_channels == [0, 2]
    assert window.stack.plots[1].parent() is not window.stack.splitter
    assert window.stack.plots[2].item.getAxis("bottom").style["showValues"]
    assert not window.stack.plots[0].item.getAxis("bottom").style["showValues"]
    window.show_all_channels()
    pump(qapp, 0.05)
    assert window.stack.visible_channels == [0, 1, 2, 3]
    sizes = window.stack.splitter.sizes()
    assert max(sizes) - min(sizes) <= 1


def test_exports_write_the_selection(qapp, window, synthetic, tmp_path):
    window.open_file_sync(synthetic["path"])
    window.stack.set_cursor(12.0)
    window.stack.set_selection(12.0, 13.0)
    sels = window.timeline.selections(12.0, 13.0)
    for fmt in ("txt", "csv", "xlsx"):
        if fmt == "xlsx":
            pytest.importorskip("openpyxl")
        path = str(tmp_path / f"out.{fmt}")
        window.run_export(ExportRequest(path=path, fmt=fmt, selections=sels, channels=[0, 1], scope_label="selection"))
        assert wait_until(qapp, lambda: window._export_cancel is None and os.path.exists(path) and "Exported" in window.statusBar().currentMessage(), timeout=30)
        assert "1,000 rows" in window.statusBar().currentMessage()
    header, t, vals, comments = read_labchart_text(str(tmp_path / "out.txt"))
    assert len(t) == 1000 and vals.shape == (1000, 2) and t[0] == pytest.approx(12.0)
    with AdichtFile(synthetic["path"]) as f:
        assert np.allclose(vals[:, 1], f.records[0].read(1, 12000, 13000), atol=1e-6)
    lines = open(tmp_path / "out.csv", encoding="utf-8").read().splitlines()
    assert len(lines) == 1001 and lines[0].startswith("time_s,")


def _slow_export(monkeypatch, gate_after: int):
    """Chunks of 100 rows, and the worker waits at chunk ``gate_after`` until ``gate`` is set.
    Returns ``(gate, reached)``: ``reached`` is set when the worker is waiting."""
    import threading
    from adichtion import export as ex
    monkeypatch.setattr(ex, "CHUNK_ROWS", 100)
    gate, reached, real, calls = threading.Event(), threading.Event(), ex._text_rows, []

    def gated(*a, **k):
        calls.append(1)
        if len(calls) == gate_after:
            reached.set()
            assert gate.wait(20)
        return real(*a, **k)

    monkeypatch.setattr(ex, "_text_rows", gated)
    return gate, reached


def test_the_export_shows_progress(qapp, window, synthetic, tmp_path, monkeypatch):
    window.open_file_sync(synthetic["path"])
    gate, reached = _slow_export(monkeypatch, 5)
    path = str(tmp_path / "p.csv")
    window.run_export(ExportRequest(path=path, fmt="csv", selections=window.timeline.selections(0.0, 10.0), channels=[0, 1],
                                    scope_label="selection"))
    assert wait_until(qapp, reached.is_set, timeout=20)
    assert wait_until(qapp, lambda: window._export_dialog is not None and window._export_dialog.value() > 0, timeout=10)
    assert "%" in window._export_dialog.labelText() and not os.path.exists(path)      # written under another name until done
    assert os.path.exists(path + ".part")
    gate.set()
    assert wait_until(qapp, lambda: "Exported 10,000 rows" in window.statusBar().currentMessage(), timeout=30)
    assert window._export_dialog is None and os.path.exists(path) and not os.path.exists(path + ".part")


def test_the_export_can_be_cancelled_and_leaves_no_file(qapp, window, synthetic, tmp_path, monkeypatch):
    window.open_file_sync(synthetic["path"])
    gate, reached = _slow_export(monkeypatch, 5)
    path = str(tmp_path / "c.txt")
    window.run_export(ExportRequest(path=path, fmt="txt", selections=window.timeline.selections(0.0, 10.0), channels=[0, 1],
                                    scope_label="selection"))
    assert wait_until(qapp, reached.is_set, timeout=20)
    window._export_dialog.findChild(QPushButton).click()                     # the Cancel button
    gate.set()
    assert wait_until(qapp, lambda: window.statusBar().currentMessage() == "Export cancelled.", timeout=30)
    assert window._export_dialog is None and not os.path.exists(path) and not os.path.exists(path + ".part")
    # and the next export works as before
    window.run_export(ExportRequest(path=path, fmt="txt", selections=window.timeline.selections(0.0, 1.0), channels=[0],
                                    scope_label="selection"))
    assert wait_until(qapp, lambda: "Exported 1,000 rows" in window.statusBar().currentMessage(), timeout=30)


def test_a_failed_export_still_reports_and_leaves_the_old_file(qapp, window, synthetic, tmp_path, monkeypatch):
    window.open_file_sync(synthetic["path"])
    path = tmp_path / "f.csv"
    path.write_text("old")
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    window.run_export(ExportRequest(path=str(path), fmt="csv", selections=window.timeline.selections(0.0, 1.0), channels=[99],
                                    scope_label="selection"))
    assert wait_until(qapp, lambda: window.statusBar().currentMessage() == "Export failed.", timeout=30)
    assert path.read_text() == "old" and not os.path.exists(str(path) + ".part")


def test_mac_settings_fill_the_definitions(qapp, window, synthetic, tmp_path):
    from test_macchart import settings_blob, write_res_file
    window.open_file_sync(synthetic["path"])
    path = tmp_path / "settings"
    write_res_file(path, [("Mcro", 30001, settings_blob())])
    window.load_mac_settings(str(path))
    # channel 6 of the settings is beyond this 4-channel file; nothing crashes and the message says so
    assert "0 calculated channels" in window.statusBar().currentMessage()


def test_time_scroll_bar_follows_and_moves_the_view(qapp, window, synthetic):
    window.open_file_sync(synthetic["path"])
    pump(qapp, 0.2)
    bar = window.stack.hbar
    d = window.stack.timeline.duration_s
    assert not bar.isEnabled()                                  # the whole file is in view
    window.stack.set_range(10.0, 20.0)
    pump(qapp, 0.1)
    assert bar.isEnabled()
    assert bar.value() / window.stack._hbar_scale == pytest.approx(10.0, abs=1e-3)
    assert bar.pageStep() / window.stack._hbar_scale == pytest.approx(10.0, abs=1e-3)
    bar.setValue(bar.maximum())                                 # dragged to the end: same zoom, last stretch
    pump(qapp, 0.1)
    x0, x1 = window.stack.view_range()
    assert x1 - x0 == pytest.approx(10.0, abs=1e-3) and x1 == pytest.approx(d, abs=1e-3)
    window.stack.zoom_reset()
    pump(qapp, 0.1)
    assert not bar.isEnabled()
    window.stack.set_range(30.0, 35.0)
    pump(qapp, 0.1)
    assert bar.isEnabled()
    window.close_file()
    assert not window.stack.hbar.isVisible() and not bar.isEnabled()      # no stale state for the next file


def test_open_dialog_filter_decides_what_the_file_is_for(qapp, window, synthetic, monkeypatch):
    """One Open dialog: the file-type filter picked routes the file to the recording reader or to
    the Mac settings reader (the separate menu item is gone)."""
    from PySide6.QtWidgets import QFileDialog
    from adichtion.ui import main_window as mw
    assert not hasattr(window, "act_mac")
    calls = []
    monkeypatch.setattr(window, "open_file", lambda p: calls.append(("open", p)))
    monkeypatch.setattr(window, "load_mac_settings", lambda p=None: calls.append(("settings", p)))
    for chosen, want in ((mw.DOCUMENT_FILTER, "open"), (mw.MAC_DOCUMENT_FILTER, "open"),
                         (mw.MAC_SETTINGS_FILTER, "settings")):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, c=chosen, **k: ("/x/f", c)))
        window.open_dialog()
        assert calls[-1] == (want, "/x/f")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    n = len(calls)
    window.open_dialog()
    assert len(calls) == n


def test_settings_file_without_an_open_recording_asks_for_one(qapp, window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    window.load_mac_settings("/x/settings")
    assert shown == ["Open a recording first"]


def test_open_failure_is_reported_not_raised(qapp, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton
    bad = tmp_path / "bad.adicht"
    bad.write_bytes(b"not a labchart file at all" * 100)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    assert window.open_file_sync(str(bad)) is False
    assert window.file is None and not window.act_export.isEnabled()


def test_every_layout_variant_opens(qapp, window, documents):
    for name, doc in documents.items():
        assert window.open_file_sync(doc["path"]), name
        assert len(window.stack.plots) == 4
        pump(qapp, 0.05)
    window.close_file()
    assert window.stack.plots == [] and window.info.name.text() == "No file open"


def test_app_main_opens_the_file_from_the_command_line(qapp, synthetic, monkeypatch):
    """The splash comes first and the window is revealed after MIN_SPLASH_MS with the file
    open; quitting waits for that (Qt ignores quit() while the modal splash is up). The
    application icon goes through _set_application_icon (tests/ui/test_logo.py)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QSplashScreen
    import adichtion.ui.app as appmod
    icon_calls = []
    real_set_icon = appmod._set_application_icon
    monkeypatch.setattr(appmod, "_set_application_icon",
                        lambda app: icon_calls.append(app) or real_set_icon(app))
    seen = {"splash": False}
    t0 = time.monotonic()

    def poll():
        tops = [w for w in qapp.topLevelWidgets() if w.isVisible()]
        seen["splash"] |= any(isinstance(w, QSplashScreen) for w in tops)
        wins = [w for w in tops if w.__class__.__name__ == "MainWindow"]
        if (not wins or wins[0].file is None) and time.monotonic() - t0 < 15:      # the file opens on a worker thread
            QTimer.singleShot(50, poll)
            return
        seen["n"] = len(wins)
        seen["file"] = os.path.basename(wins[0].file.path) if wins and wins[0].file else None
        seen["revealed_ms"] = int((time.monotonic() - t0) * 1000)
        for w in wins:
            w.close()
            w.deleteLater()
        qapp.quit()

    QTimer.singleShot(50, poll)
    assert appmod.main(["adichtion", synthetic["path"]]) == 0
    assert seen["splash"] and seen["n"] == 1 and seen["file"] == os.path.basename(synthetic["path"])
    assert seen["revealed_ms"] >= appmod.MIN_SPLASH_MS - 50
    assert not any(isinstance(w, QSplashScreen) and w.isVisible() for w in qapp.topLevelWidgets())
    assert appmod.startup_path(["x", "nope.adicht"]) is None
    assert icon_calls == [qapp]


def test_cursor_keys_move_by_sample_and_second(qapp, window, synthetic):
    window.open_file_sync(synthetic["path"])
    window.stack.set_range(0.0, 2.0)
    window.stack.set_cursor(1.0)
    window.act_step_fwd.trigger()
    assert window.stack.cursor == pytest.approx(1.001)
    window.act_sec_fwd.trigger()
    assert window.stack.cursor == pytest.approx(2.001)
    x0, x1 = window.stack.view_range()
    assert x0 < 2.001 < x1                                # the view followed the cursor off its edge
    window.act_sec_back.trigger()
    window.act_step_back.trigger()
    assert window.stack.cursor == pytest.approx(1.0)


def test_units_label_the_y_axis(qapp, window, synthetic):
    window.open_file_sync(synthetic["path"])
    pmo = window.stack.plots[1]
    left = pmo.item.getAxis("left")
    assert left.labelText == "cmH2O" and left.isVisible()
    assert window.stack.plots[0].item.getAxis("left").labelText == "V"
    # the tick values stay in the channel's units: no SI rescaling of a cmH2O axis
    assert left.autoSIPrefix is False and left.labelUnits == ""
    # a row too short for the rotated unit hides it (the overlay still names the unit)
    window.stack.set_visible_channels([0, 1, 2, 3])
    pmo.resize(pmo.width(), 40)
    pmo._place_overlays()
    assert not left.label.isVisible()
    pmo.resize(pmo.width(), 160)
    pmo._place_overlays()
    assert left.label.isVisible()
    assert "cmH2O" in pmo._label_text


def test_a_second_export_waits_and_closing_the_file_cancels_a_running_one(qapp, window, synthetic, tmp_path, monkeypatch):
    window.open_file_sync(synthetic["path"])
    gate, reached = _slow_export(monkeypatch, 5)
    path = str(tmp_path / "w.csv")
    req = ExportRequest(path=path, fmt="csv", selections=window.timeline.selections(0.0, 10.0), channels=[0], scope_label="selection")
    window.run_export(req)
    assert wait_until(qapp, reached.is_set, timeout=20)
    window.run_export(req)
    assert window.statusBar().currentMessage() == "An export is already running."
    window.close_file()
    gate.set()
    assert wait_until(qapp, lambda: window.statusBar().currentMessage() == "Export cancelled." or window._export_cancel is None, timeout=30)
    assert not os.path.exists(path) and not os.path.exists(path + ".part")
