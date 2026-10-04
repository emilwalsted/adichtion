"""Level-of-detail curves for the channel plots (Qt-free, runs in the worker thread).

A curve on the file axis is built per block overlapped by the requested range:
when the range holds fewer samples than about twice the pixel budget, the
samples themselves are used; otherwise a min/max envelope with one bucket pair
per pixel, taken from the block index, from the min/max summaries that decoding leaves
behind (``Record.summary_overview``), or from the decoded samples, in that order of preference.  ``coarse=True`` takes the envelope from the file's block index
wherever the range holds whole blocks (no decoding), which is what the plots
show first while the worker decodes the real thing.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..blocks import BLOCK_TICKS
from ..file import AdichtFile
from .timeline import Timeline


# the quick pass answers a whole record from the block index when the record spans no more buckets than this;
# at most n_buckets / COARSE_WHOLE_BUCKETS such records fit a view, so the ones left to the per-record path are few
COARSE_WHOLE_BUCKETS = 8


def interleave(t: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Min/max buckets as a zig-zag line: each bucket time twice, min then max."""
    x = np.repeat(t, 2)
    y = np.column_stack([lo, hi]).ravel()
    return x, y


def _record_curve(f: AdichtFile, timeline: Timeline, index: int, channel: int, lo_t: float, hi_t: float,
                  start: int, stop: int, n_part: int, coarse: bool) -> tuple[np.ndarray, np.ndarray, str]:
    """The curve of one record over ticks ``[start, stop)`` in ``n_part`` buckets, taking the
    block index, the samples or the block summaries, whichever the range calls for."""
    b = timeline.blocks[index]
    rec = f.records[index]
    spt = rec.samples_per_tick(channel)
    n_samples = int(round((stop - start) * spt))
    bucket_ticks = (stop - start) / n_part
    if (stop - start) >= BLOCK_TICKS and (coarse or bucket_ticks >= BLOCK_TICKS / 4):
        # a bucket a quarter block wide or more: the block index is the envelope, to the pixel;
        # blocks without a stored min/max are decoded by the worker only, never by the quick
        # coarse pass on the GUI thread
        t, mn, mx = rec.overview(channel, n_part, start, stop, coarse=True, fill=not coarse)
        x, y = interleave(t + b.start_s, mn, mx)
        level = "coarse" if coarse else "envelope"
    elif n_samples <= 2 * n_part:
        y = rec.read(channel, start, stop)
        x = rec.time(start, stop, channel) + b.start_s
        level = "samples"
    else:
        # blocks decoded before answer from their min/max summaries when a bucket is at least
        # as wide as a summary bucket (no decoding); otherwise the samples are decoded
        t, mn, mx = rec.overview(channel, n_part, start, stop, from_summaries=True)
        x, y = interleave(t + b.start_s, mn, mx)
        level = "envelope"
    return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), level


def _index_curves(f: AdichtFile, timeline: Timeline, channel: int, rec: np.ndarray,
                  start: np.ndarray, stop: np.ndarray, n_part: np.ndarray):
    """Min/max buckets of whole records straight from the channel's block table, with no loop over
    the records: record ``rec[i]`` over ticks ``[start[i], stop[i])`` in ``n_part[i]`` buckets, the
    same buckets :meth:`Record.overview` builds from the block index (``Record._spread``) but for
    every record at once.  Returns the bucket values ``(x, lo, hi)`` (file time, flat) and the
    record number of each bucket."""
    tab = f.block_table(channel)
    rate = timeline.rates[rec]
    a = start / rate
    b = stop / rate
    w = (b - a) / n_part
    off = np.cumsum(n_part) - n_part
    n_buckets = int(n_part.sum())
    # the blocks of the records in between, as one slice of the table, each assigned to its record
    p0, p1 = int(tab.first[rec[0]]), int(tab.first[rec[-1] + 1])
    owner = np.full(int(rec[-1] - rec[0]) + 1, -1, dtype=np.int64)
    owner[rec - rec[0]] = np.arange(len(rec))
    pe = owner[tab.record[p0:p1] - rec[0]]
    prate = timeline.rates[tab.record[p0:p1]]
    b0, b1 = tab.tick0[p0:p1] / prate, tab.tick1[p0:p1] / prate
    mn, mx = tab.mn[p0:p1], tab.mx[p0:p1]
    keep = (pe >= 0)
    pe = np.where(keep, pe, 0)
    keep &= (b1 > a[pe]) & (b0 < b[pe])
    pe, b0, b1, mn, mx = pe[keep], b0[keep], b1[keep], mn[keep], mx[keep]
    last = n_part[pe] - 1
    k0 = np.clip(np.floor((b0 - a[pe]) / w[pe]), 0, last).astype(np.int64)
    k1 = np.clip(np.floor((b1 - a[pe] - 1e-9) / w[pe]), 0, last).astype(np.int64)
    counts = np.maximum(k1 - k0 + 1, 0)
    base = off[pe] + k0
    if (counts == 1).all():
        ids = base
    else:       # a block wider than a bucket feeds a run of them
        ids = np.repeat(base, counts) + (np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts))
        mn, mx = np.repeat(mn, counts), np.repeat(mx, counts)
    lo = np.full(n_buckets, np.inf)
    hi = np.full(n_buckets, -np.inf)
    # fmin/fmax skip the NaN of blocks whose min/max is not stored (and not yet filled)
    np.fmin.at(lo, ids, mn)
    np.fmax.at(hi, ids, mx)
    lo[~np.isfinite(lo)] = np.nan
    hi[~np.isfinite(hi)] = np.nan
    owner_of = np.repeat(np.arange(len(rec)), n_part)
    k = np.arange(n_buckets) - off[owner_of]
    x = timeline.starts[rec][owner_of] + a[owner_of] + (k + 0.5) * w[owner_of]
    return x, lo, hi, owner_of


