"""ADICHTION_PERF in the viewer (headless Qt): off means no extra timers and no output,
on means the steps of opening and rendering are timed."""
import io
import json
import os

import pytest
from PySide6.QtCore import QTimer

from adichtion import perf
from helpers import pump, wait_until


@pytest.fixture(autouse=True)
def _perf_off():
    perf.configure(False)
    yield
    perf.configure(False)


def test_off_there_is_no_stall_timer_and_nothing_is_printed(qapp, window, synthetic, capsys):
    capsys.readouterr()
    assert window._stall is None
    assert not [t for t in window.findChildren(QTimer) if t.interval() == 5]
    window.open_file(synthetic["path"])
    assert wait_until(qapp, lambda: all(p.level == "envelope" for p in window.stack.plots), timeout=20)
    pump(qapp, 0.1)
    out = capsys.readouterr()
    assert "perf" not in out.out and "perf" not in out.err.replace("propagateSizeHints", "")
    assert perf.summary() == {}


def test_on_the_steps_are_timed_and_only_numbers_are_written(qapp, synthetic, tmp_path):
    from adichtion.ui.main_window import MainWindow
    from adichtion.ui.perfmon import StallMonitor

    out, path = io.StringIO(), tmp_path / "perf.jsonl"
    perf.configure(True, str(path), stream=out)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    try:
        assert isinstance(w._stall, StallMonitor)
        w.open_file(synthetic["path"])
        assert wait_until(qapp, lambda: all(p.level == "envelope" for p in w.stack.plots), timeout=20)
        pump(qapp, 0.2)
        w.stack.set_range(10.0, 20.0)
        assert wait_until(qapp, lambda: "render.detail" in perf.summary() and perf.summary()["render.detail"]["count"] >= 8, timeout=20)
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()
    names = set(perf.summary())
    for expected in ("open", "install.timeline", "install.stack", "install.info", "install.data", "install.video",
                     "stack.global_range", "render.coarse", "render.detail", "gui.gap"):
        assert expected in names, expected
    assert perf.summary()["gui.gap"]["count"] > 5                    # the 5 ms timer ticked
    assert "perf summary" in out.getvalue()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    cache = [r for r in rows if r["event"] == "cache"]
    assert len(cache) == 1 and cache[0]["hits"] > 0 and cache[0]["decoded_blocks"] > 0       # the file's counters at close
    detail = [r for r in rows if r["event"] == "render.detail"]
    assert detail and all("decoded_blocks" in r and "cache_hits" in r and r["level"] in ("samples", "envelope") for r in detail)
    text = path.read_text().lower() + out.getvalue().lower()
    for forbidden in ("flow", "pmo", "synthetic", "start", "inh2o", str(tmp_path).lower()):
        assert forbidden not in text.replace("perf summary", ""), forbidden


# -- the steps of a pan or zoom -----------------------------------------------------------------

def _opened(qapp, synthetic, **kw):
    from adichtion.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.open_file(synthetic["path"])
    assert wait_until(qapp, lambda: all(p.level == "envelope" for p in w.stack.plots), timeout=20)
    w.stack.set_range(10.0, 20.0)
    pump(qapp, 0.3)
    return w


def test_a_pan_is_broken_into_its_steps_and_counted(qapp, synthetic):
    perf.configure(True, stream=io.StringIO())
    w = _opened(qapp, synthetic)
    try:
        perf.configure(True, stream=io.StringIO())                    # only the pan below
        master = w.stack._master()
        for _ in range(5):
            master.vb.pan_fraction(0.05)
            pump(qapp, 0.05)
        pump(qapp, 0.3)
        s, c = perf.summary(), perf.counts()
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()
    for name in ("input.swipe", "stack.x_range", "stack.sync_hbar", "stack.range_changed", "strip.relayout", "vb.linked",
                 "vb.update_range", "axis.ticks", "axis.draw_specs", "markers.paint", "cursor.paint", "frame.paint"):
        assert name in s, name
    assert s["input.swipe"]["count"] == 5 and s["stack.x_range"]["count"] == 5
    assert c["input.swipe"] == 5 and c["signal.x_range"] == 5
    assert c["signal.x_range.all"] == 5 * len(w.stack.visible_channels)    # one per visible plot per move, the master's included
    assert c["input.swipe.x_range"] == c["signal.x_range.all"]              # all of it caused inside the five inputs
    assert c["paint.plot"] >= 5


