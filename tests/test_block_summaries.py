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
        # the stored dtype is the decoder's (int32 counts or float32), never a converted value
        assert f.summary_store(0, channel).mins.dtype == f.decode_block(0, 0, channel).dtype


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
