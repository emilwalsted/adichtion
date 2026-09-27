"""Decimation helpers for drawing long signals at screen resolution."""
from __future__ import annotations

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
