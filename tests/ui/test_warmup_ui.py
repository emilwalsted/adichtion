"""Prefetch and background warm-up in the viewer (headless Qt): it starts with a file, indexes it while the window is
idle, can be switched off (and the choice survives), never outlives its file, and does not make the GUI thread stall."""
import time

import numpy as np
import pytest
from PySide6.QtCore import QSettings, QTimer

from adichtion import AdichtFile
from adichtion.blocks import BLOCK_TICKS
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic
from adichtion.ui.warmup import channel_blocks
from helpers import pump, wait_until

RECORDS = 1200                                     # one second and one block each, 1200 s in all: so many records that the
TICKS = 1000                                       # whole-file view is answered from the block index and decodes nothing
CHANNELS = 2
RATE = 1000


def make_file(path):
    rng = np.random.default_rng(11)
    channels = [SynthChannel(f"Ch{i}", "int16", ("cmH2O", 13.34, -0.635), 10.0) for i in range(CHANNELS)]
    records = []
    for _ in range(RECORDS):
        r = SynthRecord(TICKS)
        r.data = {c: np.clip(np.cumsum(rng.integers(-40, 41, TICKS)), -32768, 32767).astype(np.int32)
                  for c in range(CHANNELS)}
        records.append(r)
    write_synthetic(path, channels, records, tick_rate=RATE)
    return str(path)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return make_file(tmp_path_factory.mktemp("wu") / "wu.adicht")


big = doc


@pytest.fixture(autouse=True)
def _quick_pauses(monkeypatch):
    """Tests do not wait for the production pacing: no minimum pause, so a sweep of 2400 blocks takes seconds."""
    from adichtion.ui import warmup
    monkeypatch.setattr(warmup, "MIN_PAUSE_S", 0.0)


def quick(w, duty=1.0):
    w.idle_s = 0.0
    w.duty = duty


def indexed(w):
    d, t = w.stack.warmer.progress()
    return t > 0 and d >= t


def test_a_file_gets_a_warmer_and_it_indexes_the_file_when_idle(qapp, window, doc):
    seen = []
    window.stack.warmProgress.connect(lambda d, t: seen.append((d, t)))
    window.open_file_sync(doc)
    w = window.stack.warmer
    assert w is not None and window.act_warmup.isChecked() and w.warm
    assert w.progress()[0] == 0                                  # the whole-file view read nothing: there is work to do
    quick(w)                                                    # not 0.4 s of waiting in a test
    assert wait_until(qapp, lambda: indexed(window), timeout=60)
    assert w.progress() == (CHANNELS * RECORDS, CHANNELS * RECORDS) and w.decoded == CHANNELS * RECORDS
    assert wait_until(qapp, lambda: seen and seen[-1] == (0, 0), timeout=5)       # done: the label goes away
    assert any(0 < d < t for d, t in seen)                       # and it reported on the way
    assert window.warm_label.text() == ""


def test_the_status_bar_shows_the_percentage_while_it_runs(qapp, window, big):
    window.open_file_sync(big)
    w = window.stack.warmer
    quick(w, duty=0.5)
    assert wait_until(qapp, lambda: window.warm_label.text().startswith("Indexing "),
                      timeout=20)
    txt = window.warm_label.text()
    assert txt.endswith(" %") and 0 <= int(txt.split()[1]) < 100
    assert wait_until(qapp, lambda: indexed(window), timeout=60)
    assert wait_until(qapp, lambda: window.warm_label.text() == "", timeout=5)


def test_switching_it_off_stops_the_sweep_and_the_choice_is_remembered(qapp, window, doc):
    window.act_warmup.setChecked(False)
    assert window.stack.warmup_enabled is False
    assert QSettings("adichtion", "adichtion-tests").value("view/warmup", True, type=bool) is False
    window.open_file_sync(doc)
    quick(window.stack.warmer)
    pump(qapp, 0.6)
    assert window.stack.warmer.progress()[0] == 0 and window.stack.warmer.decoded == 0
    from adichtion.ui.main_window import MainWindow
    w2 = MainWindow()
    try:
        assert not w2.act_warmup.isChecked() and not w2.stack.warmup_enabled      # a new window starts as it was left
    finally:
        w2.close()
        w2.deleteLater()
    window.act_warmup.setChecked(True)                                           # on again: the sweep carries on
    assert wait_until(qapp, lambda: indexed(window), timeout=60)


