"""Integration tests against real LabChart for Mac documents (docs/MAC_FORMAT.md).  They run
only when ADICHTION_SAMPLES points to a folder with real files (private data, never
committed).  Documents are found anywhere below it by their first bytes (they have no
extension); a document is paired with a ``.adicht`` there when every block has the same tick
count: that is its WinChart export, written by LabChart for Mac itself, and the reference.

A document can be newer than its export (a comment moved or added, a channel switched back
to the raw input): the comparison allows one comment per document to differ and compares
only channels the export holds as raw int16 data.
"""
import glob
import os

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.macchart import KEY_N_CHANNELS, MacResFile, channel_settings, document_properties
from adichtion.macdoc import is_mac_document
from conftest import samples_dir

pytestmark = pytest.mark.skipif(samples_dir() is None, reason="ADICHTION_SAMPLES not set")


def mac_files() -> list[str]:
    """Every LabChart for Mac file below ADICHTION_SAMPLES: documents and settings files."""
    return [path for path in sorted(glob.glob(os.path.join(samples_dir(), "**", "*"), recursive=True))
            if os.path.isfile(path) and not path.lower().endswith(".adicht") and is_mac_document(path)]


def mac_documents() -> list[str]:
    """Documents with data; settings files share the first bytes and are left out."""
    return [path for path in mac_files() if MacResFile(path).is_document]


def _ticks(path: str) -> list[int]:
    with AdichtFile(path) as f:
        return [r.ticks for r in f.records]


def pairs() -> list[tuple[str, str]]:
    exports = {p: _ticks(p) for p in glob.glob(os.path.join(samples_dir(), "**", "*.adicht"), recursive=True)}
    out = []
    for doc in mac_documents():
        t = _ticks(doc)
        match = [p for p, v in exports.items() if v == t]
        if match:
            out.append((doc, match[0]))
    return out


