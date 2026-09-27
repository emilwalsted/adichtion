"""Integration tests against real recordings.  They run only when the
environment variable ADICHTION_SAMPLES points to a folder with real files
(private data, never committed):

* ``*.adicht`` anywhere below it: every file must parse by the full grammar,
  its block index must agree with a walk of the Blocks_0 stream and every
  block's decoded min/max must match the values LabChart stored;
* ``reference-text.adicht`` + ``export/reference-text*.txt``: LabChart text exports (int16 channels, units conversion);
* ``reference-matlab.adicht`` + ``oracle/reference-matlab - <stage>.mat``: LabChart
  MATLAB exports of a file with calculated (float32) channels.
"""
import glob
import os

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.export import read_labchart_text
from conftest import samples_dir

pytestmark = pytest.mark.skipif(samples_dir() is None, reason="ADICHTION_SAMPLES not set")


def sample_files():
    return sorted(glob.glob(os.path.join(samples_dir(), "**", "*.adicht"), recursive=True))


def test_all_files_parse_by_the_grammar():
    for path in sample_files():
        with AdichtFile(path, cache_blocks=4) as f:
            assert f.parse_mode == "sequential", path
            assert f.n_channels == f.chart.n_channels >= 1
            walk = f.walk_subblocks()
            assert [(s.offset, s.body_size) for s in walk] == [(s.offset, s.body_size) for s in f.index.subblocks]
            for rec in f.records:
                assert rec.info.channels and all(cr.units is not None for cr in rec.info.channels)
                assert [b.ticks for b in rec.info.channels[0].blocks] == rec.block_ticks
            for ch in range(f.n_channels):
                u = f.units_for(ch)
                assert u.has_lsb and u.lsb > 0
                if f.chart.setups:                                   # v29: the XML range and the LSB agree
                    if f.index.channel_dtype(ch) == "int16":
                        assert f.chart.setups[ch].range_volts == pytest.approx(f.range_volts(ch))


def test_block_min_max_match_the_stored_index():
    """LabChart stores min/max per block; the decoders must reproduce them
    (exactly for int16; float32 values are sample-exact against the MATLAB
    export while the stored extrema deviate by a few ulp, so 1e-6 relative)."""
    for path in sample_files():
        with AdichtFile(path, cache_blocks=4) as f:
            for rec in f.records:
                for ch in range(f.n_channels):
                    for b, blk in enumerate(f.channel_record(rec.index, ch).blocks):
                        x = f.decode_block(rec.index, b, ch)
                        mn, mx = float(x.min()), float(x.max())
                        if x.dtype == np.int32:
                            assert (mn, mx) == (blk.min, blk.max), (path, rec.index, ch, b)
                        else:
                            tol = 1e-6 * max(1.0, abs(mn), abs(mx))
                            assert abs(mn - blk.min) <= tol and abs(mx - blk.max) <= tol, (path, rec.index, ch, b)


def test_pmo_matches_labchart_export():
    d = samples_dir()
    adicht = os.path.join(d, "reference-text.adicht")
    exports = sorted(glob.glob(os.path.join(d, "export", "reference-text*.txt")))
    if not (os.path.exists(adicht) and exports):
        pytest.skip("reference pair not present")
    with AdichtFile(adicht) as f:
        rec = f.records[23]
        assert rec.value_label(1) == "converted"          # the units name is not stored in this file
        for path in exports:
            _h, t, vals, _c = read_labchart_text(path)
            start = int(round(t[0] * 1000))
            pmo = rec.read(1, start, start + len(t))
            assert np.abs(pmo - vals[:, 1]).max() < 1e-6          # sample-exact in the converted units
            flow = rec.read_raw(0, start, start + len(t)).astype(float)
            k = np.dot(flow, vals[:, 0]) / np.dot(flow, flow)     # export is in L/s: one scale factor
            assert np.abs(k * flow - vals[:, 0]).max() < 2e-6


def test_calculated_channels_match_matlab_export():
    """Raw EMG (int16), an arithmetic channel (Pdi, float32 with units
    conversion) and RMS channels (float32 without) against a LabChart MATLAB
    export of the same recording.  The export window is located by matching
    the first channel's counts."""
    d = samples_dir()
    adicht = os.path.join(d, "reference-matlab.adicht")
    mats = sorted(glob.glob(os.path.join(d, "oracle", "reference-matlab - *.mat")))
    if not (os.path.exists(adicht) and mats):
        pytest.skip("reference pair not present")
    sio = pytest.importorskip("scipy.io")
    with AdichtFile(adicht, cache_blocks=16) as f:
        rec = f.records[3]
        emg = rec.read_raw(0).astype(np.int64)
        for mat in mats:
            m = sio.loadmat(mat)
            ref0 = m["C1B4"].ravel().astype(np.float64)
            counts = np.round(ref0 / f.units_for(0).lsb).astype(np.int64)
            cand = np.flatnonzero(emg[: len(emg) - 64] == counts[0])
            hits = [i for i in cand if np.array_equal(emg[i : i + 64], counts[:64])]
            assert len(hits) == 1, mat
            i0, n = hits[0], len(counts)
            for ch, tol in [(0, 0), (1, 0), (4, 0), (5, 1e-6), (12, 1e-6), (13, 1e-6), (16, 1e-6)]:
                ref = m[f"C{ch + 1}B4"].ravel().astype(np.float64)
                val = rec.read(ch, i0, i0 + n)
                if tol == 0:
                    assert np.array_equal(np.round(val / f.units_for(ch).lsb), np.round(ref / f.units_for(ch).lsb))
                else:
                    assert np.all(np.abs(val - ref) <= tol * np.maximum(1.0, np.abs(ref))), (mat, ch)
        assert rec.value_label(5) == "cmH2O" and rec.value_label(12) == "V"


def test_inference_explains_the_calculated_channels():
    """Every float32 channel of the files with calculated channels must be explained
    by a standard LabChart calculation of the other channels (exactly, or within the
    count resolution of the raw inputs), and the kinds must match the channel titles
    of the study (Pdi: arithmetic of two pressures; Spiro Flow: scaled and zero
    corrected raw flow; Spiro Vol: spirometry volume or plain integral; RMS ...:
    moving RMS of one raw channel).  The record analysed is the longest one."""
    from adichtion.infer import infer_record

    checked = 0
    for path in sample_files():
        with AdichtFile(path, cache_blocks=64) as f:
            targets = [c for c in range(f.n_channels) if f.index.channel_dtype(c) == "float32"]
            if not targets:
                continue
            rec = max(f.records, key=lambda r: r.ticks)
            results = infer_record(rec, targets, length=200_000)
            for c, hyps in results.items():
                title = f.channel_titles[c]
                assert hyps, (path, title)
                best = hyps[0]
                assert best.exact or best.within_lsb, (path, title, str(best))
                if title == "Pdi":
                    assert best.kind == "arithmetic" and len(best.sources) == 2
                elif title.startswith("RMS"):
                    assert best.kind == "rms" and best.params["window"] == 200
                elif title == "Spiro Flow":
                    assert best.kind in ("spiro_flow", "copy", "arithmetic") and len(best.sources) == 1
                elif title == "Spiro Vol":
                    assert best.kind in ("spiro_volume", "integral")
                    if best.kind == "spiro_volume":
                        assert best.params["dead_space"] == 0.4 and best.params["corrected_sign"] == -1
                elif title.startswith("Vol"):
                    assert best.kind == "integral"
            checked += 1
    if not checked:
        pytest.skip("no file with calculated channels")
