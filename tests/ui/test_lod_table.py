"""The vectorised level-of-detail curves (``lod.curve_for_range`` over the channel block table) against
the record-by-record implementation they replaced, on synthetic files with many records of different
lengths, a negative-slope channel, a Mac document with blocks that have no stored min/max and a channel
without data."""
import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.blocks import BLOCK_TICKS
from adichtion.synth import (SynthChannel, SynthMacChannel, SynthMacRecord, SynthRecord, write_mac_document,
                             write_synthetic)
from adichtion.ui import lod
from adichtion.ui.timeline import Timeline

RATE = 1000


def legacy_curve(f, timeline, channel, t0, t1, n_buckets, coarse=False):
    """``lod.curve_for_range`` as it was before the block table: one pass over every record."""
    xs, ys, levels = [], [], set()
    n_buckets = max(16, int(n_buckets))
    span = max(t1 - t0, 1e-9)
    for b in timeline.blocks:
        lo_t, hi_t = max(t0, b.start_s), min(t1, b.stop_s)
        if hi_t <= lo_t:
            continue
        rec = f.records[b.index]
        start = max(0, int(np.floor((lo_t - b.start_s) * b.tick_rate_hz)))
        stop = min(b.ticks, int(np.ceil((hi_t - b.start_s) * b.tick_rate_hz)) + 1)
        if stop <= start:
            continue
        n_part = max(1, int(round(n_buckets * (hi_t - lo_t) / span)))
        spt = rec.samples_per_tick(channel)
        n_samples = int(round((stop - start) * spt))
        bucket_ticks = (stop - start) / n_part
        if (stop - start) >= BLOCK_TICKS and (coarse or bucket_ticks >= BLOCK_TICKS / 4):
            t, mn, mx = rec.overview(channel, n_part, start, stop, coarse=True, fill=not coarse)
            x, y = lod.interleave(t + b.start_s, mn, mx)
            levels.add("coarse" if coarse else "envelope")
        elif n_samples <= 2 * n_part:
            y = rec.read(channel, start, stop)
            x = rec.time(start, stop, channel) + b.start_s
            levels.add("samples")
        else:
            t, mn, mx = rec.overview(channel, n_part, start, stop, from_summaries=True)
            x, y = lod.interleave(t + b.start_s, mn, mx)
            levels.add("envelope")
        if xs:
            xs.append(np.array([np.nan]))
            ys.append(np.array([np.nan]))
        xs.append(np.asarray(x, dtype=np.float64))
        ys.append(np.asarray(y, dtype=np.float64))
    level = "coarse" if "coarse" in levels else "envelope" if "envelope" in levels else "samples"
    if not xs:
        return np.zeros(0), np.zeros(0), level
    return np.concatenate(xs), np.concatenate(ys), level


@pytest.fixture(scope="module")
def many(tmp_path_factory):
    """60 records: short ones (1500 ticks), two-block ones (20000), long ones (50000, three blocks)."""
    rng = np.random.default_rng(3)
    ticks = [1500, 20000, 50000, 1500, 1500, 20000] * 10
    chs = [SynthChannel("Up", "int16", units=("cmH2O", 10.0, 1.0)),
           SynthChannel("Down", "int16", units=("L/s", -2.5, 4.0))]
    recs = []
    for i, n in enumerate(ticks):
        data = {c: np.clip(np.cumsum(rng.integers(-300, 301, n)) + 500 * c, -32000, 32000).astype(np.int32)
                for c in range(2)}
        recs.append(SynthRecord(ticks=n, start_utc=1_700_000_000.0 + i * 400.0, data=data))
    path = tmp_path_factory.mktemp("many") / "many.adicht"
    write_synthetic(path, chs, recs, tick_rate=RATE)
    return str(path)


def ranges(duration):
    yield 0.0, duration
    yield 0.0, duration / 2
    yield duration * 0.31, duration * 0.62
    yield 0.0, 40.0                 # inside the first records
    yield 100.0, 130.0
    yield 31.5, 47.2                # across a record boundary, a few ticks into the second record
    yield duration - 5.0, duration + 50.0
    yield -20.0, 3.0