def test_off_a_pan_counts_and_times_nothing(qapp, synthetic):
    perf.configure(False)
    w = _opened(qapp, synthetic)
    try:
        for _ in range(3):
            w.stack._master().vb.pan_fraction(0.05)
            pump(qapp, 0.05)
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()
    assert perf.summary() == {} and perf.counts() == {}


def test_event_timing_names_are_code_and_only_the_outermost_event_counts(qapp):
    from PySide6.QtCore import QEvent, QObject
    from adichtion.ui import perfmon

    perf.configure(True, stream=io.StringIO())
    obj = QObject()
    calls = []

    def inner(receiver, ev):
        calls.append(ev.type())
        if len(calls) == 1:                                           # an event sent from inside a handler: part of the outer one
            perfmon.timed_notify(inner, receiver, QEvent(QEvent.Timer))
        return True

    assert perfmon.timed_notify(inner, obj, QEvent(QEvent.Timer)) is True
    assert perfmon.timed_notify(lambda r, e: True, obj, QEvent(QEvent.User)) is True      # a type outside the named set
    names = set(perf.summary())
    assert names == {"ev.Timer.QObject", "ev.Other"}
    assert perf.summary()["ev.Timer.QObject"]["count"] == 1 and len(calls) == 2
    class Local:                                                      # a class of neither this program nor a toolkit is never named
        pass
    perf.configure(True, stream=io.StringIO())
    perfmon.timed_notify(lambda r, e: True, Local(), QEvent(QEvent.Timer))
    assert set(perf.summary()) == {"ev.Other"}
    perf.configure(True, stream=io.StringIO())
    with pytest.raises(RuntimeError):                                 # an exception leaves nothing stuck: the next event is timed again
        perfmon.timed_notify(lambda r, e: (_ for _ in ()).throw(RuntimeError()), obj, QEvent(QEvent.Timer))
    perfmon.timed_notify(lambda r, e: True, obj, QEvent(QEvent.Timer))
    assert perf.summary()["ev.Timer.QObject"]["count"] == 2
    perf.configure(True, stream=io.StringIO())
    with pytest.raises(ValueError):
        perf.count("subject 42")


def test_bench_pan_drives_the_window_and_reports_only_numbers(synthetic, tmp_path, capsys):
    import shutil

    from adichtion.cli import main as cli_main

    path = tmp_path / "SUBJ-0042 baseline.adicht"
    shutil.copy(synthetic["path"], path)
    cli_main(["bench", str(path), "--pan", "6", "--json"])
    r = json.loads(capsys.readouterr().out)["pan"]
    assert r["scenario"]["pan_steps"] == 6 and r["scenario"]["zoom_steps"] == 3 and r["scenario"]["channels_shown"] == 4
    assert r["events"]["input.swipe"]["count"] == 6 and r["events"]["input.wheel"]["count"] == 3
    assert r["counts"]["signal.x_range"] == 9 and r["counts"]["paint.plot"] > 0
    cli_main(["bench", str(path), "--pan", "4"])
    table = capsys.readouterr().out
    assert "pan        4 pan + 2 zoom steps" in table and "stack.x_range" in table and "counts:" in table
    text = (json.dumps(r) + table).lower()
    for forbidden in ("subj-0042", "baseline", "flow", "pmo", "inh2o", str(tmp_path).lower()):
        assert forbidden not in text, forbidden
    assert not perf.enabled()                                         # the scenario leaves timing off again


def test_bench_pan_times_qt_events_through_the_application_class(synthetic):
    """A fresh process: the application class that installs ``notify`` (the pytest QApplication would shadow it)."""
    import subprocess
    import sys

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", ADICHTION_PERF="", PYTHONPATH=os.pathsep.join(p for p in sys.path if p))   # the tests move HOME: keep the user site
    out = subprocess.run([sys.executable, "-m", "adichtion", "bench", synthetic["path"], "--pan", "4", "--json"],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-300:]
    events = json.loads(out.stdout)["pan"]["events"]
    assert any(k.startswith("ev.") for k in events), sorted(events)
    assert all(k.startswith("ev.") is False or k.count(".") == 2 or k == "ev.Other" for k in events)
