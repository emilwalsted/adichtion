"""Block min/max summaries, the memory-limited block cache and the detail pass that uses them
(``AdichtFile.summary_store``, ``Record.summary_overview``, ``lod.curve_for_range``)."""
import numpy as np
import pytest

from adichtion import AdichtFile, file as file_module
from adichtion.blocks import BLOCK_TICKS
from adichtion.file import SUMMARY_BUCKETS, _BlockCache, default_cache_bytes
from adichtion.synth import (SynthChannel, SynthMacChannel, SynthMacRecord, SynthRecord, write_mac_document,
                             write_synthetic)
from adichtion.ui import lod
from adichtion.ui.timeline import Timeline

TICKS = (13 * BLOCK_TICKS + 5000, 7000)             # 14 blocks, the last one short; a single short block
CH_VOLTS, CH_NEG, CH_FLOAT = 0, 1, 2


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    rng = np.random.default_rng(3)
    channels = [
        SynthChannel("Pmo", "int16", ("cmH2O", 13.34, -0.635), 10.0),
        SynthChannel("Neg", "int16", ("mV", -2.0, 5.0), 10.0),          # a negative slope swaps min and max
        SynthChannel("Calc", "float32", None, 10.0, lsb=0.2 * 1.024 / 32768),
    ]
    records = []
    for ticks in TICKS:
        t = np.arange(ticks)
        r = SynthRecord(ticks)
        r.data = {
            0: np.clip(9000 * np.sin(t / 3000.0) + np.cumsum(rng.integers(-40, 41, ticks)), -32768, 32767).astype(np.int32),
            1: np.clip(np.cumsum(rng.integers(-60, 61, ticks)), -32768, 32767).astype(np.int32),
            2: (np.cumsum(rng.normal(0, 1, ticks)) * 0.5 + 100).astype(np.float32),
        }
        records.append(r)
    path = tmp_path_factory.mktemp("sum") / "summaries.adicht"
    write_synthetic(path, channels, records, tick_rate=1000)
    return {"path": str(path), "records": records}


def _decode_all(f, rec=0, channel=0):
    for b in range(len(f.records[rec].block_ticks)):
        f.decode_block(rec, b, channel)


# -- (a) the summary equals a min/max taken from the decoded block -----------------------------------

