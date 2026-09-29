"""The viewer on a synthetic LabChart for Mac document (headless Qt): calculated channels
are not stored and stay out of the way, slow channels read between their samples."""
import numpy as np
import pytest

from adichtion.synth import SynthMacChannel, SynthMacRecord, write_mac_document
from helpers import pump, wait_until


@pytest.fixture(scope="module")
def mac_doc(tmp_path_factory):
    rng = np.random.default_rng(11)
    walk = lambda n, s: np.cumsum(rng.integers(-s, s + 1, n)).astype(np.int64)
    channels = [
        SynthMacChannel("EMGdi1", 1, "V", 2.048),
        SynthMacChannel("Pdi", 0, calculation="Arithmetic…", expression="Ch4-Ch3"),
        SynthMacChannel("Poes", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True)),
        SynthMacChannel("Pgas", 40, "V", 10.24, units=("cmH2O", 35.1, -3.2, True),
                        no_minmax_blocks=[(r, b) for r in range(2) for b in range(4)]),   # no stored min/max at all
        SynthMacChannel("Flow", 40, "V", 10.24),
    ]
    records = []
    for ticks, start in ((48000, 1_700_000_000.0), (20000, 1_700_000_200.0)):
        data = [walk(ticks, 300), None] + [walk(-(-ticks // 40), 30) for _ in range(3)]
        records.append(SynthMacRecord(ticks, data, start, comments=[(4000, -1, "rest")]))
    path = tmp_path_factory.mktemp("macui") / "Subject document"
    write_mac_document(path, channels, records, tick_rate=4000)
    return {"path": str(path), "records": records}


def test_calculated_channels_are_named_but_not_shown(qapp, window, mac_doc):
    assert window.open_file(mac_doc["path"])
    assert window.stack.visible_channels == [0, 2, 3, 4]
    assert "LabChart for Mac document (7.2)" in window.info.summary.text()
    assert "1 calculated channel not stored" in window.info.summary.text()
    assert window.info.channels.item(1, 2).text() == "calculated, not stored"
    assert window.info.channels.item(1, 6).text().startswith("Arithmetic: Ch4-Ch3")
    assert window.info.channels.item(2, 3).text() == "100"            # 100 Hz on a 4 kHz tick
    assert window.definitions[1].startswith("Arithmetic: Ch4-Ch3")
    window.show_all_channels()
    assert window.stack.visible_channels == [0, 2, 3, 4]
    assert not window.video_box.isVisible()


def test_channel_and_export_dialogs_grey_out_what_is_not_stored(qapp, window, mac_doc):
    from adichtion.ui.channel_dialog import ChannelDialog
    from adichtion.ui.export_dialog import ExportDialog
    window.open_file(mac_doc["path"])
    f = window.file
    dlg = ChannelDialog(f.channel_titles, [False, True, False, False, False], [0, 1, 2], window,
                        available=[f.has_data(c) for c in range(5)], notes=["", "Arithmetic: Ch4-Ch3", "", "", ""])
    assert not dlg.boxes[1].isEnabled() and not dlg.boxes[1].isChecked()
    assert "not stored" in dlg.boxes[1].text() and dlg.boxes[1].toolTip() == "Arithmetic: Ch4-Ch3"
    dlg._set_all(True)
    assert dlg.selected() == [0, 2, 3, 4]
    dlg.deleteLater()
    ex = ExportDialog(f, window.timeline, window.stack.visible_channels, None, None, window)
    assert ex.rb_all.text() == "All channels (4)" and not ex.boxes[1].isEnabled()
    ex.rb_all.setChecked(True)
    assert ex.channels() == [0, 2, 3, 4]
    # mixed rates: a row per tick, and the dialog says the slow channels are up-sampled
    ex.rb_file.setChecked(True)
    assert ex._rows() == sum(r.ticks for r in mac_doc["records"])
    assert "Up-sampled to 4000 Hz" in ex.note.text() and "channels 3-5 from 100 Hz" in ex.note.text()
    ex.rb_pick.setChecked(True)
    for c in (0, 4):
        ex.boxes[c].setChecked(False)                  # only the slow channels: their own rate, no note
    assert ex.channels() == [2, 3]
    assert "Up-sampled" not in ex.note.text()
    assert ex._rows() == sum(-(-r.ticks // 40) for r in mac_doc["records"])
    ex.deleteLater()


def test_cursor_values_between_slow_samples(qapp, window, mac_doc):
    window.open_file(mac_doc["path"])
    window.stack.set_cursor(1.2345)                    # tick 4938: not a multiple of 40
    pump(qapp, 0.05)
    t = window.data.table
    assert t.rowCount() == 4                           # the calculated channel has no row
    names = [t.item(i, 0).text() for i in range(4)]
    assert names[1].startswith("Ch 3 · Poes")
    values = [t.item(i, 1).text() for i in range(4)]
    assert "–" not in values
    x = mac_doc["records"][0].data[2]
    assert float(values[1]) == pytest.approx(33.9 * x[4938 // 40] * 10.24 / 32768 + 1.4, rel=1e-4)


def test_a_channel_without_stored_minmax_gets_its_range_from_the_decoded_curve(qapp, window, mac_doc):
    window.open_file(mac_doc["path"])
    assert 3 in window.stack._y_unset
    assert wait_until(qapp, lambda: 3 not in window.stack._y_unset, timeout=20)
    lo, hi = window.stack.plots[3].vb.viewRange()[1]
    x = np.concatenate([r.data[3] for r in mac_doc["records"]])
    y = 35.1 * x * 10.24 / 32768 - 3.2
    assert lo <= y.min() and hi >= y.max()


def test_startup_path_accepts_a_document_without_extension(mac_doc, tmp_path):
    from adichtion.ui.app import startup_path
    assert startup_path(["adichtion", mac_doc["path"]]) == mac_doc["path"]
    other = tmp_path / "notes"
    other.write_text("hello")
    assert startup_path(["adichtion", str(other)]) is None


def test_a_mac_movie_follows_the_cursor_by_its_frame_stamps(qapp, window, tmp_path):
    """The movie of a LabChart for Mac block is found in ``<name> movies`` and synchronised
    by the capture stamps of its frames (MAC_FORMAT.md §7): at block time t the panel shows
    the frame at movie time t + offset."""
    from test_video import FPS, SECONDS, frame_number, write_test_video
    from adichtion import AdichtFile
    from adichtion.synth import add_labchart_sync_track
    rng = np.random.default_rng(3)
    doc = tmp_path / "Recording"
    write_mac_document(doc, [SynthMacChannel("EMGdi1", 1, "V", 2.048)],
                       [SynthMacRecord(40000, [np.cumsum(rng.integers(-50, 51, 40000))], 1_700_000_000.0)],
                       tick_rate=4000, movies={0: "LabChartMovie1.mov"})
    folder = tmp_path / "Recording movies"
    folder.mkdir()
    movie = str(folder / "LabChartMovie1.mov")
    write_test_video(movie)                                   # frame numbers drawn in, 25 fps
    with AdichtFile(doc) as f:
        clock0 = f.mac.record_clock_us[0]
    offset = 0.2                                              # movie time of the block start
    stamps = clock0 + np.round((np.arange(FPS * SECONDS) / FPS - offset) * 1e6).astype(np.int64)
    stamps[0] = clock0 + 20_000
    add_labchart_sync_track(movie, stamps)
    window.open_file(str(doc))
    v = window.video
    assert window.video_box.isVisible() and v.has_videos
    window.stack.set_cursor(4.0)
    assert v.has_clip
    assert wait_until(qapp, lambda: v._pixmap is not None, timeout=20)
    assert frame_number(v._pixmap.toImage()) == pytest.approx(round((4.0 + offset) * FPS), abs=1)
    assert "4.200 s in the clip (synchronised by the movie's frame stamps)" in v.caption.text()
    assert "1 video clip" in window.info.summary.text()
