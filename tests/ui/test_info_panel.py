"""The file panel's tables: lazy (model/view) comment and block lists, and the clip count off the GUI thread."""
import os
import time

import numpy as np
import pytest

from PySide6.QtWidgets import QHeaderView

from adichtion import AdichtFile
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic
from adichtion.ui.info_panel import InfoPanel
from adichtion.ui.timeline import Timeline
from helpers import wait_until


def _many_comments(tmp_path, n_records=4, per_record=1500, video=False):
    ticks = 20000
    channels = [SynthChannel("Flow", "int16", None, 0.2)]
    rng = np.random.default_rng(3)
    records = []
    for r in range(n_records):
        times = sorted(int(t) for t in rng.integers(0, ticks, per_record))
        coms = [(t, -1, f"event {r + 1}-{k}") for k, t in enumerate(times)]
        rec = SynthRecord(ticks, comments=coms,
                          videos=(f"Record{r + 1}.wmv", 416.67, 455.65, 416.38, 0.46) if video else None)
        rec.data = {0: rng.integers(-1000, 1000, ticks)}
        records.append(rec)
    path = tmp_path / "many.adicht"
    write_synthetic(path, channels, records, version=29, layout="sequential", video=video)
    return str(path)


@pytest.fixture
def panel(qapp):
    p = InfoPanel()
    p.resize(700, 500)
    p.show()
    qapp.processEvents()
    yield p
    p.close()
    p.deleteLater()
    qapp.processEvents()


def _cell(view, row, col):
    return view.model().index(row, col).data()


def test_comments_and_blocks_of_a_big_file_are_listed_lazily(qapp, panel, tmp_path):
    f = AdichtFile(_many_comments(tmp_path))
    tl = Timeline(f)
    n = len(tl.comments)
    assert n == 6000
    t0 = time.perf_counter()
    panel.set_file(f, tl)
    took = time.perf_counter() - t0
    qapp.processEvents()
    assert took < 0.15, f"set_file took {took * 1e3:.0f} ms for {n} comments"       # ~2 ms here; one item per cell took ~270
    assert panel.comments.rowCount() == n and panel.blocks.rowCount() == 4
    assert panel.tabs.tabText(0) == f"Comments ({n})" and panel.tabs.tabText(2) == "Blocks (4)"
    assert panel.tabs.tabText(1) == "Channels (1)" and panel.tabs.currentIndex() == 0   # Comments first and shown at start
    # row and cell content, first, middle and last row
    for row in (0, n // 2, n - 1):
        m = tl.comments[row]
        assert [_cell(panel.comments, row, c) for c in range(4)] == [
            str(m.comment.id), str(m.block.number), f"{m.comment.tick / m.block.tick_rate_hz:.3f}", m.comment.text]
    assert _cell(panel.comments, n - 1, 3).startswith("event 4-")
    assert [_cell(panel.blocks, 1, c) for c in range(5)] == [
        "2", f"{f.records[1].start_local:%Y-%m-%d %H:%M:%S}", "0:20", "1500", "-"]
    # alignment as before: numbers right, text left; the comment text doubles as its tooltip
    from PySide6.QtCore import Qt
    idx = panel.comments.model().index(5, 0)
    assert idx.data(Qt.TextAlignmentRole) & Qt.AlignRight and not panel.comments.model().index(5, 3).data(
        Qt.TextAlignmentRole) & Qt.AlignRight
    assert panel.comments.model().index(5, 3).data(Qt.ToolTipRole) == tl.comments[5].comment.text
    assert panel.comments.model().index(5, 0).data(Qt.ToolTipRole) is None
    # a view column is sized from a sample, and the rows are one fixed height
    assert panel.comments.verticalHeader().sectionResizeMode(0) == QHeaderView.Fixed


def test_a_click_on_a_comment_or_block_row_reports_it(qapp, panel, tmp_path):
    f = AdichtFile(_many_comments(tmp_path, n_records=2, per_record=50))
    tl = Timeline(f)
    panel.set_file(f, tl)
    heard, blocks, fits = [], [], []
    panel.commentActivated.connect(heard.append)
    panel.blockActivated.connect(blocks.append)
    panel.blockFitRequested.connect(fits.append)
    # a real click on the viewport, as well as the table-widget style signal
    view = panel.comments
    view.scrollTo(view.model().index(60, 0))
    qapp.processEvents()
    rect = view.visualRect(view.model().index(60, 3))
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(rect.center().x(), rect.center().y()))
    assert heard == [pytest.approx(tl.comments[60].file_time_s)]
    panel.comments.cellClicked.emit(3, 0)
    assert heard[-1] == pytest.approx(tl.comments[3].file_time_s)
    panel.comments.cellClicked.emit(10_000, 0)                                      # out of range: ignored
    assert len(heard) == 2
    panel.blocks.cellClicked.emit(1, 0)
    panel.blocks.cellDoubleClicked.emit(1, 0)
    assert blocks == [1] and fits == [1]