@pytest.mark.parametrize("channel", [0, 1])
@pytest.mark.parametrize("width", [16, 300, 1500, 6000])
def test_detail_pass_is_the_old_curve(many, channel, width):
    with AdichtFile(many) as f:
        tl = Timeline(f)
        for t0, t1 in ranges(tl.duration_s):
            f.clear_cache()         # the block summaries a first call leaves behind would change the second
            x, y, level = lod.curve_for_range(f, tl, channel, t0, t1, width, coarse=False)
            f.clear_cache()
            xl, yl, levell = legacy_curve(f, tl, channel, t0, t1, width, coarse=False)
            assert level == levell, (t0, t1)
            assert x.shape == xl.shape, (t0, t1)
            assert np.allclose(x, xl, atol=1e-9, equal_nan=True) and (np.isnan(x) == np.isnan(xl)).all()
            assert np.allclose(y, yl, rtol=1e-9, atol=1e-9, equal_nan=True), (t0, t1)


@pytest.mark.parametrize("channel", [0, 1])
@pytest.mark.parametrize("width", [16, 300, 1500, 6000])
def test_coarse_pass_covers_the_old_curve(many, channel, width):
    """The quick pass takes a whole short record from the block index where it used to decode it, so its
    two buckets may be wider than the old ones; it never shows less than the old curve did, and where
    the old one used the index the two are the same."""
    with AdichtFile(many) as f:
        tl = Timeline(f)
        for t0, t1 in ranges(tl.duration_s):
            f.clear_cache()
            x, y, level = lod.curve_for_range(f, tl, channel, t0, t1, width, coarse=True)
            f.clear_cache()
            xl, yl, levell = legacy_curve(f, tl, channel, t0, t1, width, coarse=True)
            assert x.shape == xl.shape, (t0, t1)
            assert np.allclose(x, xl, atol=1e-9, equal_nan=True)
            assert (np.isnan(y) == np.isnan(yl)).all()
            ok = np.isfinite(y)
            lo, hi = y[ok][0::2], y[ok][1::2]
            lol, hil = yl[ok][0::2], yl[ok][1::2]
            assert (lo <= lol + 1e-9).all() and (hi >= hil - 1e-9).all(), (t0, t1)
            if levell == "coarse":
                assert level == "coarse"


def test_many_short_records_decode_nothing_in_the_coarse_pass(many):
    with AdichtFile(many) as f:
        tl = Timeline(f)
        f.clear_cache()
        lod.curve_for_range(f, tl, 0, 0.0, tl.duration_s, 1500, coarse=True)
        assert f.cache_stats() == (0, 0)
        lod.curve_for_range(f, tl, 0, 0.0, tl.duration_s, 1500, coarse=False)
        assert f.cache_stats()[1] < 600       # the detail pass decodes only what the index cannot answer


def test_global_range_is_the_min_max_of_all_records(many):
    with AdichtFile(many) as f:
        for c in (0, 1):
            data = np.concatenate([r.read(c) for r in f.records])
            lo, hi = lod.global_range(f, c)
            assert lo == pytest.approx(data.min()) and hi == pytest.approx(data.max())


def test_block_table_matches_the_per_record_envelopes(many):
    with AdichtFile(many) as f:
        for c in (0, 1):
            tab = f.block_table(c)
            assert tab.complete and tab.first[-1] == len(tab.mn) and f.block_table(c) is tab
            for r in (0, 1, 2, 17, len(f.records) - 1):
                t0, t1, mn, mx = f.records[r].block_envelope(c, fill=False)
                a, b = int(tab.first[r]), int(tab.first[r + 1])
                rate = f.records[r].tick_rate_hz
                assert np.allclose(tab.tick0[a:b] / rate, t0) and np.allclose(tab.tick1[a:b] / rate, t1)
                assert np.array_equal(tab.mn[a:b], mn) and np.array_equal(tab.mx[a:b], mx)
                assert (tab.record[a:b] == r).all()


