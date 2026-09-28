"""LabChart for Mac documents: a synthetic document written by ``synth.write_mac_document``
must read back exactly through the same API as a ``.adicht`` file (docs/MAC_FORMAT.md)."""
import math
import os
import struct

import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.cli import main as cli_main
from adichtion.export import export_selections, whole_file
from adichtion.macchart import MAGIC
from adichtion.macdoc import MacFormatError, decode_mac_block, ext80, is_mac_document, read_mac_document
from adichtion.synth import SynthMacChannel, SynthMacRecord, _ext80, encode_mac_block, write_mac_document

TICK_RATE = 4000
STARTS = (1_700_000_000.0, 1_700_000_100.25, 1_700_000_300.5)
TICKS = (40000, 5000, 16192)            # 3 storage blocks; a single one; a full one plus a 192-tick tail


def _walk(rng, n, step):
    return np.cumsum(rng.integers(-step, step + 1, n)).astype(np.int64)


@pytest.fixture(scope="module")
def mac(tmp_path_factory):
    rng = np.random.default_rng(7)
    channels = [
        SynthMacChannel("EMGdi1", 1, "V", 2.048),                                   # every tick
        SynthMacChannel("Pdi", 0, calculation="Arithmetic…", expression="Ch4-Ch3"),  # calculated: not stored
        SynthMacChannel("Poes", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True), no_minmax_blocks=[(0, 1)]),
        SynthMacChannel("Pgas", 40, "V", 10.24, units=("cmH2O", 35.1, -3.2, True), raw_blocks=[(2, 1)]),
        SynthMacChannel("Flow", 40, "mV", 102.4, units=("L/s", 2.0, 0.0, False)),   # a 100 mV range, conversion off
    ]
    records = []
    for r, (ticks, start) in enumerate(zip(TICKS, STARTS)):
        data = [_walk(rng, ticks, 400), None] + [_walk(rng, -(-ticks // 40), 30) for _ in range(3)]
        comments = [(100, -1, "start"), (min(16005, ticks - 1), 2, "15,5 cmH2O"), (ticks - 1, -1, f"end {r + 1}")]
        records.append(SynthMacRecord(ticks, data, start, comments=comments, marks=[ticks // 2] if r == 1 else []))
    path = tmp_path_factory.mktemp("mac") / "Synthetic document"          # no extension, like the real ones
    # record 1's capture kept no movie (an empty entry, as in one real document)
    write_mac_document(path, channels, records, tick_rate=TICK_RATE,
                       movies={0: "", 1: "LabChartMovie1.mov", 2: "LabChartMovie2.mov"})
    return {"path": str(path), "channels": channels, "records": records}


def test_recognised_by_its_first_bytes(mac, tmp_path):
    assert is_mac_document(mac["path"])
    other = tmp_path / "x.adicht"
    other.write_bytes(b"not a LabChart for Mac file")
    assert not is_mac_document(other) and not is_mac_document(tmp_path / "missing")


def test_description(mac):
    with AdichtFile(mac["path"]) as f:
        assert f.kind == "mac" and f.format_version == 0
        assert f.format_label == "LabChart for Mac document (7.2)"
        assert f.mac.created_version == (7, 0, 0)
        assert f.channel_titles == ["EMGdi1", "Pdi", "Poes", "Pgas", "Flow"]
        assert [f.has_data(c) for c in range(5)] == [True, False, True, True, True]
        assert [f.index.channel_dtype(c) for c in range(5)] == ["int16", "none", "int16", "int16", "int16"]
        assert f.channel_definition(1) == "Arithmetic: Ch4-Ch3"
        assert f.channel_definition(0) is None
        assert f.movie_files == ["LabChartMovie1.mov", "LabChartMovie2.mov"] and f.videos == []
        assert f.mac.record_movies == [None, "LabChartMovie1.mov", "LabChartMovie2.mov"]
        assert f.walk_subblocks() == []


def test_records_and_times(mac):
    with AdichtFile(mac["path"]) as f:
        assert [r.ticks for r in f.records] == list(TICKS)
        assert [r.block_ticks for r in f.records] == [[16000, 16000, 8000], [5000], [16000, 192]]
        # the rate comes from the clock steps; the single-block record borrows it
        assert [r.tick_rate_hz for r in f.records] == [TICK_RATE] * 3
        for r, start in zip(f.records, STARTS):
            assert r.start_utc.timestamp() == pytest.approx(start, abs=1e-6)
            assert r.start_local.utcoffset().total_seconds() == 0      # local wall-clock time, as the export writes it


def test_samples_exact(mac):
    with AdichtFile(mac["path"]) as f:
        for r, rec in zip(f.records, mac["records"]):
            for c in (0, 2, 3, 4):
                np.testing.assert_array_equal(r.read_raw(c), rec.data[c])
            assert r.samples_per_tick(0) == 1.0 and r.samples_per_tick(2) == 1 / 40
            assert r.sample_rate_hz(2) == 100.0
            assert r.n_samples(2) == -(-rec.ticks // 40)               # sample k is at tick 40 k
            assert len(r.read_raw(1)) == 0 and r.n_samples(1) == 0      # the calculated channel has no data
        r = f.records[0]
        np.testing.assert_array_equal(r.read_raw(2, 80, 200), mac["records"][0].data[2][2:5])   # ticks 80, 120, 160
        np.testing.assert_array_equal(r.read_raw(2, 81, 200), mac["records"][0].data[2][3:5])
        assert r.time(0, 400, 2)[:3].tolist() == [0.0, 0.01, 0.02]


def test_the_raw_float32_block(mac):
    """Record 3's 192-tick tail of Pgas is stored as raw float32 (5 samples)."""
    with AdichtFile(mac["path"]) as f:
        blk = f.index.get(2, 1, 3)
        assert not blk.packed and blk.n_samples == 5 and blk.length == 20
        np.testing.assert_array_equal(f.decode_block(2, 1, 3), mac["records"][2].data[3][400:])


def test_units_and_ranges(mac):
    with AdichtFile(mac["path"]) as f:
        r = f.records[0]
        u = r.units(2)
        assert (u.units, u.slope, u.offset, u.enabled) == ("cmH2O", pytest.approx(33.9), pytest.approx(1.4), True)
        assert u.lsb == 10.24 / 32768 and f.range_volts(2) == pytest.approx(10.0)
        assert f.records[0].units(0).lsb == 2.048 / 32768 and not f.records[0].units(0).enabled
        flow = r.units(4)
        assert flow.lsb == pytest.approx(0.1024 / 32768) and f.range_volts(4) == pytest.approx(0.1)   # mV range
        assert not flow.enabled and r.value_label(4) == "V" and r.value_label(2) == "cmH2O"
        x = mac["records"][0].data[2][:50]
        np.testing.assert_allclose(r.read(2, 0, 2000), 33.9 * x * (10.24 / 32768) + 1.4)
        assert u.calibration == ("0.0 V", "1.0 V", "1.4", "35.3")      # V1, V2, value1, value2 as the .adicht stores them


def test_comments_and_marks(mac):
    with AdichtFile(mac["path"]) as f:
        got = [(c.record_index, c.tick, c.channel, c.text) for c in f.comments]
        want = [(r, t, ch, text) for r, rec in enumerate(mac["records"]) for t, ch, text in rec.comments]
        assert got == want
        assert [c.id for c in f.comments] == list(range(1, len(want) + 1))
        assert f.mac.marks == [(1, 2500)]                               # a kind-2 entry is not a comment
        assert [b.n_comments for b in f.records[0].info.blocks] == [1, 1, 1]


def test_sample_at_between_slow_samples(mac):
    with AdichtFile(mac["path"]) as f:
        r = f.records[0]
        x = mac["records"][0].data[2]
        conv = lambda k: 33.9 * x[k] * (10.24 / 32768) + 1.4
        assert r.sample_at(2, 80) == pytest.approx(conv(2))
        assert r.sample_at(2, 119) == pytest.approx(conv(2))            # the latest sample before the tick
        assert r.sample_at(2, 120) == pytest.approx(conv(3))
        assert r.sample_at(2, 81, units=False) == x[2]
        assert r.sample_at(0, 81, units=False) == mac["records"][0].data[0][81]
        assert r.sample_at(2, r.ticks) is None and r.sample_at(1, 100) is None


def test_block_envelope_fills_what_the_index_lacks(mac):
    with AdichtFile(mac["path"]) as f:
        r = f.records[0]
        x = mac["records"][0].data[2]
        _t0, _t1, mn, mx = r.block_envelope(2, units=False, fill=False)
        assert np.isnan(mn[1]) and np.isnan(mx[1])                      # block 2's min/max is not stored
        assert (mn[0], mx[0]) == (x[:400].min(), x[:400].max())
        _t0, _t1, mn, mx = r.block_envelope(2, units=False)
        assert (mn[1], mx[1]) == (x[400:800].min(), x[400:800].max())
        _t0, _t1, mn, mx = r.block_envelope(2, units=False, fill=False)  # remembered once filled
        assert not np.isnan(mn).any()
        assert [len(a) for a in r.block_envelope(1)] == [0, 0, 0, 0]


def test_export_refuses_what_it_cannot_write(mac, tmp_path):
    with AdichtFile(mac["path"]) as f:
        with pytest.raises(ValueError, match=r"4000 Hz: channel 1; 100 Hz: channels 3, 4"):
            export_selections(f, whole_file(f), [0, 2, 3], tmp_path / "mixed.csv", fmt="csv")
        with pytest.raises(ValueError, match="not stored"):
            export_selections(f, whole_file(f), [1], tmp_path / "calc.csv", fmt="csv")
        n = export_selections(f, whole_file(f), [2, 3], tmp_path / "slow.csv", fmt="csv")
        assert n == sum(-(-t // 40) for t in TICKS)


def test_command_line(mac, capsys, tmp_path):
    cli_main(["info", mac["path"]])
    out = capsys.readouterr().out
    assert "LabChart for Mac document (7.2), 3 records, 5 channels" in out
    assert "Pdi" in out and "not stored  (calculated: Arithmetic: Ch4-Ch3)" in out
    with pytest.raises(SystemExit) as e:
        cli_main(["check", mac["path"]])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "0 mismatches" in out and "1 blocks without a stored min/max" in out and "VERDICT: match" in out
    cli_main(["videos", mac["path"]])
    out = capsys.readouterr().out
    assert "record   3  LabChartMovie2.mov" in out and "record   1" not in out
    cli_main(["macsettings", mac["path"]])
    out = capsys.readouterr().out
    assert "LabChart for Mac document (with data)" in out and ", 5 channels" in out
    assert "Arithmetic: Ch4-Ch3" in out
    cli_main(["tree", mac["path"]])
    assert "'Map4'" in capsys.readouterr().out
    out_csv = tmp_path / "slow.csv"
    cli_main(["export", mac["path"], "-o", str(out_csv), "--channels", "3-5", "--record", "2"])
    assert "125 rows x 3 channels" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        cli_main(["export", mac["path"], "-o", str(out_csv), "--channels", "2"])
    assert "not stored" in str(e.value.code)


def test_a_settings_file_is_not_a_document(tmp_path):
    body = bytearray(MAGIC + b"\0\0\0" + bytes(8))
    toc = b"Mcro" + struct.pack(">HII", 30001, len(body), 0)
    struct.pack_into(">II", body, 24, len(body), len(toc))
    p = tmp_path / "settings"
    p.write_bytes(bytes(body) + toc)
    assert is_mac_document(p)
    with pytest.raises(MacFormatError, match="settings file"):
        read_mac_document(p)
    with pytest.raises(MacFormatError):
        AdichtFile(p)


def test_extended_floats_and_block_codec():
    for v in (0.0, 1.0, -33.358870526431, 0.36361168873809785, 166666.6666666667, -1516.666666666667, 1e-9):
        assert ext80(_ext80(v)) == v
    assert ext80(bytes.fromhex("3ffdba2b4faf259cc800")) == pytest.approx(0.3636116887)   # as LabChart wrote it
    rng = np.random.default_rng(3)
    x = np.cumsum(rng.integers(-5000, 5001, 1000))
    from adichtion.macdoc import MacBlock
    for packed in (True, False):
        blob = encode_mac_block(x, packed)
        blk = MacBlock(key=0, offset=0, length=len(blob), packed=packed, n_samples=len(x))
        np.testing.assert_array_equal(decode_mac_block(blob, blk), x)