@pytest.mark.parametrize("channel", [CH_VOLTS, CH_NEG, CH_FLOAT])
def test_summary_matches_decoded_min_max_per_bucket(doc, channel):
    with AdichtFile(doc["path"]) as f:
        assert f.summary_store(0, channel) is None                    # nothing decoded yet
        for rec in (0, 1):
            for b in range(len(f.records[rec].block_ticks)):
                x = f.decode_block(rec, b, channel)
                store = f.summary_store(rec, channel)
                assert store.have[b] and store.count[b] == min(SUMMARY_BUCKETS, len(x)) and store.n[b] == len(x)
                k = store.count[b]
                for j in range(k):
                    part = x[(j * len(x)) // k: ((j + 1) * len(x)) // k]
                    assert store.mins[b, j] == part.min() and store.maxs[b, j] == part.max()


def test_blocks_decoded_in_any_order_leave_summaries_and_reads_stay_exact(doc):
    with AdichtFile(doc["path"]) as f, AdichtFile(doc["path"], cache_bytes=1) as g:    # g keeps one block only
        for b in (5, 0, 13, 7):
            f.decode_block(0, b, CH_VOLTS)
        assert list(f.summary_store(0, CH_VOLTS).have).count(True) == 4
        for ch in range(3):
            assert np.array_equal(f.records[0].read_raw(ch, 1234, 99999), g.records[0].read_raw(ch, 1234, 99999))


# -- (b) a repeated view of the whole file decodes nothing -------------------------------------------

def test_second_full_file_view_decodes_no_block(doc):
    with AdichtFile(doc["path"]) as f:
        tl = Timeline(f)
        # 300 buckets over ~214 s: a bucket is ~710 ticks, i.e. between 1/32 and 1/4 of a block
        first = lod.curve_for_range(f, tl, CH_VOLTS, 0.0, tl.duration_s, 300)
        _hits, misses = f.cache_stats()
        assert misses == 15 and first[2] == "envelope"                 # every block of record 0 and 1 decoded once
        f._cache.clear()                                               # even with the block cache emptied ...
        again = lod.curve_for_range(f, tl, CH_VOLTS, 0.0, tl.duration_s, 300)
        assert f.cache_stats()[1] == misses                            # ... nothing is decoded again
        assert again[2] == "envelope" and len(again[0]) == len(first[0])
        f.clear_cache()
        assert f.summary_store(0, CH_VOLTS) is None                    # a cold start forgets the summaries too


# -- (c) the summary envelope against the decoded one ---------------------------------------------------

@pytest.mark.parametrize("channel", [CH_VOLTS, CH_NEG, CH_FLOAT])
@pytest.mark.parametrize("start,stop,n", [(0, None, 250), (0, None, 400), (123456, 190001, 120), (20000, 60000, 77)])
def test_summary_envelope_bounds_the_decoded_one_within_one_bucket(doc, channel, start, stop, n):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        exact = rec.overview(channel, n, start, stop)                  # decodes (and leaves the summaries)
        got = rec.summary_overview(channel, n, start, stop)
        assert got is not None
        t_e, lo_e, hi_e = exact
        t_s, lo_s, hi_s = got
        assert len(lo_s) == n and np.allclose(t_s, t_e)
        assert np.isfinite(lo_s).all() and np.isfinite(hi_s).all()
        # never misses an extreme ...
        assert (lo_s <= lo_e + 1e-9).all() and (hi_s >= hi_e - 1e-9).all()
        # ... and reaches no further than one summary bucket (500 ticks; 1 sample per tick here) beyond
        # the samples of its own pixel bucket, which also holds at the ends of the requested range
        stop_t = rec.ticks if stop is None else stop
        full = rec.read(channel)
        edges = np.linspace(start, stop_t, n + 1)
        reach = BLOCK_TICKS // SUMMARY_BUCKETS
        for i in range(n):
            a = max(0, int(np.floor(edges[i])) - reach)
            b = min(rec.ticks, int(np.ceil(edges[i + 1])) + reach)
            assert lo_s[i] >= full[a:b].min() - 1e-9 and hi_s[i] <= full[a:b].max() + 1e-9


def test_overview_without_summaries_or_with_narrow_buckets_decodes_as_before(doc):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        plain = rec.overview(CH_VOLTS, 300, 0, None)
        f.clear_cache()
        assert rec.summary_overview(CH_VOLTS, 300) is None             # never decoded: no answer
        again = rec.overview(CH_VOLTS, 300, 0, None, from_summaries=True)
        assert all(np.array_equal(a, b) for a, b in zip(plain, again))   # fell back to the samples: identical
        # a bucket narrower than a summary bucket (500 ticks) is not served from the summaries
        assert rec.summary_overview(CH_VOLTS, 2000, 0, 40000) is None
        part = rec.overview(CH_VOLTS, 2000, 0, 40000, from_summaries=True)
        assert all(np.array_equal(a, b) for a, b in zip(part, rec.overview(CH_VOLTS, 2000, 0, 40000)))
        _decode_all(f)
        # one block missing, the rest present: also no answer
        f2 = AdichtFile(doc["path"])
        for b in range(len(f2.records[0].block_ticks)):
            if b != 4:
                f2.decode_block(0, b, CH_VOLTS)
        assert f2.records[0].summary_overview(CH_VOLTS, 300) is None
        assert f2.records[0].summary_overview(CH_VOLTS, 20, 0, 3 * BLOCK_TICKS) is not None     # range misses block 4
        f2.close()


def test_units_off_summary_returns_raw_counts(doc):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        _decode_all(f, 0, CH_VOLTS)
        t, lo, hi = rec.summary_overview(CH_VOLTS, 100, 0, None, units=False)
        t2, lo2, hi2 = rec.overview(CH_VOLTS, 100, 0, None, units=False)
        assert (lo <= lo2).all() and (hi >= hi2).all() and lo.dtype == np.float64


# -- (d) the memory-limited cache ---------------------------------------------------------------------------

def test_cache_respects_its_byte_limit_and_hands_out_the_decoders_values(doc):
    block_bytes = BLOCK_TICKS * 2                                       # int16 channel: kept as int16
    with AdichtFile(doc["path"], cache_bytes=3 * block_bytes) as f, AdichtFile(doc["path"]) as ref:
        for b in range(len(f.records[0].block_ticks)):
            x = f.decode_block(0, b, CH_VOLTS)
            assert x.dtype == np.int32 and np.array_equal(x, ref.decode_block(0, b, CH_VOLTS))
            used, limit = f.cache_usage()
            assert used <= limit == 3 * block_bytes
        assert len(f._cache._d) == 3                                    # the three most recent blocks
        hits_before = f.cache_stats()[0]
        again = f.decode_block(0, 13, CH_VOLTS)
        assert f.cache_stats()[0] == hits_before + 1 and again.dtype == np.int32
        assert np.array_equal(again, ref.decode_block(0, 13, CH_VOLTS))
        again[:] = 0                                                    # a caller changing its copy does no harm
        assert np.array_equal(f.decode_block(0, 13, CH_VOLTS), ref.decode_block(0, 13, CH_VOLTS))


def test_int16_kept_as_int16_float_as_float_and_wide_values_as_they_are():
    c = _BlockCache(max_bytes=10_000)
    c.put(1, np.arange(1000, dtype=np.int32) - 500)
    assert c._d[1].dtype == np.int16 and c.nbytes == 2000
    c.put(2, np.arange(1000, dtype=np.float32))
    assert c._d[2].dtype == np.float32 and c.nbytes == 2000 + 4000
    c.put(3, np.array([0, 40000], dtype=np.int32))                      # does not fit 16 bits
    assert c._d[3].dtype == np.int32 and c.get(3).dtype == np.int32
    assert c.get(1).dtype == np.int32 and np.array_equal(c.get(1), np.arange(1000) - 500)
    c.put(4, np.zeros(2400, dtype=np.float32))                          # 9600 bytes: evicts the older ones
    assert c.nbytes <= 10_000 and 4 in c._d and 1 not in c._d
    c.put(5, np.zeros(5000, dtype=np.float32))                          # a block above the limit is still kept alone
    assert list(c._d) == [5]
    c.clear()
    assert c.nbytes == 0 and not c._d


def test_block_count_limit_still_works(doc):
    with AdichtFile(doc["path"], cache_blocks=4) as f:
        _decode_all(f)
        assert len(f._cache._d) == 4


def test_free_memory_is_a_positive_number_or_unknown():
    free = file_module._available_memory()
    assert free is None or free > 0


def test_default_cache_size_follows_free_memory_within_its_bounds(monkeypatch):
    mib = 1024 * 1024
    for free, want in ((100 * mib, file_module.CACHE_MIN_BYTES),             # little memory: the floor
                       (2048 * mib, 512 * mib),                              # a quarter
                       (64 * 1024 * mib, file_module.CACHE_MAX_BYTES),       # plenty: the ceiling
                       (None, file_module.CACHE_MIN_BYTES)):                 # unknown: the floor
        monkeypatch.setattr(file_module, "_available_memory", lambda free=free: free)
        assert default_cache_bytes() == want


# -- the detail pass without the 8-bucket floor ------------------------------------------------------------

def test_many_short_records_do_not_multiply_the_curve_points(tmp_path):
    n_rec, ticks = 40, 2000
    rng = np.random.default_rng(5)
    recs = []
    for r in range(n_rec):
        rec = SynthRecord(ticks, start_utc=1_700_000_000.0 + r * 10.0)
        rec.data = {0: np.clip(np.cumsum(rng.integers(-50, 51, ticks)), -30000, 30000).astype(np.int32)}
        recs.append(rec)
    path = tmp_path / "many.adicht"
    write_synthetic(path, [SynthChannel("Ch", "int16", None, 10.0)], recs, tick_rate=1000)
    with AdichtFile(path) as f:
        tl = Timeline(f)
        x, y, _level = lod.curve_for_range(f, tl, 0, 0.0, tl.duration_s, 200)
        n_points = int(np.isfinite(y).sum())
        assert n_points <= 2 * (200 + n_rec)             # two points per bucket; at most one extra bucket per record
        assert len(x) == len(y) and np.isnan(y).sum() == n_rec - 1


# -- Mac documents (decimated channels, a block without data) -----------------------------------------------

def test_mac_document_summaries(tmp_path):
    rng = np.random.default_rng(11)
    ticks = 3 * BLOCK_TICKS + 700
    channels = [SynthMacChannel("Fast", 1, "V", 2.048), SynthMacChannel("Slow", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True)),
                SynthMacChannel("Calc", 0, calculation="Arithmetic…", expression="Ch1-Ch2")]
    data = [np.cumsum(rng.integers(-300, 301, ticks)).astype(np.int64),
            np.cumsum(rng.integers(-30, 31, -(-ticks // 40))).astype(np.int64), None]
    path = tmp_path / "Mac document"
    write_mac_document(path, channels, [SynthMacRecord(ticks, data, 1_700_000_000.0, comments=[], marks=[])], tick_rate=4000)
    with AdichtFile(path) as f:
        rec = f.records[0]
        assert f.summary_store(0, 2, create=True) is None               # a calculated channel has no samples
        for ch in (0, 1):
            exact = rec.overview(ch, 90)
            got = rec.summary_overview(ch, 90)
            assert got is not None and got[1].shape == (90,)
            assert (got[1] <= exact[1] + 1e-9).all() and (got[2] >= exact[2] - 1e-9).all()
            assert np.isfinite(got[1]).all()


def test_summary_mixes_block_types_and_ignores_nan():
    from adichtion.file import _ChannelSummaries
    st = _ChannelSummaries(np.array([0, 10, 20]))
    st.put(0, np.arange(10, dtype=np.int32))
    st.put(1, np.linspace(0.1, 0.9, 10).astype(np.float32))        # not truncated to the first block's type
    assert st.maxs[1, :10].max() == pytest.approx(0.9) and st.mins[1, 0] == pytest.approx(0.1)
    x = np.array([1, np.nan, 3, 4], dtype=np.float32)
    st2 = _ChannelSummaries(np.array([0, 4]))
    st2.put(0, x)
    assert st2.mins[0, 2] == 3 and st2.maxs[0, 3] == 4 and np.isnan(st2.mins[0, 1])


# -- the quick pass: summaries where blocks were decoded, the block index elsewhere, nothing decoded -------------

@pytest.mark.parametrize("channel", [CH_VOLTS, CH_NEG, CH_FLOAT])
@pytest.mark.parametrize("start,stop,n", [(0, None, 250), (0, None, 3000), (123456, 190001, 120), (20000, 60000, 777),
                                          (40000, 40300, 20)])
def test_quick_overview_without_summaries_is_the_block_index(doc, channel, start, stop, n):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        stop_t = rec.ticks if stop is None else stop
        got = rec.quick_overview(channel, n, start, stop)
        b0, b1, mn, mx = rec.block_envelope(channel, fill=False)
        want = rec._spread(start / 1000.0, stop_t / 1000.0, n, b0, b1, mn, mx)
        assert all(np.array_equal(a, b) for a, b in zip(got, want))
        if stop_t - start >= BLOCK_TICKS:                               # the first picture the plots always drew
            old = rec.overview(channel, n, start, stop, coarse=True, fill=False)
            assert all(np.array_equal(a, b) for a, b in zip(got, old))
        assert f.cache_stats() == (0, 0) and f.summary_store(0, channel) is None       # nothing was decoded


@pytest.mark.parametrize("channel", [CH_VOLTS, CH_NEG, CH_FLOAT])
@pytest.mark.parametrize("start,stop,n", [(0, None, 250), (0, None, 3000), (123456, 190001, 120), (20000, 60000, 777),
                                          (40000, 40300, 20)])
def test_quick_overview_from_summaries_bounds_the_samples_and_decodes_nothing(doc, channel, start, stop, n):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        _decode_all(f, 0, channel)
        full = rec.read(channel)
        stats = f.cache_stats()
        t, lo, hi = rec.quick_overview(channel, n, start, stop)
        assert f.cache_stats() == stats                                 # not one block asked for
        stop_t = rec.ticks if stop is None else stop
        assert len(lo) == n and np.isfinite(lo).all() and np.isfinite(hi).all()
        edges = np.linspace(start, stop_t, n + 1)
        assert np.allclose(t, (edges[:-1] + edges[1:]) / 2 / 1000.0)
        reach = BLOCK_TICKS // SUMMARY_BUCKETS
        for i in range(n):
            a, b = int(np.floor(edges[i])), min(rec.ticks, int(np.ceil(edges[i + 1])))
            own = full[a:max(b, a + 1)]
            assert lo[i] <= own.min() + 1e-9 and hi[i] >= own.max() - 1e-9          # never narrower than the samples
            wide = full[max(0, a - reach):min(rec.ticks, b + reach)]               # at most one summary bucket beyond
            assert lo[i] >= wide.min() - 1e-9 and hi[i] <= wide.max() + 1e-9
        # also where a pixel bucket is narrower than a summary bucket, which summary_overview declines
        if (stop_t - start) / n < reach:
            assert rec.summary_overview(channel, n, start, stop) is None


def test_quick_overview_mixes_summaries_and_index_block_by_block(doc):
    with AdichtFile(doc["path"]) as f:
        rec = f.records[0]
        n = 13 * 64                                                      # 64 buckets per full block, over 13 blocks
        index_only = rec.quick_overview(CH_VOLTS, n, 0, 13 * BLOCK_TICKS)
        for b in (3, 4, 9):
            f.decode_block(0, b, CH_VOLTS, cache=False)                  # as the background warm-up does
        stats = f.cache_stats()
        t, lo, hi = rec.quick_overview(CH_VOLTS, n, 0, 13 * BLOCK_TICKS)
        assert f.cache_stats() == stats
        block_of = np.arange(n) // 64
        seen = np.isin(block_of, (3, 4, 9))
        inner = seen & ~np.isin(np.arange(n) % 64, (0, 63))              # a bucket at a block's edge also takes its neighbour
        assert np.array_equal(lo[~seen], index_only[1][~seen]) and np.array_equal(hi[~seen], index_only[2][~seen])
        assert (lo[inner] >= index_only[1][inner] - 1e-9).all() and (hi[inner] <= index_only[2][inner] + 1e-9).all()
        assert (hi[inner] - lo[inner]).mean() < 0.5 * (index_only[2][inner] - index_only[1][inner]).mean()      # and much finer


def test_quick_overview_of_a_channel_without_samples_is_empty(doc, tmp_path):
    rng = np.random.default_rng(2)
    ticks = 2 * BLOCK_TICKS + 700
    channels = [SynthMacChannel("Fast", 1, "V", 2.048),
                SynthMacChannel("Calc", 0, calculation="Arithmetic…", expression="Ch1*2")]
    data = [np.cumsum(rng.integers(-300, 301, ticks)).astype(np.int64), None]
    path = tmp_path / "Mac document"
    write_mac_document(path, channels, [SynthMacRecord(ticks, data, 1_700_000_000.0, comments=[], marks=[])], tick_rate=4000)
    with AdichtFile(path) as f:
        t, lo, hi = f.records[0].quick_overview(1, 50)
        assert len(t) == 50 and np.isnan(lo).all() and np.isnan(hi).all()
        t, lo, hi = f.records[0].quick_overview(0, 50)
        assert len(t) == 50


def test_the_quick_curve_uses_summaries_where_a_block_is_several_pixels_wide(doc):
    with AdichtFile(doc["path"]) as f:
        tl = Timeline(f)
        t0, t1, n = 20.0, 120.0, 1500                                    # 67 ms buckets against 16 s blocks
        x0, y0, level0 = lod.curve_for_range(f, tl, CH_VOLTS, t0, t1, n, coarse=True)
        assert level0 == "coarse" and f.cache_stats() == (0, 0)
        _decode_all(f, 0, CH_VOLTS)
        exact = lod.curve_for_range(f, tl, CH_VOLTS, t0, t1, n, coarse=False)
        stats = f.cache_stats()
        x1, y1, level1 = lod.curve_for_range(f, tl, CH_VOLTS, t0, t1, n, coarse=True)
        assert f.cache_stats() == stats and level1 == "coarse"
        assert np.allclose(x0, x1, rtol=0, atol=1e-9)                    # the same buckets ...
        lo0, hi0, lo1, hi1 = y0[0::2], y0[1::2], y1[0::2], y1[1::2]
        assert (lo1 >= lo0 - 1e-9).all() and (hi1 <= hi0 + 1e-9).all()   # ... inside the index's steps ...
        assert (hi1 - lo1).mean() < 0.2 * (hi0 - lo0).mean()             # ... and far finer
        ex_lo, ex_hi = exact[1][0::2], exact[1][1::2]
        assert (lo1 <= ex_lo + 1e-9).all() and (hi1 >= ex_hi - 1e-9).all()               # never narrower than the decoded curve


def test_the_quick_curve_stays_with_the_index_where_blocks_are_narrow_or_records_many(doc, monkeypatch):
    with AdichtFile(doc["path"]) as f:
        tl = Timeline(f)
        _decode_all(f, 0, CH_VOLTS)
        calls = []
        real = type(f.records[0]).quick_overview
        monkeypatch.setattr(type(f.records[0]), "quick_overview", lambda self, *a, **k: calls.append(a) or real(self, *a, **k))
        lod.curve_for_range(f, tl, CH_VOLTS, 0.0, 208.0, 30, coarse=True)        # 2 buckets per block: the table
        assert calls == []
        lod.curve_for_range(f, tl, CH_VOLTS, 0.0, 208.0, 13 * lod.COARSE_SUMMARY_PX + 2, coarse=True)
        assert len(calls) == 1                                                   # blocks four pixels wide: the summaries
        monkeypatch.setattr(lod, "COARSE_SUMMARY_RECORDS", 0)
        lod.curve_for_range(f, tl, CH_VOLTS, 0.0, 208.0, 1500, coarse=True)
        assert len(calls) == 1                                                   # too many records for the per-record path


def test_the_quick_curve_never_decodes_when_told_not_to(doc):
    with AdichtFile(doc["path"]) as f:
        tl = Timeline(f)
        # shorter than a storage block: the plain quick pass decodes the block or two it needs ...
        x, y, level = lod.curve_for_range(f, tl, CH_VOLTS, 40.0, 43.0, 200, coarse=True)
        assert f.cache_stats()[1] > 0 and level == "envelope"
        f.clear_cache()
        # ... the strip beside a decoded curve does not (index: one step per block)
        x, y, level = lod.curve_for_range(f, tl, CH_VOLTS, 40.0, 43.0, 200, coarse=True, decode=False)
        assert f.cache_stats() == (0, 0) and level == "coarse" and len(x) == 400
        assert len(np.unique(y[np.isfinite(y)])) <= 4                    # the min and max of the block (or two) it lies in
        # a strip a few pixels wide gets a few buckets, not the sixteen a whole curve has at least
        x, y, level = lod.curve_for_range(f, tl, CH_VOLTS, 40.0, 40.2, 3, coarse=True, decode=False)
        assert f.cache_stats() == (0, 0) and len(x) == 6
        # zoomed in to the samples it reads them (the block is the one the view shows, so it is in the cache)
        x, y, level = lod.curve_for_range(f, tl, CH_VOLTS, 40.0, 40.2, 300, coarse=True, decode=False)
        assert level == "samples" and len(x) in (200, 201)
