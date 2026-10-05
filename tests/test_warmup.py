"""Background prefetch and warm-up (``adichtion.ui.warmup``, ``AdichtFile.decode_block(cache=False)``).  Qt-free."""
import threading
import time

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.blocks import BLOCK_TICKS
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic
from adichtion.ui.timeline import Timeline
from adichtion.ui.warmup import MAX_FAILURES, Activity, Warmer, channel_blocks

TICKS = (13 * BLOCK_TICKS + 5000, 7000)             # 14 blocks, the last one short; a single short block
RATE = 1000
N_BLOCKS = 15                                       # per channel


@pytest.fixture(scope="module")
def path(tmp_path_factory):
    rng = np.random.default_rng(5)
    channels = [SynthChannel(f"Ch{i}", "int16", ("cmH2O", 13.34, -0.635), 10.0) for i in range(3)]
    records = []
    for ticks in TICKS:
        r = SynthRecord(ticks)
        r.data = {c: np.clip(np.cumsum(rng.integers(-40, 41, ticks)), -32768, 32767).astype(np.int32) for c in range(3)}
        records.append(r)
    p = tmp_path_factory.mktemp("warm") / "warm.adicht"
    write_synthetic(p, channels, records, tick_rate=RATE)
    return str(p)


@pytest.fixture
def f(path):
    with AdichtFile(path) as f:
        yield f


def warmer(f, **kw):
    kw.setdefault("idle_s", 0.0)
    kw.setdefault("prefetch_idle_s", 0.0)
    kw.setdefault("duty", 1.0)                      # only the 2 ms minimum pause: tests stay quick
    return Warmer(f, Timeline(f), **kw)


def run_for(w, seconds=None, until=None, timeout=10.0, reports=None):
    """Run the job on a thread until ``until()`` (or ``seconds``), then stop it with its own stale flag."""
    stop = threading.Event()
    out = {}
    reports = [] if reports is None else reports
    t = threading.Thread(target=lambda: out.setdefault("n", w.run(stop.is_set, reports.append)))
    t.start()
    end = time.monotonic() + (seconds if seconds is not None else timeout)
    while time.monotonic() < end and not (until is not None and until()):
        time.sleep(0.005)
    stop.set()
    t.join(5)
    assert not t.is_alive()
    return out["n"], reports


# -- the block map ----------------------------------------------------------------------------------------

def test_channel_blocks_cover_the_file_in_order(f):
    tl = Timeline(f)
    for c in range(3):
        b = channel_blocks(f, tl, c)
        assert len(b) == N_BLOCKS
        assert list(b.record[:14]) == [0] * 14 and b.record[14] == 1
        assert list(b.block[:14]) == list(range(14)) and b.block[14] == 0
        assert np.all(np.diff(b.start) > 0) and np.all(b.stop > b.start)
        assert b.start[0] == 0.0 and b.stop[-1] == pytest.approx(tl.duration_s)
        assert b.stop[13] == pytest.approx(TICKS[0] / RATE) and b.start[14] == pytest.approx(TICKS[0] / RATE)


def test_in_range_is_the_overlapping_blocks(f):
    b = channel_blocks(f, Timeline(f), 0)
    per = BLOCK_TICKS / RATE
    assert b.in_range(0.0, 1.0) == slice(0, 1)
    assert b.in_range(per - 1, per + 1) == slice(0, 2)
    assert b.in_range(per, per + 1) == slice(1, 2)               # a block that ends exactly at t0 is not in range
    assert b.in_range(3 * per + 1, 5 * per - 1) == slice(3, 5)
    s = b.in_range(1e6, 2e6)
    assert s.start == s.stop                                       # beyond the file: nothing


# -- decode_block(cache=False) ----------------------------------------------------------------------------

def test_decode_without_caching_leaves_the_summary_and_not_the_block(f):
    x = f.decode_block(0, 3, 0, cache=False)
    assert f.summary_store(0, 0).have[3]
    assert f._cache.nbytes == 0 and f.cache_stats() == (0, 1)
    y = f.decode_block(0, 3, 0)                                    # still a miss: it was never kept
    assert f.cache_stats() == (0, 2) and np.array_equal(x, y) and f._cache.nbytes > 0
    f.decode_block(0, 3, 0, cache=False)                           # cached now: served from the cache
    assert f.cache_stats() == (1, 2)


