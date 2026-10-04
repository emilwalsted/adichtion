"""ADICHTION_PERF in the viewer (headless Qt): off means no extra timers and no output,
on means the steps of opening and rendering are timed."""
import io
import json

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
