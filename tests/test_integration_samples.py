"""Integration tests against real recordings.  They run only when the
environment variable ADICHTION_SAMPLES points to a folder that contains
``reference-text.adicht`` and its LabChart text
exports under ``export/`` (private data, never committed)."""
import glob
import os

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.export import read_labchart_text
from conftest import samples_dir

pytestmark = pytest.mark.skipif(samples_dir() is None, reason="ADICHTION_SAMPLES not set")


def test_all_files_open_and_decode():
    for path in glob.glob(os.path.join(samples_dir(), "**", "*.adicht"), recursive=True):
        with AdichtFile(path) as f:
            assert f.n_channels >= 1
            for rec in f.records[:3]:
                for ch in range(f.n_channels):
                    x = rec.read_raw(ch, 0, min(rec.ticks, 40000))
                    assert len(x) == min(rec.ticks, 40000) * rec.samples_per_tick(ch)
                    if x.dtype == np.int32:
                        assert np.abs(x).max() <= 32768


def test_pmo_matches_labchart_export():
    d = samples_dir()
    adicht = os.path.join(d, "reference-text.adicht")
    exports = sorted(glob.glob(os.path.join(d, "export", "reference-text*.txt")))
    if not (os.path.exists(adicht) and exports):
        pytest.skip("reference pair not present")
    with AdichtFile(adicht) as f:
        rec = f.records[23]
        for path in exports:
            _h, t, vals, _c = read_labchart_text(path)
            start = int(round(t[0] * 1000))
            pmo = rec.read(1, start, start + len(t))
            assert np.abs(pmo - vals[:, 1]).max() < 1e-6          # sample-exact in inH2O
            flow = rec.read_raw(0, start, start + len(t)).astype(float)
            k = np.dot(flow, vals[:, 0]) / np.dot(flow, flow)     # export is in L/s: one scale factor
            assert np.abs(k * flow - vals[:, 0]).max() < 2e-6