# -- warm-up ----------------------------------------------------------------------------------------------

def test_warm_up_fills_every_summary_and_keeps_the_cache_empty(f):
    w = warmer(f)
    reports = []
    n, _ = run_for(w, until=lambda: reports and reports[-1] == (3 * N_BLOCKS, 3 * N_BLOCKS), reports=reports)
    assert w.progress() == (3 * N_BLOCKS, 3 * N_BLOCKS)
    assert n == 3 * N_BLOCKS and f._cache.nbytes == 0
    assert reports[-1] == (3 * N_BLOCKS, 3 * N_BLOCKS)
    assert all(a[0] <= b[0] for a, b in zip(reports, reports[1:]))                 # the percentage never goes back
    for c in range(3):                                             # the summaries are the real ones
        x = f.records[0].read_raw(c, 0, BLOCK_TICKS)
        store = f.summary_store(0, c)
        assert store.mins[0].min() == x.min() and store.maxs[0].max() == x.max()


def test_warm_up_starts_at_the_view_with_the_visible_channels_first(f, monkeypatch):
    seen = []
    real = f.decode_block
    monkeypatch.setattr(f, "decode_block", lambda r, b, c, cache=True: (seen.append((c, r, b)), real(r, b, c, cache))[1])
    w = warmer(f)
    per = BLOCK_TICKS / RATE
    w.set_view(9.5 * per, [2, 0])                                  # in block 9; channel 2 is the first shown, 1 is hidden
    run_for(w, until=lambda: len(seen) >= 3 * N_BLOCKS)
    assert [c for c, _r, _b in seen] == [2] * N_BLOCKS + [0] * N_BLOCKS + [1] * N_BLOCKS
    first = [b + (14 if r else 0) for _c, r, b in seen[:N_BLOCKS]]
    assert first[0] == 9 and set(first[:3]) == {8, 9, 10}          # nearest the centre first, spreading outwards
    assert sorted(first) == list(range(N_BLOCKS))


def test_warm_up_skips_what_the_view_already_decoded(f):
    for b in range(5):
        f.decode_block(0, b, 0)
    w = warmer(f)
    n, _ = run_for(w, until=lambda: w.progress()[0] == 3 * N_BLOCKS)
    assert n == 3 * N_BLOCKS - 5


def test_warm_up_off_decodes_nothing_and_on_again_resumes(f):
    w = warmer(f, warm=False)
    n, reports = run_for(w, seconds=0.3)
    assert n == 0 and w.progress()[0] == 0
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    t.start()
    try:
        time.sleep(0.1)
        w.set_warm(True)
        end = time.monotonic() + 10
        while w.progress()[0] < 3 * N_BLOCKS and time.monotonic() < end:
            time.sleep(0.01)
        assert w.progress()[0] == 3 * N_BLOCKS
    finally:
        stop.set()
        t.join(5)


def test_a_finished_sweep_idles_and_a_new_view_does_not_start_it_again(f):
    w = warmer(f)
    run_for(w, until=lambda: w.progress()[0] == 3 * N_BLOCKS)
    before = w.decoded
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    t.start()
    w.set_view(3.0, [0])
    time.sleep(0.4)
    stop.set()
    t.join(5)
    assert w.decoded == before


# -- user activity ----------------------------------------------------------------------------------------

def test_nothing_is_decoded_while_the_user_is_active(f):
    act = Activity()
    w = warmer(f, activity=act, idle_s=0.2, prefetch_idle_s=0.2)
    w.request_prefetch(0, (0.0, 1.0), (1.0, 40.0))
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    act.touch()
    t.start()
    try:
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            act.touch()                                            # a move every few ms
            time.sleep(0.005)
        assert w.decoded == 0 and f.cache_stats() == (0, 0)
        end = time.monotonic() + 5
        while w.decoded == 0 and time.monotonic() < end:           # quiet at last: it starts
            time.sleep(0.01)
        assert w.decoded > 0
    finally:
        stop.set()
        t.join(5)


def test_an_interaction_pauses_a_running_sweep_at_once(f):
    act = Activity()
    w = warmer(f, activity=act, idle_s=0.15)
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    t.start()
    end = time.monotonic() + 5
    while w.decoded < 3 and time.monotonic() < end:
        time.sleep(0.005)
    assert w.decoded >= 3
    act.touch()
    time.sleep(0.04)                                               # a block or two may be in flight
    n = w.decoded
    time.sleep(0.1)
    assert w.decoded == n                                          # quiet for less than idle_s: paused
    stop.set()
    t.join(5)