def curve_for_range(f: AdichtFile, timeline: Timeline, channel: int, t0: float, t1: float,
                    n_buckets: int, coarse: bool = False,
                    stale=None) -> tuple[np.ndarray, np.ndarray, str]:
    """``(x, y, level)`` for ``channel`` over file times ``[t0, t1]``; ``level`` is
    ``"samples"``, ``"envelope"`` or ``"coarse"``.  Blocks are separated by a NaN so
    the plot leaves a gap between them.  ``stale()`` may abort a long job early.

    A record whose share of the range is answered by the block index (a whole record in a few buckets,
    or a stretch of at least one storage block, see :func:`_from_index`) is handled for all such
    records at once from the channel's block table; the few others (the records at the
    ends of the range, or a range inside one record) take :func:`_record_curve` one by one."""
    n_buckets = max(16, int(n_buckets))
    span = max(t1 - t0, 1e-9)
    tl = timeline
    r0 = int(np.searchsorted(tl.stops, t0, side="right"))       # the first block that ends after t0
    r1 = int(np.searchsorted(tl.starts, t1, side="left"))       # one past the last that starts before t1
    if r1 <= r0:
        return np.zeros(0), np.zeros(0), "samples"
    rec = np.arange(r0, r1, dtype=np.int64)
    s0, rate = tl.starts[r0:r1], tl.rates[r0:r1]
    lo_t, hi_t = np.maximum(t0, s0), np.minimum(t1, tl.stops[r0:r1])
    start = np.maximum(0, np.floor((lo_t - s0) * rate)).astype(np.int64)
    stop = np.minimum(tl.block_ticks[r0:r1], np.ceil((hi_t - s0) * rate) + 1).astype(np.int64)
    inc = (hi_t > lo_t) & (stop > start)
    if not inc.any():
        return np.zeros(0), np.zeros(0), "samples"
    rec, lo_t, hi_t, start, stop = rec[inc], lo_t[inc], hi_t[inc], start[inc], stop[inc]
    # a share of the buckets in proportion to the time this record covers; a record narrower than
    # a pixel still gets one, so the curve has at most n_buckets plus one per such record
    n_part = np.maximum(1, np.rint(n_buckets * (hi_t - lo_t) / span)).astype(np.int64)
    ok = _from_index(f, tl, channel, rec, start, stop, n_part, coarse)
    if ok.any() and not coarse:
        _fill_missing(f, channel, rec[ok], stale)
    n = len(rec)
    xs: list[Optional[np.ndarray]] = [None] * n
    ys: list[Optional[np.ndarray]] = [None] * n
    levels: set[str] = {"coarse" if coarse else "envelope"} if ok.any() else set()
    size = np.where(ok, 2 * n_part, 0)
    for i in np.flatnonzero(~ok):
        if stale is not None and stale():
            break
        x, y, level = _record_curve(f, tl, int(rec[i]), channel, lo_t[i], hi_t[i], int(start[i]), int(stop[i]),
                                    int(n_part[i]), coarse)
        xs[i], ys[i] = x, y
        size[i] = len(x)
        levels.add(level)
    done = ok | np.array([x is not None for x in xs])      # a stale job leaves the rest out
    if not done.all():
        rec, start, stop, n_part, ok, size = (v[done] for v in (rec, start, stop, n_part, ok, size))
        xs = [x for x, d in zip(xs, done) if d]
        ys = [y for y, d in zip(ys, done) if d]
        n = len(rec)
        if n == 0:
            return np.zeros(0), np.zeros(0), "samples"
    out = np.cumsum(size + 1) - (size + 1)              # where each record's points start; a NaN between records
    total = int(out[-1] + size[-1])
    X = np.full(total, np.nan)
    Y = np.full(total, np.nan)
    if ok.any():
        e = np.flatnonzero(ok)
        bx, blo, bhi, owner = _index_curves(f, tl, channel, rec[e], start[e], stop[e], n_part[e])
        first = np.cumsum(n_part[e]) - n_part[e]
        pos = out[e][owner] + 2 * (np.arange(len(bx)) - first[owner])
        X[pos] = X[pos + 1] = bx
        Y[pos], Y[pos + 1] = blo, bhi
    for i in np.flatnonzero(~ok):
        X[out[i]:out[i] + size[i]] = xs[i]
        Y[out[i]:out[i] + size[i]] = ys[i]
    level = "coarse" if "coarse" in levels else "envelope" if "envelope" in levels else "samples"
    return X, Y, level


