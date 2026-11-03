"""Very large recordings: ``synth.write_large_synthetic`` and a file of more than 2 GB.

The quick test checks the generator on a few megabytes, block by block.  ``test_two_gigabytes`` (marked
``slow``: ``--runslow`` or ``ADICHTION_SLOW=1``; it needs about 2.3 GB of free disk) writes a 2.2 GB file
in the test's temporary folder and deletes it at the end (pytest keeps its last temporary folders), and checks that it opens on the file's mapping,
that the memory the reader itself allocates does not follow the file size, and that blocks anywhere in
the file decode sample for sample.  The measurements behind it are in docs/FORMAT.md §9.
"""
import os
import sys

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.synth import write_large_synthetic


def _variant(info, c, g):
    return info["variants"][c][(g * 7 + c) % len(info["variants"][c])]


def test_large_synthetic_generator_is_sample_exact(tmp_path):
    p = tmp_path / "large.adicht"
    info = write_large_synthetic(p, 3_000_000, n_channels=3, n_records=2)
    with AdichtFile(p) as f:
        assert f.parse_mode == "sequential" and f.n_channels == 3
        assert [r.ticks for r in f.records] == info["ticks"]
        g = 0
        for r in f.records:
            for b in range(len(r.block_ticks)):
                for c in range(3):
                    np.testing.assert_array_equal(f.decode_block(r.index, b, c, cache=False), _variant(info, c, g))
                g += 1


def _anon_mib():
    """Anonymous resident memory of this process (Linux); mapped file pages are not counted."""
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("RssAnon:"):
                return int(line.split()[1]) / 1024
    return None


@pytest.mark.slow
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads RssAnon from /proc")
def test_two_gigabytes(tmp_path):
    p = tmp_path / "two-gigabytes.adicht"
    size = 2_200_000_000
    info = write_large_synthetic(p, size)
    assert os.path.getsize(p) >= 2 * 1024 ** 3
    before = _anon_mib()
    with AdichtFile(p) as f:
        assert f.zero_copy                       # the samples are a window on the mapping, not a copy
        opened = _anon_mib() - before
        # the block index costs about 4 % of the file (about 85 MiB here); the samples cost nothing
        assert opened < 0.08 * size / 2 ** 20, opened
        rng = np.random.default_rng(3)
        starts = np.cumsum([0] + info["blocks"])
        for g in rng.integers(0, starts[-1], 60):
            r = int(np.searchsorted(starts, g, side="right") - 1)
            for c in range(f.n_channels):
                np.testing.assert_array_equal(f.decode_block(r, int(g - starts[r]), c, cache=False),
                                              _variant(info, c, int(g)))
        t = f.block_table(0)
        assert t.complete and len(t.mn) == starts[-1]
        rec = f.records[1]
        x = rec.read_raw(2, rec.ticks - 16000, rec.ticks)        # the last block of the file
        np.testing.assert_array_equal(x, _variant(info, 2, int(starts[-1]) - 1))
    os.remove(p)