# -- stopping ---------------------------------------------------------------------------------------------

def test_stale_stops_the_job_quickly(f):
    w = warmer(f)
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    t.start()
    while w.decoded < 2:
        time.sleep(0.002)
    stop.set()
    t.join(2)
    assert not t.is_alive()
    assert w.decoded < 3 * N_BLOCKS


def test_cancel_returns_after_the_block_in_flight_and_nothing_follows(f, monkeypatch):
    real = f.decode_block
    inside, release = threading.Event(), threading.Event()

    def slow(r, b, c, cache=True):
        inside.set()
        release.wait(5)
        return real(r, b, c, cache)

    monkeypatch.setattr(f, "decode_block", slow)
    w = warmer(f)
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    t.start()
    assert inside.wait(5)
    done = []
    c = threading.Thread(target=lambda: (w.cancel(), done.append(1)))
    c.start()
    time.sleep(0.1)
    assert not done                                                # cancel waits for the block being decoded
    release.set()
    c.join(5)
    t.join(5)
    assert done and not t.is_alive() and w.decoded == 1


def test_a_file_that_keeps_failing_is_left_alone(f, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("gone")

    monkeypatch.setattr(f, "decode_block", boom)
    w = warmer(f)
    n, _ = run_for(w, timeout=5)
    assert n == 0 and len(calls) == MAX_FAILURES


# -- prefetch ---------------------------------------------------------------------------------------------

def test_prefetch_caches_the_blocks_next_to_the_view(f):
    per = BLOCK_TICKS / RATE
    w = warmer(f, warm=False)
    w.request_prefetch(0, (4 * per, 6 * per), (8 * per, 10 * per))            # blocks 4, 5 and 8, 9
    run_for(w, until=lambda: w.decoded >= 4)
    assert w.decoded == 4
    hits0, misses0 = f.cache_stats()
    for b in (4, 5, 8, 9):
        f.decode_block(0, b, 0)
    assert f.cache_stats() == (hits0 + 4, misses0)                 # all served from the cache
    f.decode_block(0, 6, 0)
    assert f.cache_stats()[1] == misses0 + 1                       # the one in between was not prefetched


def test_prefetch_goes_outwards_from_the_view_on_both_sides(f, monkeypatch):
    seen = []
    real = f.decode_block
    monkeypatch.setattr(f, "decode_block", lambda r, b, c, cache=True: (seen.append((b, cache)), real(r, b, c, cache))[1])
    per = BLOCK_TICKS / RATE
    w = warmer(f, warm=False)
    w.request_prefetch(1, (1 * per, 4 * per), (6 * per, 9 * per))             # before: blocks 1-3, after: 6-8
    run_for(w, until=lambda: len(seen) >= 6)
    assert seen == [(6, True), (3, True), (7, True), (2, True), (8, True), (1, True)]


def test_a_newer_prefetch_request_replaces_the_older_one(f, monkeypatch):
    seen = []
    real = f.decode_block
    monkeypatch.setattr(f, "decode_block", lambda r, b, c, cache=True: (seen.append(b), real(r, b, c, cache))[1])
    per = BLOCK_TICKS / RATE
    act = Activity()
    w = warmer(f, warm=False, activity=act, prefetch_idle_s=0.15)
    stop = threading.Event()
    t = threading.Thread(target=lambda: w.run(stop.is_set))
    act.touch()
    t.start()
    w.request_prefetch(0, (0.0, 0.0), (1 * per, 4 * per))
    w.request_prefetch(0, (0.0, 0.0), (10 * per, 12 * per))        # before the first one was started
    end = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < end:
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert seen == [10, 11]


def test_prefetch_is_capped_per_side(f, monkeypatch):
    from adichtion.ui import warmup
    monkeypatch.setattr(warmup, "PREFETCH_MAX_BLOCKS", 2)
    w = warmer(f, warm=False)
    w.request_prefetch(0, (0.0, 0.0), (0.0, 14 * BLOCK_TICKS / RATE))
    n, _ = run_for(w, seconds=0.4)
    assert n == 2