@pytest.fixture(scope="module")
def mac_many(tmp_path_factory):
    rng = np.random.default_rng(5)
    chs = [SynthMacChannel("Flow", 1, "V", 2.048, no_minmax_blocks=[(2, 1), (3, 0)]),
           SynthMacChannel("Calc", 0, calculation="Arithmetic…", expression="Ch1*2"),
           SynthMacChannel("Poes", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True))]
    records = []
    for i, n in enumerate([40000, 3000, 20000, 20000, 2000, 40000]):
        data = [np.cumsum(rng.integers(-50, 51, n)).astype(np.int64), None,
                np.cumsum(rng.integers(-30, 31, -(-n // 40))).astype(np.int64)]
        records.append(SynthMacRecord(n, data, 1_700_000_000.0 + i * 100.0))
    path = tmp_path_factory.mktemp("macmany") / "Synthetic document"
    write_mac_document(path, chs, records, tick_rate=4000)
    return str(path)


def test_mac_document_with_missing_min_max_and_a_channel_without_data(mac_many):
    with AdichtFile(mac_many) as f:
        tl = Timeline(f)
        tab = f.block_table(0)
        assert not tab.complete and np.isnan(tab.mn).sum() == 2 and np.isnan(tab.mx).sum() == 2
        assert len(f.block_table(1).mn) == 0 and lod.global_range(f, 1) is None
        for c in (0, 2):
            for coarse in (True, False):
                for t0, t1 in ranges(tl.duration_s):
                    f.clear_cache()
                    x, y, level = lod.curve_for_range(f, tl, c, t0, t1, 400, coarse=coarse)
                    f.clear_cache()
                    xl, yl, levell = legacy_curve(f, tl, c, t0, t1, 400, coarse=coarse)
                    assert x.shape == xl.shape and np.allclose(x, xl, atol=1e-9, equal_nan=True), (c, coarse, t0)
                    assert (np.isnan(y) == np.isnan(yl)).all(), (c, coarse, t0, t1)
                    ok = np.isfinite(y)
                    assert (y[ok][0::2] <= yl[ok][0::2] + 1e-9).all() and (y[ok][1::2] >= yl[ok][1::2] - 1e-9).all()


def test_missing_min_max_is_filled_by_a_detail_pass_and_seen_by_the_next_coarse_one(mac_many):
    with AdichtFile(mac_many) as f:
        tl = Timeline(f)
        full = (0.0, tl.duration_s)
        assert np.isnan(lod.curve_for_range(f, tl, 0, *full, 16, coarse=True)[1]).sum() > len(f.records) - 1
        assert not f.block_table(0).complete
        lod.curve_for_range(f, tl, 0, *full, 16, coarse=False)         # buckets of whole blocks: the index, filled
        assert f.block_table(0).complete
        y = lod.curve_for_range(f, tl, 0, *full, 16, coarse=True)[1]
        assert np.isnan(y).sum() == len(f.records) - 1                  # only the gaps between records


def test_a_stale_job_returns_what_it_has(many):
    with AdichtFile(many) as f:
        tl = Timeline(f)
        calls = []

        def stale():
            calls.append(1)
            return len(calls) > 2
        x, y, _ = lod.curve_for_range(f, tl, 0, 100.0, 130.0, 300, coarse=False, stale=stale)
        assert len(x) == len(y)


def test_the_strip_makes_block_labels_only_for_the_blocks_it_shows(qapp, window, many):
    window.open_file(many)
    strip = window.stack.strip
    tl = window.stack.timeline
    n = len(tl.blocks)
    assert 0 < len(strip._labels) < n and len(strip._shown) == len(strip._labels)
    # whole file: the shown labels sit side by side without overlapping
    x0, x1 = strip.vb.viewRange()[0]
    px = (x1 - x0)
    shown = sorted(strip._shown)
    spans = [((tl.blocks[i].start_s - x0) / px * strip.vb.width(), strip._label_width(i)) for i in shown]
    assert all(a[0] + a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:]))
    # zoomed to one record: its label, and none of the far-away ones
    b = tl.blocks[7]
    window.stack.set_range(b.start_s, b.stop_s)
    qapp.processEvents()
    assert 7 in strip._shown and all(abs(i - 7) <= 1 for i in strip._shown)
    assert strip._labels[7].isVisible() and not any(strip._labels[i].isVisible() for i in strip._labels if i not in strip._shown)
    # a new file starts without the old labels
    window.open_file(many)
    assert 7 not in strip._labels or 7 in strip._shown or not strip._labels[7].isVisible()
