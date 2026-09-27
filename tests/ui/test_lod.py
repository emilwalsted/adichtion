"""Level-of-detail curves and cursor statistics (Qt-free)."""
import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.ui import lod
from adichtion.ui.timeline import Timeline


@pytest.fixture
def opened(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        yield f, Timeline(f)


def test_samples_when_zoomed_in_envelope_when_not(opened):
    f, tl = opened
    x, y, level = lod.curve_for_range(f, tl, 1, 10.0, 10.5, 1000)
    assert level == "samples" and len(x) == len(y) and 500 <= len(x) <= 502
    assert np.allclose(y, f.records[0].read(1, 10000, 10000 + len(y)))
    assert np.allclose(x, 10.0 + np.arange(len(x)) / 1000.0)
    x, y, level = lod.curve_for_range(f, tl, 1, 0.0, 45.0, 400)
    assert level == "envelope"
    # min/max pairs, a NaN gap between the two blocks, nothing beyond the data
    assert np.isnan(y).sum() == 1 and np.nanmin(x) >= 0 and np.nanmax(x) <= 45.0
    finite = y[np.isfinite(y)]
    assert finite.min() >= f.records[0].read(1).min() - 1e-9
    coarse = lod.curve_for_range(f, tl, 1, 0.0, 45.0, 400, coarse=True)
    assert coarse[2] == "coarse" and len(coarse[0]) > 0


def test_value_at_and_global_range(opened):
    f, tl = opened
    v = lod.value_at(f, tl, 1, 42.5)
    assert v == pytest.approx(float(f.records[1].read(1, 2500, 2501)[0]))
    assert lod.value_at(f, tl, 0, 1e9) is not None
    lo, hi = lod.global_range(f, 1)
    data = np.concatenate([r.read(1) for r in f.records])
    assert lo == pytest.approx(data.min()) and hi == pytest.approx(data.max())


def test_interval_stats(opened):
    f, tl = opened
    s = lod.interval_stats(f, tl, 1, 39.0, 41.0)
    ref = np.concatenate([f.records[0].read(1, 39000, 40000), f.records[1].read(1, 0, 1000)])
    assert s["decoded"] and s["n"] == 2000
    assert s["min"] == pytest.approx(ref.min()) and s["max"] == pytest.approx(ref.max())
    assert s["mean"] == pytest.approx(ref.mean())
    # beyond the decoding budget the extremes come from the block index and the mean is dropped
    s2 = lod.interval_stats(f, tl, 1, 0.0, 45.0, max_decoded_blocks=1)
    assert not s2["decoded"] and s2["mean"] is None and s2["min"] <= ref.min() and s2["max"] >= ref.max()
