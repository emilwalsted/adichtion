import io

import numpy as np

from adichtion import AdichtFile, AdichtContainer
from adichtion.export import read_labchart_text, write_text


def test_container_tree(synthetic):
    with AdichtContainer(synthetic["path"]) as c:
        names = sorted(e.name for e in c.walk())
        assert names == ["Blocks_0", "Map", "Version"]
        assert c.read_stream(c.find_stream("Version")).startswith(b"\x01\x00\x00\x80")


def test_metadata(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        assert f.channel_titles == ["Flow", "Pmo", "Calc"]
        assert [c.dtype for c in f.channels] == ["int16", "int16", "float32"]
        assert [c.range_volts for c in f.channels] == [0.2, 10.0, 10.0]
        assert f.channels[1].units.units == "cmH2O"
        assert f.channels[0].units is None
        assert [r.ticks for r in f.records] == [40000, 5000]
        assert [r.block_ticks for r in f.records] == [[16000, 16000, 8000], [5000]]
        r = f.records[0]
        assert [(c.tick, c.channel, c.text) for c in r.comments] == [(10, -1, "start"), (16005, 1, "15,5 inH2O"), (39995, 0, "stop")]
        assert r.selection_between("start", "stop").ticks == 39985
        assert r.sample_rate_hz(2) == 1000.0


def test_samples_exact(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        for ri, rec in enumerate(f.records):
            ref = synthetic["records"][ri].data
            assert np.array_equal(rec.read_raw(0), ref[0])
            assert np.array_equal(rec.read_raw(1), ref[1])
            assert np.allclose(rec.read_raw(2), ref[2], rtol=1e-6, atol=1e-3)
            # partial reads across block boundaries
            assert np.array_equal(rec.read_raw(0, 15990, 16010), ref[0][15990:16010])
            # units conversion: counts -> volts -> cmH2O
            volts = ref[1][:5] * (10.0 * 1.024 / 32768)
            assert np.allclose(rec.read(1, 0, 5), 13.34 * volts - 0.635)
            assert np.allclose(rec.read(0, 0, 5), ref[0][:5] * (0.2 * 1.024 / 32768))


def test_overview_and_time(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        rec = f.records[0]
        t, mn, mx = rec.overview(0, 100)
        assert len(t) == len(mn) == len(mx) == 100
        assert np.all(mn <= mx)
        assert np.allclose(rec.time(0, 3), [0, 0.001, 0.002])


def test_export_roundtrip(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        rec = f.records[0]
        sel = rec.selection(15.99, 16.02)
        buf = io.StringIO()
        n = write_text(rec, sel, [0, 1], buf, fmt="txt", decimal=",")
        assert n == 30
        text = buf.getvalue()
        assert text.startswith("Interval=\t0,001 s\n")
        assert "#* 15,5 inH2O" in text
        import tempfile, os
        p = os.path.join(tempfile.mkdtemp(), "x.txt")
        open(p, "w").write(text)
        header, t, vals, comments = read_labchart_text(p)
        assert header["ChannelTitle"] == "Flow\tPmo"
        assert np.allclose(t[:2], [15.99, 15.991])
        assert np.allclose(vals[:, 1], rec.read(1, sel.start, sel.stop), rtol=1e-6)
        assert comments == [(15, "15,5 inH2O")]
