"""Rate conversion: min/max decimation for drawing long signals at screen resolution, and
the up-sampling LabChart's own export applies to channels stored below the tick rate."""
from __future__ import annotations

from typing import Optional

import numpy as np


def minmax_envelope(x: np.ndarray, n_buckets: int) -> tuple[np.ndarray, np.ndarray]:
    """Split ``x`` into ``n_buckets`` (nearly) equal buckets and return the
    per-bucket minimum and maximum.  Drawing both as a filled band or as
    min/max line pairs preserves spikes that plain subsampling would lose.
    When ``x`` has fewer samples than ``n_buckets`` both arrays equal ``x``.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    if n_buckets <= 0 or n <= n_buckets:
        return x.copy(), x.copy()
    edges = np.linspace(0, n, n_buckets + 1).astype(np.int64)
    mins = np.minimum.reduceat(x, edges[:-1])
    maxs = np.maximum.reduceat(x, edges[:-1])
    return mins, maxs


def minmax_interleaved(x: np.ndarray, n_buckets: int) -> np.ndarray:
    """Return a ``2 * n_buckets`` array alternating min and max per bucket, the
    classic input for a fast line plot of a decimated signal."""
    mins, maxs = minmax_envelope(x, n_buckets)
    return np.column_stack([mins, maxs]).ravel()


def upsample_like_labchart(samples: np.ndarray, d: int, start: int, stop: int, first: int = 0,
                           total: Optional[int] = None) -> np.ndarray:
    """Values at every tick in ``[start, stop)`` of a channel that stores every ``d``-th
    tick, as LabChart's own export writes them (``docs/MAC_FORMAT.md`` §4).

    ``samples`` are the stored values from sample number ``first`` on (sample *k* is at
    tick ``k * d``); ``total`` is the channel's number of samples in the record (default:
    ``first + len(samples)``).  Between two samples the value is interpolated linearly;
    whole-number samples (ADC counts) are rounded to whole counts, half away from zero,
    others are left unrounded.  After the last sample its value is held.  The samples
    must cover ``start`` and the sample after ``stop - 1`` where there is one."""
    x = np.asarray(samples)
    total = first + len(x) if total is None else total
    ticks = np.arange(start, stop, dtype=np.int64)
    k = ticks // d                                   # the sample at or before each tick
    j = ticks - k * d
    nxt = np.minimum(k + 1, total - 1)
    i0, i1 = k - first, nxt - first
    if len(ticks) and (i0.min() < 0 or i1.max() >= len(x)):
        raise ValueError("the samples passed do not cover the ticks asked for")
    held = k + 1 >= total                            # after the last sample: hold it
    if np.issubdtype(x.dtype, np.integer):
        x0 = x[i0].astype(np.int64)
        x1 = x[i1].astype(np.int64)
        num = x0 * d + (x1 - x0) * j                 # d times the interpolated value, exactly
        q = np.where(num >= 0, (2 * num + d) // (2 * d), -((-2 * num + d) // (2 * d)))
        return np.where(held, x0, q)
    x0 = x[i0].astype(np.float64)
    x1 = x[i1].astype(np.float64)
    return np.where(held, x0, x0 + (x1 - x0) * j / d)
