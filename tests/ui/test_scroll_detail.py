"""The decoded curve stays on screen while the view moves: a pan past its end or a zoom draws only the strip it does
not reach the quick way (never the whole view from the block index again), the decoding keeps up with a hand that does
not stop, and a curve that arrives mid-move goes on screen with the next move's repaint."""
import numpy as np
import pytest

from adichtion.blocks import BLOCK_TICKS
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic
from adichtion.ui import channel_stack
from helpers import pump, wait_until

RATE = 1000
TICKS = 20 * BLOCK_TICKS                            # one record of 320 s: twenty storage blocks of 16 s
CHANNELS = 2


@pytest.fixture(scope="module")
def long_doc(tmp_path_factory):
    rng = np.random.default_rng(23)
    channels = [SynthChannel(f"Ch{i}", "int16", ("cmH2O", 13.34, -0.635), 10.0) for i in range(CHANNELS)]
    r = SynthRecord(TICKS)
    r.data = {c: np.clip(np.cumsum(rng.integers(-40, 41, TICKS)), -32768, 32767).astype(np.int32) for c in range(CHANNELS)}
    path = tmp_path_factory.mktemp("scroll") / "scroll.adicht"
    write_synthetic(path, channels, [r], tick_rate=RATE)
    return str(path)


def _width(st) -> int:
    return max(200, int(st._master().vb.width()))


def _open(qapp, window, path, t0, t1):
    """The file open, its background decoding off (the tests decide what has been decoded) and ``[t0, t1]`` decoded."""
    window.open_file_sync(path)
    st = window.stack
    window.act_warmup.setChecked(False)
    st.warmer.prefetch_idle_s = 1e9
    st.set_range(t0, t1)
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(t0, t1, _width(st)) for c in st.visible_channels), timeout=20)
    pump(qapp, 0.3)
    st._coarse_ms, st._coarse_end = 0.0, 0.0        # (a cold first pass on a busy machine may have been slow)
    return st


def _payload(c, a, b, n=4000, value=0.0):
    x = np.linspace(a, b, n)
    return c, a, b, x, np.full(n, value), "envelope", n, False


def test_a_pan_past_the_decoded_curve_keeps_it_and_draws_only_the_strip(qapp, window, long_doc, monkeypatch):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    calls = []
    real = channel_stack.lod.curve_for_range

    def recording(f, tl, c, a, b, n, coarse=False, **kw):
        if coarse:
            calls.append((c, a, b, kw.get("decode", True)))
        return real(f, tl, c, a, b, n, coarse=coarse, **kw)

    monkeypatch.setattr(channel_stack.lod, "curve_for_range", recording)
    before = {c: (st.plots[c]._detail_cover, st.plots[c]._curve_x) for c in st.visible_channels}
    assert all(cover == pytest.approx((95.0, 125.0)) for cover, _ in before.values())
    stats = st.file.cache_stats()
    st.set_range(108.0, 128.0)                      # three seconds past the end of the decoded curve
    assert st.file.cache_stats() == stats           # nothing decoded on the GUI thread for it
    for c in st.visible_channels:
        p = st.plots[c]
        assert p._detail_cover == before[c][0] and p._curve_x is before[c][1]      # the decoded curve is untouched
        assert p.level == "envelope"
        ex = p._edge_x[np.isfinite(p._edge_x)]
        assert len(ex) and ex.min() >= 125.0 - 1e-6 and ex.max() <= 128.0 + 1e-6   # the strip, and only the strip
        assert p.coarse_key is not None
    # one quick curve per channel, for the strip alone, told not to decode
    assert sorted(calls) == [(c, pytest.approx(125.0), pytest.approx(128.0), False) for c in st.visible_channels]
    # the worker's curve then replaces both
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(108.0, 128.0, _width(st)) for c in st.visible_channels),
                      timeout=20)
    assert all(len(st.plots[c]._edge_x) == 0 and st.plots[c].coarse_key is None for c in st.visible_channels)