def test_set_file_replaces_and_clears(qapp, panel, tmp_path, synthetic):
    f = AdichtFile(synthetic["path"])
    panel.set_file(f, Timeline(f))
    assert panel.comments.rowCount() == 4 and panel.blocks.rowCount() == 2
    assert panel.channels.item(2, 2).text() == "calculated"
    panel.set_definition(2, "Arithmetic: x")
    assert panel.channels.item(2, 6).text() == "Arithmetic: x"
    g = AdichtFile(_many_comments(tmp_path, n_records=1, per_record=10))
    panel.set_file(g, Timeline(g))
    assert panel.comments.rowCount() == 10 and panel.blocks.rowCount() == 1 and panel.channels.rowCount() == 1
    panel.set_file(None, None)
    assert panel.comments.rowCount() == 0 and panel.blocks.rowCount() == 0 and panel.channels.rowCount() == 0
    assert panel.name.text() == "No file open" and panel.summary.text() == ""
    panel.comments.cellClicked.emit(0, 0)                                           # nothing listed: no signal, no error


def test_clip_count_is_looked_up_off_the_gui_thread(qapp, panel, tmp_path, monkeypatch):
    path = _many_comments(tmp_path, n_records=3, per_record=5, video=True)
    f = AdichtFile(path)
    assert [r.video_candidate for r in f.records] == [
        os.path.join(path[:-len(".adicht")] + " movies", f"Record{i}.wmv") for i in (1, 2, 3)]
    calls = []
    real = os.path.exists
    main = __import__("threading").current_thread()

    def spy(p):
        calls.append((str(p).endswith(".wmv"), __import__("threading").current_thread() is main))
        return real(p)
    monkeypatch.setattr(os.path, "exists", spy)
    panel.set_file(f, Timeline(f))
    # nothing on the GUI thread looked for a clip while the file was installed
    assert not [c for c in calls if c[0] and c[1]]
    assert "3 video clips" in panel.summary.text()
    assert wait_until(qapp, lambda: "(0 found)" in panel.summary.text())            # none of the files exists here
    assert [c for c in calls if c[0] and not c[1]]                                  # the lookups ran on a thread
    # the count sits where it always did, before the later parts
    text = panel.summary.text()
    assert text.index("(0 found)") > text.index("3 video clips")


def test_a_late_clip_count_for_an_earlier_file_is_dropped(qapp, panel, tmp_path):
    # the second file is its own (the session-wide `synthetic` file can have its clip written beside it by
    # another test, which would make nothing missing and no "(0 found)" appear)
    (tmp_path / "g").mkdir()
    f = AdichtFile(_many_comments(tmp_path, n_records=2, per_record=5, video=True))
    g = AdichtFile(_many_comments(tmp_path / "g", n_records=1, per_record=5, video=True))
    panel.set_file(f, Timeline(f))
    token = panel._video_token
    panel.set_file(g, Timeline(g))
    assert wait_until(qapp, lambda: "(0 found)" in panel.summary.text())            # the new file's own answer
    n_text = panel.summary.text()
    assert n_text.count("(0 found)") == 1
    panel._video_counted.emit(token, 1)                                             # the old file's answer arrives late
    qapp.processEvents()
    assert panel.summary.text() == n_text
    panel._video_counted.emit(token - 1, 1)
    qapp.processEvents()
    assert panel.summary.text() == n_text


def test_video_column_only_when_a_clip_is_attached(qapp, panel, tmp_path):
    from adichtion.ui.info_panel import _VIDEO_COLUMN
    f = AdichtFile(_many_comments(tmp_path, n_records=2, per_record=3))
    panel.set_file(f, Timeline(f))
    assert panel.blocks.isColumnHidden(_VIDEO_COLUMN)
    assert not any(panel.blocks.isColumnHidden(c) for c in range(_VIDEO_COLUMN))
    d = tmp_path / "v"
    d.mkdir()
    fv = AdichtFile(_many_comments(d, n_records=2, per_record=3, video=True))
    panel.set_file(fv, Timeline(fv))
    assert not panel.blocks.isColumnHidden(_VIDEO_COLUMN)
    panel.set_file(f, Timeline(f))                                     # and hidden again for the next file
    assert panel.blocks.isColumnHidden(_VIDEO_COLUMN)


def test_mark_interval_is_on_the_comment_and_block_rows_only(qapp, panel, synthetic):
    from PySide6.QtCore import QPoint, Qt
    f = AdichtFile(synthetic["path"])
    tl = Timeline(f)
    panel.set_file(f, tl)
    heard = []
    panel.markIntervalRequested.connect(lambda a, b: heard.append((a, b)))
    # the menu of a comment row: one entry, and it reports the interval up to the next comment
    menu = panel._row_menu("comment", 1)
    assert [a.text() for a in menu.actions()] == ["Mark interval"]
    menu.actions()[0].trigger()
    assert heard == [(pytest.approx(16.005), pytest.approx(39.994))]
    # a block row reports the block's span
    menu = panel._row_menu("block", 1)
    menu.actions()[0].trigger()
    assert heard[-1] == (pytest.approx(40.0), pytest.approx(45.0))
    # a real right click on a row opens the menu for that row; one below the last row does not
    shown = []
    panel.comments.rowContextMenuRequested.connect(lambda r, pos: shown.append(r))
    view = panel.comments
    rect = view.visualRect(view.model().index(2, 3))
    view._on_context_menu(QPoint(rect.center().x(), rect.center().y()))
    assert shown == [2] and view.currentIndex().row() == 2
    view._on_context_menu(QPoint(5, view.viewport().height() - 2))
    assert shown == [2]
    # Channels has no context menu at all
    assert panel.channels.contextMenuPolicy() != Qt.CustomContextMenu
    assert not panel.channels.actions()
    # no file, nothing to mark
    panel.set_file(None, None)
    n = len(heard)
    panel._mark_interval("block", 0)
    assert len(heard) == n