def test_the_warm_up_does_not_fill_the_block_cache(qapp, window, doc):
    window.open_file_sync(doc)
    quick(window.stack.warmer)
    assert wait_until(qapp, lambda: indexed(window), timeout=60)
    assert window.file._cache.nbytes == 0                       # only the summaries were kept


def test_closing_the_file_stops_the_warmer_before_the_file_is_closed(qapp, window, doc):
    window.open_file_sync(doc)
    w = window.stack.warmer
    quick(w)
    assert wait_until(qapp, lambda: w.decoded >= 2, timeout=20)
    f = window.file
    window.close_file()
    assert w.cancelled and window.stack.warmer is None
    n = w.decoded
    pump(qapp, 0.3)
    assert w.decoded == n                                       # nothing is decoded from the closed file
    assert f._cache.nbytes == 0


def test_opening_another_file_replaces_the_warmer(qapp, window, doc, synthetic):
    window.open_file_sync(doc)
    first = window.stack.warmer
    window.open_file_sync(synthetic["path"])
    assert first.cancelled and window.stack.warmer is not first and window.stack.warmer.f is window.file


def test_a_detail_pass_prefetches_the_blocks_next_to_the_view(qapp, window, doc):
    window.open_file_sync(doc)
    window.act_warmup.setChecked(False)                         # only the prefetch is under test
    w = window.stack.warmer
    w.duty = 1.0
    w.prefetch_idle_s = 0.0
    f = window.file
    assert wait_until(qapp, lambda: all(p.level in ("envelope", "coarse") for p in window.stack.plots), timeout=20)
    pump(qapp, 0.2)
    window.stack.set_range(100.0, 110.0)                         # ten records wide: decoded samples
    assert wait_until(qapp, lambda: all(p.detail_covers(100.0, 110.0, 800) for p in window.stack.plots), timeout=20)
    blocks = channel_blocks(f, window.timeline, 0)
    assert blocks.in_range(97.5, 112.5) == slice(97, 113)         # the pass decoded these records' blocks ...
    assert wait_until(qapp, lambda: w.decoded >= 10 and not w._has_prefetch(), timeout=20)
    assert blocks.in_range(92.5, 97.5) == slice(92, 98)          # ... and the half screen before it: records 92 to 96 are new
    hits, misses = f.cache_stats()
    for c in range(CHANNELS):
        for r in range(92, 97):
            f.decode_block(r, 0, c)
    assert f.cache_stats() == (hits + 5 * CHANNELS, misses)      # ready in the cache for a pan to the left


def test_a_prefetch_waits_for_the_user(qapp, window, doc):
    window.open_file_sync(doc)
    window.act_warmup.setChecked(False)
    w = window.stack.warmer
    w.duty = 1.0
    w.prefetch_idle_s = 0.3
    assert wait_until(qapp, lambda: all(p.level in ("envelope", "coarse") for p in window.stack.plots), timeout=20)
    pump(qapp, 0.2)
    window.stack.set_range(100.0, 110.0)
    assert wait_until(qapp, lambda: all(p.detail_covers(100.0, 110.0, 800) for p in window.stack.plots), timeout=20)
    n = w.decoded
    t_end = time.monotonic() + 0.25
    while time.monotonic() < t_end:                              # the user keeps moving the cursor
        window.stack.activity.touch()
        qapp.processEvents()
        time.sleep(0.01)
    assert w.decoded == n
    assert wait_until(qapp, lambda: w.decoded > n, timeout=10)