def test_a_pan_to_the_left_and_a_zoom_out_draw_the_strips_on_both_sides(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    p = st.plots[st.visible_channels[0]]
    st.set_range(88.0, 108.0)
    ex = p._edge_x[np.isfinite(p._edge_x)]
    assert p._detail_cover == pytest.approx((95.0, 125.0)) and ex.min() >= 88.0 - 1e-6 and ex.max() <= 95.0 + 1e-6
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    st.set_range(60.0, 160.0)                       # a zoom out: the decoded curve in the middle, a strip on each side
    ex = p._edge_x[np.isfinite(p._edge_x)]
    assert p._detail_cover == pytest.approx((95.0, 125.0))
    assert ex.min() < 62.0 and ex.max() > 158.0 and not ((ex > 95.0 + 1e-6) & (ex < 125.0 - 1e-6)).any()


def test_the_strip_is_drawn_from_the_summaries_of_blocks_decoded_before(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    p = st.plots[st.visible_channels[0]]
    st.set_range(120.0, 140.0)                      # 125 to 140 s: blocks 7 (decoded for the margin) and 8 (not)
    ey = p._edge_y[np.isfinite(p._edge_x) & (p._edge_x > 130.0)]
    assert len(np.unique(ey)) <= 2                  # block 8 as the block index has it: one min and one max
    assert wait_until(qapp, lambda: p.detail_covers(120.0, 140.0, _width(st)), timeout=20)
    pump(qapp, 0.2)
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    stats = st.file.cache_stats()
    st.set_range(100.0, 120.0)                      # back: 100 to 115 s lies left of the curve, in blocks decoded before
    assert st.file.cache_stats() == stats
    ey = p._edge_y[np.isfinite(p._edge_x)]
    assert len(np.unique(ey)) > 20                  # their summaries: many steps per block, not one


def test_a_zoom_in_keeps_the_decoded_curve_until_it_is_far_too_coarse(qapp, window, long_doc, monkeypatch):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    calls = []
    real = channel_stack.lod.curve_for_range

    def counting(f, tl, c, a, b, n, coarse=False, **kw):
        if coarse:
            calls.append(c)
        return real(f, tl, c, a, b, n, coarse=coarse, **kw)

    monkeypatch.setattr(channel_stack.lod, "curve_for_range", counting)
    p = st.plots[st.visible_channels[0]]
    cover, cx = p._detail_cover, p._curve_x
    st.set_range(107.0, 112.0)                      # four times closer: the curve is a little soft, and stays
    assert calls == [] and p._detail_cover == cover and p._curve_x is cx and len(p._edge_x) == 0
    assert not p.detail_covers(107.0, 112.0, _width(st))            # ...until the worker's sharper one arrives
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(107.0, 112.0, _width(st)) for c in st.visible_channels),
                      timeout=20)
    pump(qapp, 0.2)
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    cover = p._detail_cover
    st.set_range(109.0, 109.2)                      # twenty-five times closer: the quick picture of the view instead
    assert sorted(calls) == sorted(st.visible_channels) and p._detail_cover is None
    assert p.detail_usable(109.0, 109.2, _width(st)) is None


def test_a_steady_scroll_keeps_its_decoded_curves(qapp, window, long_doc):
    """A hand that does not stop: before, the debounced pass never ran and every view past the margin was the block
    index.  Now a pass runs while the view moves and asks for the next curve before the view reaches the end."""
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    vb = st._master().vb
    first_job = st._render_job
    covered, jobs_mid_move = [], 0
    for _ in range(150):                            # 0.5 % of the view per step, about 12 ms apart: 75 % in two seconds
        vb.pan_fraction(0.005)
        pump(qapp, 0.012)
        x0, x1 = st.view_range()
        covered.append(all(st.plots[c].detail_covers(x0, x1, _width(st)) for c in st.visible_channels))
        jobs_mid_move = st._render_job - first_job
    assert st.view_range()[0] == pytest.approx(115.0)               # well past the 5 s of margin it began with
    assert jobs_mid_move >= 1                                       # decoded while moving, not after
    assert sum(covered) >= 0.9 * len(covered), covered              # (a stalled test machine may miss a step or two)
    for c in st.visible_channels:                                   # and what was not covered was only ever a strip
        assert st.plots[c]._detail_cover is not None