def _fill_missing(f: AdichtFile, channel: int, rec: np.ndarray, stale=None) -> None:
    """Decode the blocks of ``rec`` whose min/max the index lacks (a Mac document's), and only those, so the
    table answers for them; ``stale()`` is checked between records.  The quick pass never calls this."""
    tab = f.block_table(channel)
    if tab.complete:
        return
    for r in rec:
        a, b = int(tab.first[r]), int(tab.first[r + 1])
        if np.isnan(tab.mn[a:b]).any() or np.isnan(tab.mx[a:b]).any():
            if stale is not None and stale():
                return
            f.block_minmax(int(r), channel, True)


def _from_index(f: AdichtFile, timeline: Timeline, channel: int, rec: np.ndarray, start: np.ndarray,
                stop: np.ndarray, n_part: np.ndarray, coarse: bool) -> np.ndarray:
    """Which of the records the block index answers.  The rule the curve always had: a share of at
    least one storage block (``BLOCK_TICKS``), and for the decoded pass a bucket a quarter block wide
    or more.  Added with the block table: a WHOLE record in one bucket (the record's own min/max, which
    is exactly what decoding it would give) in both passes, and for the quick pass in up to
    ``COARSE_WHOLE_BUCKETS`` buckets (each then shows the record's min/max, a first picture the decoded curve
    replaces); that keeps a file of thousands of short records off the decoder in the GUI thread."""
    covered = stop - start
    # a record of a couple of ticks keeps its samples (the old rule), the index only answers real envelopes
    whole = (start == 0) & (stop == timeline.block_ticks[rec]) & (covered > 2 * n_part)
    if coarse:
        ok = (covered >= BLOCK_TICKS) | (whole & (n_part <= COARSE_WHOLE_BUCKETS))
    else:
        ok = ((covered >= BLOCK_TICKS) & (covered / n_part >= BLOCK_TICKS / 4)) | (whole & (n_part == 1))
    if ok.any():
        first = f.block_table(channel).first
        ok &= first[rec + 1] > first[rec]               # the channel has data in the record
    return ok


def value_at(f: AdichtFile, timeline: Timeline, channel: int, t: float) -> Optional[float]:
    """The sample of ``channel`` at file time ``t`` (in its units), or None outside the data."""
    pos = timeline.locate(t)
    if pos is None:
        return None
    rec = f.records[pos.block.index]
    # the sample at or before the tick: a channel sampled below the tick rate (a Mac
    # document's 100 Hz pressures on a 4 kHz tick) has a value between its samples too
    return rec.sample_at(channel, pos.tick)


def global_range(f: AdichtFile, channel: int) -> Optional[tuple[float, float]]:
    """Min/max of ``channel`` over the whole file, from the block index (nothing decoded)."""
    tab = f.block_table(channel, fill=False)
    if not np.isfinite(tab.mn).any() or not np.isfinite(tab.mx).any():
        return None
    lo, hi = float(np.nanmin(tab.mn)), float(np.nanmax(tab.mx))
    if not np.isfinite(lo) or not np.isfinite(hi):
        return None
    return lo, hi


def interval_stats(f: AdichtFile, timeline: Timeline, channel: int, t0: float, t1: float,
                   max_decoded_blocks: int = 400, stale=None) -> dict:
    """Min, max and mean of ``channel`` over file times ``[t0, t1]``.

    Whole blocks inside the range take their min/max from the block index; the mean
    needs the samples, so it is only computed when the range holds at most
    ``max_decoded_blocks`` blocks (else ``mean`` is None and ``decoded`` False)."""
    sels = timeline.selections(t0, t1)
    n_blocks = sum(int(np.ceil(s.ticks / BLOCK_TICKS)) for s in sels)
    lo, hi = np.inf, -np.inf
    total = 0.0
    count = 0
    decoded = n_blocks <= max_decoded_blocks
    for s in sels:
        if stale is not None and stale():
            return {"min": None, "max": None, "mean": None, "n": 0, "decoded": False}
        rec = f.records[s.record]
        if decoded:
            x = rec.read(channel, s.start, s.stop)
            if len(x):
                lo, hi = min(lo, float(x.min())), max(hi, float(x.max()))
                total += float(x.sum())
                count += len(x)
        else:
            b0, b1, mn, mx = rec.block_envelope(channel)
            r = rec.tick_rate_hz
            keep = (b1 > s.start / r) & (b0 < s.stop / r)
            if keep.any():
                lo, hi = min(lo, float(np.nanmin(mn[keep]))), max(hi, float(np.nanmax(mx[keep])))
            count += rec.tick_to_sample(s.stop, channel) - rec.tick_to_sample(s.start, channel)
    if count == 0 or not np.isfinite(lo):
        return {"min": None, "max": None, "mean": None, "n": 0, "decoded": decoded}
    return {"min": lo, "max": hi, "mean": (total / count) if decoded else None, "n": count, "decoded": decoded}
