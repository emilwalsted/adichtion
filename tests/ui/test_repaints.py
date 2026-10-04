"""One repaint per plot per move (P4): a pan or zoom must not make a plot paint twice, once for the new range and
once more because pyqtgraph applied the view transform lazily inside the first paint, and the coarse picture of an
uncovered view is set in the same event-loop turn as the move, not by the debounced pass."""
import pytest
from PySide6.QtCore import QEvent, QObject

from adichtion.ui import channel_stack
from helpers import pump, wait_until


class _PaintCounter(QObject):
    """Counts the paint events of every plot's viewport (the widget the scene is drawn on)."""

    def __init__(self, stack):
        super().__init__()
        self.stack = stack
        self.counts = {p.index: 0 for p in stack.plots}
        self._owner = {id(p.widget.viewport()): p.index for p in stack.plots}
        for p in stack.plots:
            p.widget.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint and id(obj) in self._owner:
            self.counts[self._owner[id(obj)]] += 1
        return False

    def reset(self):
        for k in self.counts:
            self.counts[k] = 0


def _settled(qapp, st, t0, t1):
    st.set_range(t0, t1)
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(t0, t1, int(st._master().vb.width())) for c in st.visible_channels),
                      timeout=20)
    pump(qapp, 0.3)


def test_a_pan_inside_the_decoded_margin_paints_each_plot_once(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    counter = _PaintCounter(st)
    counter.reset()
    st._master().vb.pan_fraction(0.05)
    assert hasattr(st.plots[0].vb, "_matrixNeedsUpdate")           # a renamed pyqtgraph attribute must fail here, loudly
    for c in st.visible_channels:                                  # the view transform is current before any paint
        assert not getattr(st.plots[c].vb, "_matrixNeedsUpdate", False)
    pump(qapp, 0.4)                                                # past the debounced pass (it has nothing to draw)
    assert st.view_range()[0] == pytest.approx(10.5)
    assert all(n <= 1 for n in counter.counts.values()), counter.counts
    assert sum(counter.counts.values()) >= 1


def test_every_range_change_leaves_the_view_transform_current(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    for fn in (lambda: st.set_range(5.0, 7.0), lambda: st.scroll_view(0.5), lambda: st.zoom_in(), lambda: st.zoom_out(),
               lambda: st._master().vb.pan_fraction(-0.1)):
        fn()
        assert not any(getattr(p.vb, "_matrixNeedsUpdate", False) for p in st.plots if p.index in st.visible_channels)


def test_an_uncovered_view_gets_its_coarse_picture_with_the_move_and_only_once(qapp, window, synthetic, monkeypatch):
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    calls = []
    real = channel_stack.lod.curve_for_range

    def counting(f, tl, c, a, b, n, coarse=False, **kw):
        if coarse:
            calls.append(c)
        return real(f, tl, c, a, b, n, coarse=coarse, **kw)

    monkeypatch.setattr(channel_stack.lod, "curve_for_range", counting)
    st._coarse_ms, st._coarse_end = 0.0, 0.0                       # (a cold first pass on a busy machine may have been slow)
    st.set_range(10.0, 10.5)                                       # far outside the decoded span's resolution
    assert sorted(calls) == sorted(st.visible_channels)            # drawn at once, before any event is processed
    key = st.plots[st.visible_channels[0]].coarse_key
    assert key is not None and key[:2] == pytest.approx(st.view_range())
    st._render_now()                                               # what the debounced timer does
    assert sorted(calls) == sorted(st.visible_channels)            # the same view is not drawn again
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    st.set_range(11.0, 11.5)                                       # a new view is
    assert len(calls) == 2 * len(st.visible_channels)
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(11.0, 11.5, 800) for c in st.visible_channels), timeout=20)
    assert all(st.plots[c].coarse_key is None for c in st.visible_channels)    # the decoded curve replaced the coarse one


def test_refresh_draws_the_coarse_picture_again(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    st.refresh()
    assert all(st.plots[c]._detail_cover is None and st.plots[c].coarse_key is None for c in st.visible_channels)
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(10.0, 20.0, 800) for c in st.visible_channels), timeout=20)


def test_a_slow_coarse_pass_is_left_to_the_debounced_timer(qapp, window, synthetic, monkeypatch):
    """A file whose block index is expensive to walk (thousands of records) must not stall every wheel notch: once a
    pass has taken longer than the budget, the picture waits for the debounced pass, as before."""
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    calls = []
    real = channel_stack.lod.curve_for_range

    def counting(f, tl, c, a, b, n, coarse=False, **kw):
        if coarse:
            calls.append(c)
        return real(f, tl, c, a, b, n, coarse=coarse, **kw)

    monkeypatch.setattr(channel_stack.lod, "curve_for_range", counting)
    slow = channel_stack.COARSE_SYNC_BUDGET_MS * 100.0
    st._coarse_ms = slow
    st.set_range(10.0, 10.5)
    assert calls == []                                             # nothing on the GUI thread with the move
    assert wait_until(qapp, lambda: len(calls) >= len(st.visible_channels), timeout=5)    # the debounced pass draws it
    assert st._coarse_ms < slow                                    # measured again, smoothed towards what it really costs
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(10.0, 10.5, 800) for c in st.visible_channels), timeout=20)


def test_a_burst_of_inputs_draws_the_coarse_picture_once_not_per_input(qapp, window, synthetic, monkeypatch):
    """Queued inputs (a busy GUI thread) must not each pay for a coarse pass: at most one per frame, the debounced
    pass draws the final view."""
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    calls = []
    real = channel_stack.lod.curve_for_range

    def counting(f, tl, c, a, b, n, coarse=False, **kw):
        if coarse:
            calls.append(c)
        return real(f, tl, c, a, b, n, coarse=coarse, **kw)

    monkeypatch.setattr(channel_stack.lod, "curve_for_range", counting)
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    for _ in range(10):
        st.zoom_in()
    assert 0 < len(calls) <= 2 * len(st.visible_channels)
    assert wait_until(qapp, lambda: all(st.plots[c].detail_covers(*st.view_range(), 800) for c in st.visible_channels), timeout=20)


def test_a_detail_result_for_an_older_view_is_dropped(qapp, window, synthetic):
    import numpy as np
    window.open_file(synthetic["path"])
    st = window.stack
    _settled(qapp, st, 10.0, 20.0)
    st._coarse_ms, st._coarse_end = 0.0, 0.0
    st.set_range(30.0, 30.5)
    c = st.visible_channels[0]
    key = st.plots[c].coarse_key
    assert key is not None
    st._on_progress(st._render_job, "render", (c, 0.0, 20.0, np.zeros(3), np.zeros(3), "envelope", 3))
    assert st.plots[c].coarse_key == key                           # the coarse picture of the current view stays