def test_a_pass_made_while_panning_asks_ahead_and_lets_a_serving_job_finish(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    job = st._render_job
    st._master().vb.pan_fraction(0.12)              # 102.4 to 122.4 s: still inside the decoded 95 to 125 s
    assert st._pan_dir == 1
    st._render_now(settled=True)
    assert st._render_job == job                    # a settled pass has nothing to ask for
    st._render_now(settled=False)                   # a pass while panning does: the curve ends 2.6 s ahead (under 15 %)
    assert st._render_job != job
    _, a, b = st._render_live
    assert (a, b) == pytest.approx((102.4 - 5.0, 122.4 + 10.0))     # the usual margin behind, twice that ahead
    job = st._render_job
    st._master().vb.pan_fraction(0.01)
    st._render_now(settled=False)
    assert st._render_job == job                    # the job in flight still serves the view: not replaced
    st._master().vb.pan_fraction(-0.01)
    assert st._pan_dir == -1
    st.zoom_in()
    assert st._pan_dir == 0                         # a zoom has no direction
    assert wait_until(qapp, lambda: st._render_live is None, timeout=20)


def test_a_curve_that_arrives_mid_move_waits_for_the_next_move(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    c = st.visible_channels[0]
    p = st.plots[c]
    st._master().vb.pan_fraction(0.01)              # the view is moving (the debounce is running)
    assert st._timer.isActive()
    cover = p._detail_cover
    st._on_progress(st._render_job, "render", _payload(c, 90.0, 140.0))
    assert p._detail_cover == cover and c in st._held               # kept, not drawn between two moves
    st._master().vb.pan_fraction(0.01)
    assert p._detail_cover == (90.0, 140.0) and not st._held        # on screen in the turn of the move
    # and when no move follows, the pass after the last one shows it
    st._master().vb.pan_fraction(0.01)
    st._on_progress(st._render_job, "render", _payload(c, 80.0, 150.0))
    assert p._detail_cover == (90.0, 140.0)
    assert wait_until(qapp, lambda: p._detail_cover == (80.0, 150.0), timeout=5)
    # a view at rest takes it at once
    assert not st._timer.isActive()
    st._on_progress(st._render_job, "render", _payload(c, 70.0, 160.0))
    assert p._detail_cover == (70.0, 160.0)
    # a file change forgets what was held
    st._master().vb.pan_fraction(0.01)
    st._on_progress(st._render_job, "render", _payload(c, 60.0, 170.0))
    assert st._held
    st.set_file(None)
    assert not st._held


def test_a_late_curve_is_shown_when_it_shows_more_of_the_view_than_what_is_there(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    c = st.visible_channels[0]
    p = st.plots[c]
    st.runner.submit = lambda *a, **k: 0            # only the curves this test hands in
    st.set_range(200.0, 220.0)                      # a jump: the quick picture of the whole view, no decoded curve
    assert p._detail_cover is None and p.level == "coarse"
    st._show_result(_payload(c, 150.0, 195.0), fit=False)           # misses the view altogether: dropped
    assert p._detail_cover is None
    st._show_result(_payload(c, 190.0, 212.0), fit=False)           # 12 of the 20 s: better than nothing decoded
    assert p._detail_cover == (190.0, 212.0)
    ex = p._edge_x[np.isfinite(p._edge_x)]
    assert len(ex) and ex.min() >= 212.0 - 1e-6 and ex.max() <= 220.0 + 1e-6      # the rest drawn the quick way
    st._show_result(_payload(c, 185.0, 210.0), fit=False)           # 10 s: less than what is on screen
    assert p._detail_cover == (190.0, 212.0)
    st._show_result(_payload(c, 195.0, 225.0), fit=False)           # the whole view
    assert p._detail_cover == (195.0, 225.0) and len(p._edge_x) == 0


def test_the_auto_zoom_fits_when_the_move_is_over_not_during_it(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    c = st.visible_channels[0]
    p = st.plots[c]
    st.set_auto_zoom(True)
    pump(qapp, 0.2)
    y = p.vb.viewRange()[1]
    st.runner.submit = lambda *a, **k: 0
    st._show_result(_payload(c, 90.0, 140.0, value=1e6), fit=False)   # a curve arriving while the view moves
    assert p.vb.viewRange()[1] == y
    st._show_result(_payload(c, 90.0, 140.0, value=1e6), fit=True)    # ...and one at rest
    lo, hi = p.vb.viewRange()[1]
    assert lo < 1e6 < hi and (lo, hi) != tuple(y)


def test_the_visible_y_range_counts_the_strip(qapp, window, long_doc):
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    c = st.visible_channels[0]
    p = st.plots[c]
    st.runner.submit = lambda *a, **k: 0
    st._show_result(_payload(c, 95.0, 125.0, value=5.0), fit=False)
    st.set_range(110.0, 130.0)
    lo, hi = p.visible_y_range()
    ey = p._edge_y[np.isfinite(p._edge_y)]
    assert len(ey) and lo == pytest.approx(min(5.0, ey.min())) and hi == pytest.approx(max(5.0, ey.max()))


def test_a_flick_too_fast_for_the_decoding_waits_for_the_last_move(qapp, window, long_doc):
    """Mid-move decoding is for curves that can arrive before the view has left them; a flick across blocks that are
    slow to decode would only take time from the drawing."""
    import time
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    assert st._job_s > 0.0                          # the pass that decoded the view was timed on its thread
    vb = st._master().vb
    vb.pan_fraction(0.1)
    assert st._pan_speed == 0.0                     # a single move after a rest has no speed yet
    time.sleep(0.02)
    vb.pan_fraction(0.1)
    time.sleep(0.02)
    vb.pan_fraction(0.1)                            # 106 to 126 s: a second past the decoded curve
    assert 1.0 < st._pan_speed < 20.0               # some views per second
    job = st._render_job
    st._pan_speed, st._job_s = 3.0, 0.5             # three views a second, half a second per pass: gone before it comes
    st._render_now(settled=False)
    assert st._render_job == job
    st._job_s = 0.01                                # a pass of 10 ms does arrive in time
    st._render_now(settled=False)
    assert st._render_job != job
    job = st._render_job
    st._pan_speed, st._job_s = 3.0, 0.5
    st._render_live = None
    st._render_now(settled=True)                    # and the pass after the last move always asks
    assert st._render_job != job
    st.zoom_in()
    assert st._pan_speed == 0.0


def test_slow_frames_are_not_mistaken_for_a_stop(qapp, window, long_doc, monkeypatch):
    """When a frame takes longer than the debounce, the timer fires between two moves of a steady scroll.  The pace of
    the moves tells that apart from a stop, so the y-axes are not fitted (and no decoding is restarted) on every move."""
    import time
    st = _open(qapp, window, long_doc, 100.0, 120.0)
    seen = []
    monkeypatch.setattr(st, "_render_now", lambda settled=True: seen.append(settled))
    st._timer.stop()
    st._move_gap, st._last_request = 0.08, time.perf_counter() - 0.07      # moves 80 ms apart, the last one 70 ms ago
    st._render()
    assert seen == [False] and st._timer.isActive()                        # still moving: the pass after the last move is to come
    st._timer.stop()
    st._move_gap, st._last_request = 0.08, time.perf_counter() - 0.13      # more than a gap and a half: at rest
    st._render()
    assert seen == [False, True] and not st._timer.isActive()
    st._move_gap, st._last_request = 0.0, time.perf_counter() - 0.07       # a single move, the debounce over: at rest
    st._render()
    assert seen == [False, True, True]
    st._move_gap, st._last_request = 5.0, time.perf_counter() - 0.25       # never waits longer than SETTLE_MAX_MS
    st._render()
    assert seen[-1] is True
    monkeypatch.undo()
    vb = st._master().vb
    vb.pan_fraction(0.01)
    assert st._move_gap == 0.0                                             # the first move of a run has no pace yet
    time.sleep(0.03)
    vb.pan_fraction(0.01)
    assert 0.02 < st._move_gap < 0.2