class Heartbeat:
    """A 5 ms timer on the GUI thread; ``max_ms`` is the longest time between two of its ticks."""

    def __init__(self):
        self.last = time.perf_counter()
        self.max_ms = 0.0
        self.timer = QTimer()
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.tick)

    def tick(self):
        now = time.perf_counter()
        self.max_ms = max(self.max_ms, (now - self.last) * 1000.0)
        self.last = now

    def run(self, app, seconds):
        self.last = time.perf_counter()
        self.max_ms = 0.0
        self.timer.start()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.002)                                    # never a tight loop: it starves the worker of the GIL
        self.timer.stop()
        return self.max_ms


def test_the_warm_up_does_not_make_the_gui_thread_stall(qapp, window, big, monkeypatch):
    monkeypatch.undo()                                           # the production pacing: that is what is being measured
    window.open_file_sync(big)
    w = window.stack.warmer
    assert wait_until(qapp, lambda: all(p.level in ("envelope", "coarse") for p in window.stack.plots), timeout=30)
    window.act_warmup.setChecked(False)
    window._on_warm_progress(1, 100)                             # the status label's first paint is a one-off (70 to 110 ms with
    pump(qapp, 0.3)                                              # the software renderer): not what is measured here
    window._on_warm_progress(0, 0)
    pump(qapp, 0.3)
    hb = Heartbeat()
    baseline = max(hb.run(qapp, 1.0) for _ in range(2))
    window.act_warmup.setChecked(True)
    w.idle_s = 0.0                                               # (duty and pauses are the production ones)
    d0 = w.progress()[0]
    during = hb.run(qapp, 1.5)
    assert w.progress()[0] > d0 + 20                              # it really was decoding meanwhile
    assert w.progress()[0] < w.progress()[1]                     # and was not done yet: the whole run was measured under load
    # no worse than a little above the idle window's own worst gap (generous: the sandbox's timing is noisy)
    assert during <= max(4 * baseline, baseline + 50.0), (baseline, during)


def test_a_late_progress_report_of_the_file_before_is_ignored(qapp, window, doc, synthetic):
    window.open_file_sync(doc)
    old_job = window.stack._warm_job
    window.open_file_sync(synthetic["path"])
    window.stack._on_warm_progress(old_job, "warm", (37, 100))
    assert window.warm_label.text() == ""
    window.stack._on_warm_progress(window.stack._warm_job, "warm", (37, 100))
    assert window.warm_label.text() == "Indexing 37 %"


def test_a_render_result_of_the_file_before_is_dropped(qapp, window, doc, synthetic):
    window.open_file_sync(doc)
    window.stack.set_range(100.0, 110.0)
    assert wait_until(qapp, lambda: window.stack._render_job != 0, timeout=10)
    job = window.stack._render_job
    window.open_file_sync(synthetic["path"])
    assert window.stack._render_job != job
    c = np.zeros(3)
    window.stack._on_progress(job, "render", (0, 0.0, 1e9, c, c, "samples", 3, True))
    assert window.stack.warmer._requests == {}                    # nothing was drawn or prefetched from it


def test_a_pass_answered_by_the_index_does_not_prefetch_even_while_the_sweep_decodes(qapp, window, doc):
    window.act_warmup.setChecked(False)
    window.open_file_sync(doc)
    w = window.stack.warmer
    w.prefetch_idle_s = 0.0
    assert wait_until(qapp, lambda: all(p.level in ("envelope", "coarse") for p in window.stack.plots), timeout=20)
    pump(qapp, 0.3)
    assert w._requests == {} and not w._queue and w.decoded == 0                   # the whole-file view needed no decoded samples


def test_the_sweep_starts_where_the_user_is_looking_even_when_the_index_answers(qapp, window, doc):
    window.open_file_sync(doc)
    window.act_warmup.setChecked(False)
    window.stack.set_range(900.0, 1100.0)                         # 200 one-second records: still the index's business
    assert wait_until(qapp, lambda: window.stack.warmer._view is not None, timeout=10)
    centre, shown = window.stack.warmer._view
    assert centre == pytest.approx(1000.0, abs=1.0) and shown == window.stack.visible_channels
