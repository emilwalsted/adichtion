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


def interleave(t: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Min/max buckets as a zig-zag line: each bucket time twice, min then max."""
    x = np.repeat(t, 2)
    y = np.column_stack([lo, hi]).ravel()
    return x, y


def curve_for_range(f: AdichtFile, timeline: Timeline, channel: int, t0: float, t1: float,
                    n_buckets: int, coarse: bool = False,
                    stale=None) -> tuple[np.ndarray, np.ndarray, str]:
    """``(x, y, level)`` for ``channel`` over file times ``[t0, t1]``; ``level`` is
    ``"samples"``, ``"envelope"`` or ``"coarse"``.  Blocks are separated by a NaN so
    the plot leaves a gap between them.  ``stale()`` may abort a long job early."""
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    levels: set[str] = set()
    n_buckets = max(16, int(n_buckets))
    span = max(t1 - t0, 1e-9)
    for b in timeline.blocks:
        lo_t, hi_t = max(t0, b.start_s), min(t1, b.stop_s)
        if hi_t <= lo_t:
            continue
        if stale is not None and stale():
            break
        rec = f.records[b.index]
        start = max(0, int(np.floor((lo_t - b.start_s) * b.tick_rate_hz)))
        stop = min(b.ticks, int(np.ceil((hi_t - b.start_s) * b.tick_rate_hz)) + 1)
        if stop <= start:
            continue
        # a share of the buckets in proportion to the time this block covers; a block narrower than
        # a pixel still gets one, so the curve has at most n_buckets plus one per such block
        n_part = max(1, int(round(n_buckets * (hi_t - lo_t) / span)))
        spt = rec.samples_per_tick(channel)
        n_samples = int(round((stop - start) * spt))
        bucket_ticks = (stop - start) / n_part
        if (stop - start) >= BLOCK_TICKS and (coarse or bucket_ticks >= BLOCK_TICKS / 4):
            # a bucket a quarter block wide or more: the block index is the envelope, to the pixel;
            # blocks without a stored min/max are decoded by the worker only, never by the quick
            # coarse pass on the GUI thread
            t, mn, mx = rec.overview(channel, n_part, start, stop, coarse=True, fill=not coarse)
            x, y = interleave(t + b.start_s, mn, mx)
            levels.add("coarse" if coarse else "envelope")
        elif n_samples <= 2 * n_part:
            y = rec.read(channel, start, stop)
            x = rec.time(start, stop, channel) + b.start_s
            levels.add("samples")
        else:
            # blocks decoded before answer from their min/max summaries when a bucket is at least
            # as wide as a summary bucket (no decoding); otherwise the samples are decoded
            t, mn, mx = rec.overview(channel, n_part, start, stop, from_summaries=True)
            x, y = interleave(t + b.start_s, mn, mx)
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
    lo, hi = np.inf, -np.inf
    for rec in f.records:
        _t0, _t1, mn, mx = rec.block_envelope(channel, fill=False)
        if np.isfinite(mn).any():
            lo = min(lo, float(np.nanmin(mn)))
            hi = max(hi, float(np.nanmax(mx)))
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