def test_every_document_agrees_with_its_own_block_index():
    docs = mac_documents()
    if not docs:
        pytest.skip("no LabChart for Mac documents below ADICHTION_SAMPLES")
    for path in docs:
        with AdichtFile(path, cache_blocks=4) as f:
            assert f.kind == "mac" and f.n_channels >= 1
            for rec in f.records:
                assert rec.tick_rate_hz > 0
                for ch in range(f.n_channels):
                    if not f.has_data(ch):
                        continue
                    decimated = f.index.decimation(rec.index, ch) > 1
                    for b, blk in enumerate(f.channel_record(rec.index, ch).blocks):
                        x = f.decode_block(rec.index, b, ch)
                        assert len(x) == -(-blk.ticks // f.index.decimation(rec.index, ch))
                        if np.isnan(blk.min):
                            continue
                        if decimated:           # the index envelope is taken before decimation
                            assert blk.min <= x.min() and x.max() <= blk.max, (path, rec.index, ch, b)
                        else:
                            assert (x.min(), x.max()) == (blk.min, blk.max), (path, rec.index, ch, b)


def test_documents_match_their_winchart_exports():
    found = pairs()
    if not found:
        pytest.skip("no document below ADICHTION_SAMPLES has a .adicht export next to it")
    for doc, exp in found:
        with AdichtFile(doc) as m, AdichtFile(exp) as e:
            compared = 0
            for rm, re_ in zip(m.records, e.records):
                assert rm.tick_rate_hz == re_.tick_rate_hz
                # the export rounds the start to 10 ms; the document keeps the finer value
                assert abs(rm.start_utc.timestamp() - re_.start_utc.timestamp()) <= 0.0051, (doc, rm.index)
                for ch in range(min(m.n_channels, e.n_channels)):
                    if not m.has_data(ch) or e.index.channel_dtype(ch) != "int16":
                        continue
                    d = m.index.decimation(rm.index, ch)
                    a = re_.read_raw(ch)
                    x = rm.read_raw(ch)
                    assert len(x) == -(-len(a) // d), (doc, rm.index, ch)
                    np.testing.assert_array_equal(a[::d], x, err_msg=f"{doc} record {rm.index} channel {ch}")
                    um, ue = rm.units(ch), re_.units(ch)
                    assert um.lsb == pytest.approx(ue.lsb, rel=1e-12)
                    assert um.enabled == ue.enabled
                    if ue.enabled:
                        assert (um.slope, um.offset, um.units) == (pytest.approx(ue.slope, rel=1e-12),
                                                                   pytest.approx(ue.offset, rel=1e-12, abs=1e-12), ue.units)
                    compared += 1
            assert compared > 0, doc
            mine = {(c.record_index, c.tick, c.channel, c.text) for c in m.comments}
            theirs = {(c.record_index, c.tick, c.channel, c.text) for c in e.comments}
            assert len(mine ^ theirs) <= 2, (doc, len(mine - theirs), len(theirs - mine))


def test_every_calculated_channel_has_a_complete_definition():
    """The settings keys move with the extension registry (CALCULATED_CHANNELS.md §1): with
    them resolved, no Arithmetic channel lacks its expression and no RMS or spirometry
    channel its source, in documents and settings files alike."""
    files = mac_files()
    if not files:
        pytest.skip("no LabChart for Mac files below ADICHTION_SAMPLES")
    for path in files:
        f = MacResFile(path)
        n = document_properties(f).get(KEY_N_CHANNELS)
        for ch, s in channel_settings(f).items():
            if not s.is_calculated or (n and ch > n):
                continue
            where = (os.path.basename(path), ch, s.describe())
            if s.calculation == "Arithmetic":
                assert s.expression, where
            if s.calculation in ("RMS", "Spiro. Flow", "Spiro. Volume"):
                assert s.source, where
            if s.calculation == "Spiro. Flow":
                assert s.flow_calibration is not None, where
            if s.calculation == "Spiro. Volume":
                assert s.volume_ratio is not None, where


def test_upsampling_matches_the_winchart_exports():
    """``Record.read_ticks`` fills the ticks between a slow channel's samples as LabChart's
    export does: identical except where LabChart broke an exact half-count tie the other
    way (a few thousand of ~378 million values), never by more than one count."""
    found = pairs()
    if not found:
        pytest.skip("no document below ADICHTION_SAMPLES has a .adicht export next to it")
    total = same = 0
    for doc, exp in found:
        with AdichtFile(doc) as m, AdichtFile(exp) as e:
            for rm, re_ in zip(m.records, e.records):
                for ch in range(min(m.n_channels, e.n_channels)):
                    if not m.has_data(ch) or e.index.channel_dtype(ch) != "int16" or m.index.decimation(rm.index, ch) == 1:
                        continue
                    diff = rm.read_ticks(ch, units=False) - re_.read_raw(ch)
                    assert np.abs(diff).max() <= 1, (doc, rm.index, ch)
                    total += len(diff)
                    same += int(np.count_nonzero(diff == 0))
    assert total > 0 and same / total >= 0.9999, (same, total)


def test_movies_are_timed_by_their_frame_stamps():
    """Every readable movie in a document's ``<name> movies`` folder carries one stamp per
    frame on the clock of the document's mTim (MAC_FORMAT.md §7): its first frame comes
    within 0.2 s after the block's start, one offset (a few ms) fits every later frame,
    and the movie lasts as long as the block."""
    from adichtion.qtmovie import read_movie_timing
    checked = 0
    for doc in mac_documents():
        with AdichtFile(doc) as f:
            for v in f.videos:
                path = os.path.join(f.movies_folder, v.file_name)
                if not os.path.isfile(path) or os.path.getsize(path) == 0:     # absent, or a cloud placeholder
                    continue
                where = (os.path.basename(doc), v.file_name)
                assert v.stamped, where
                tm = read_movie_timing(path)
                rec = f.records[v.record_index]
                clock0 = f.mac.record_clock_us[v.record_index]
                assert 0.0 <= (tm.stamps_us[0] - clock0) / 1e6 < 0.2, where
                d = tm.frame_times_s[1:] - (tm.stamps_us[1:] - clock0) / 1e6
                assert d.max() - d.min() < 1e-4 and 0.0 <= v.offset_s < 0.05, where
                assert np.all(np.diff(tm.stamps_us) > 0), where
                assert abs(tm.duration_s - rec.duration_s) < 0.5, where
                checked += 1
    if not checked:
        pytest.skip("no readable LabChart for Mac movie below ADICHTION_SAMPLES")
