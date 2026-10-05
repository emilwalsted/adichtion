"""What RespMech reads: the CSV, tab-separated, Excel and MATLAB exports load as they are, with the
values of ``Record.read``.  The first tests use pandas and scipy (what RespMech's loaders use); the
last one runs RespMech's own ``load`` when RespMech is installed."""
import io
import os
import struct

import numpy as np
import pytest

from adichtion.export import export_selections, write_text
from adichtion.file import AdichtFile, Selection
from adichtion.matfile import write_mat
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic

pd = pytest.importorskip("pandas")
sio = pytest.importorskip("scipy.io")

TICKS = (1200, 700)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """Two records on three channels (the third a float32 channel with units), with awkward
    comments: a comma, a semicolon, a tab, a line break and a dot."""
    rng = np.random.default_rng(11)
    channels = [SynthChannel("Flow, L/s", "int16", None, 0.2), SynthChannel("Pmo", "int16", ("cmH2O", -13.34, 0.0), 10.0),
                SynthChannel("Calc", "float32", None, 10.0, lsb=1.0)]
    records = []
    for ticks in TICKS:
        r = SynthRecord(ticks, comments=[(3, -1, "a, b; c\td\ne. f"), (40, -1, "plain")])
        r.data = {0: np.cumsum(rng.integers(-5, 6, ticks)), 1: np.cumsum(rng.integers(-5, 6, ticks)),
                  2: (rng.normal(0, 1e3, ticks)).astype(np.float32)}
        records.append(r)
    path = tmp_path_factory.mktemp("respmech") / "r.adicht"
    write_synthetic(path, channels, records, version=29, layout="sequential", video=False)
    return str(path)


def _read(f, record, channels):
    return [f.records[record].read(c, 0, f.records[record].ticks) for c in channels]


@pytest.mark.parametrize("fmt, ext", [("csv", "csv"), ("tsv", "txt")])
@pytest.mark.parametrize("decimal", [".", ","])
@pytest.mark.parametrize("comments", [True, False])
def test_delimited_exports_are_tables_pandas_reads_with_respmechs_settings(recording, tmp_path, fmt, ext, decimal, comments):
    """RespMech: ``.csv`` is comma-separated, semicolon-separated under a decimal comma; ``.txt`` is
    tab-separated; the first row is the column titles and the first column is the time."""
    sep = "\t" if fmt == "tsv" else (";" if decimal == "," else ",")
    with AdichtFile(recording) as f:
        out = tmp_path / f"t.{ext}"
        export_selections(f, [Selection(0, 0, TICKS[0])], [0, 1, 2], out, fmt=fmt, decimal=decimal, comments=comments)
        df = pd.read_csv(out, sep=sep, decimal=decimal)
        assert df.shape[0] == TICKS[0] and df.shape[1] == (5 if comments else 4)
        for j, ref in enumerate(_read(f, 0, [0, 1, 2])):
            assert df.dtypes.iloc[1 + j].kind == "f"
            assert np.allclose(df.iloc[:, 1 + j].to_numpy(), ref, rtol=1e-6, atol=0)
        assert np.allclose(df.iloc[:, 0].to_numpy(), np.arange(TICKS[0]) / f.records[0].tick_rate_hz)
        if comments:
            assert df.iloc[3, 4] == {",": "a; b; c d e. f", ";": "a, b  c d e. f", "\t": "a, b; c d e. f"}[sep]      # the separator never survives in a comment
            assert df["comment"].notna().sum() == 2


def test_a_comma_decimal_csv_is_semicolon_separated_and_has_no_stray_commas_in_numbers(recording, tmp_path):
    with AdichtFile(recording) as f:
        out = io.StringIO()
        write_text(f.records[0], Selection(0, 0, 50), [0, 1], out, fmt="csv", decimal=",", comments=False)
    lines = out.getvalue().splitlines()
    assert lines[0].count(";") == 2 and all(line.count(";") == 2 for line in lines)
    assert "," in lines[1] and "." not in lines[1]


def test_no_exported_row_is_broken_by_a_line_break_in_a_comment(recording):
    with AdichtFile(recording) as f:
        for fmt in ("txt", "csv", "tsv"):
            out = io.StringIO()
            n = write_text(f.records[0], Selection(0, 0, TICKS[0]), [0], out, fmt=fmt)
            lines = out.getvalue().splitlines()
            assert len(lines) == n + (7 if fmt == "txt" else 1)


def test_the_xlsx_sheet_is_a_plain_table_pandas_reads(recording, tmp_path):
    pytest.importorskip("openpyxl")
    with AdichtFile(recording) as f:
        for comments in (True, False):
            out = tmp_path / f"t{comments}.xlsx"
            export_selections(f, [Selection(0, 10, 510)], [0, 2], out, fmt="xlsx", comments=comments)
            df = pd.read_excel(out)
            assert list(df.columns[:3]) == ["time_s", "Flow, L/s [V]", "Calc [V]"]
            assert df.iloc[:, :3].shape == (500, 3) and df.iloc[:, :3].notna().all().all()
            assert df["interval (s)"].iloc[0] == pytest.approx(1.0 / f.records[0].tick_rate_hz)
            assert df["Block"].iloc[0] == 1 and df["Block"].notna().sum() == 1
            assert np.allclose(df.iloc[:, 1].to_numpy(), f.records[0].read(0, 10, 510), rtol=1e-6, atol=0)
        no_header = tmp_path / "n.xlsx"
        export_selections(f, [Selection(0, 0, 5)], [0], no_header, fmt="xlsx", header=False, comments=False)
        assert pd.read_excel(no_header, header=None).shape == (5, 2)


# -- MATLAB ----------------------------------------------------------------------------------------

def test_the_mat_file_is_a_level_5_file_whose_data_block_has_the_channels_as_rows(recording, tmp_path):
    with AdichtFile(recording) as f:
        out = tmp_path / "m.mat"
        sels = [Selection(0, 5, 1000), Selection(1, 0, TICKS[1])]
        n = export_selections(f, sels, [2, 0, 1], out, fmt="mat")
        assert n == 995 + TICKS[1]
        raw = out.read_bytes()
        assert raw[:19] == b"MATLAB 5.0 MAT-file" and struct.unpack("<H", raw[124:126]) == (0x0100,) and raw[126:128] == b"IM"
        m = sio.loadmat(out)
        assert [k for k in m if not k.startswith("__")] == [f"{v}_block{i}" for i in (1, 2)
                                                           for v in ("data", "titles", "units", "samplerate", "record")]
        for i, sel in ((1, sels[0]), (2, sels[1])):
            d = m[f"data_block{i}"]
            rec = f.records[sel.record]
            assert d.dtype == np.float64 and d.shape == (3, sel.stop - sel.start)
            for row, c in enumerate([2, 0, 1]):
                assert np.array_equal(d[row], rec.read(c, sel.start, sel.stop).astype(np.float64))
            assert [t.strip() for t in m[f"titles_block{i}"]] == ["Calc", "Flow, L/s", "Pmo"]
            assert [t.strip() for t in m[f"units_block{i}"]] == [rec.value_label(c) for c in (2, 0, 1)]
            assert m[f"samplerate_block{i}"].item() == pytest.approx(rec.tick_rate_hz)
            assert m[f"record_block{i}"].item() == sel.record + 1
        # list(data["data_block1"]) is what RespMech's "windows" variant does: one array per channel
        assert len(list(m["data_block1"])) == 3 and list(m["data_block1"])[1].squeeze().shape == (995,)


def test_a_mat_export_in_raw_counts_and_with_one_channel(recording, tmp_path):
    with AdichtFile(recording) as f:
        out = tmp_path / "raw.mat"
        write_mat(f, [Selection(0, 0, 300)], [0], out, units=False)
        m = sio.loadmat(out)
        assert m["data_block1"].shape == (1, 300)
        assert np.array_equal(m["data_block1"][0], f.records[0].read(0, 0, 300, units=False))


def test_mat_chunks_do_not_change_the_bytes(recording, tmp_path, monkeypatch):
    from adichtion import export
    with AdichtFile(recording) as f:
        a, b = tmp_path / "a.mat", tmp_path / "b.mat"
        write_mat(f, [Selection(0, 0, TICKS[0])], [0, 1], a)
        monkeypatch.setattr(export, "CHUNK_ROWS", 7)
        write_mat(f, [Selection(0, 0, TICKS[0])], [0, 1], b)
    assert a.read_bytes()[128:] == b.read_bytes()[128:]          # the header text holds the time


def test_a_variable_over_4_gib_is_refused_with_a_message(tmp_path):
    from adichtion import matfile
    with open(tmp_path / "x", "wb") as fh, pytest.raises(ValueError, match="too large"):
        matfile._matrix_head(fh, "data_block1", 6, (8, 2**28), 9, 8 * 8 * 2**28)


def test_a_cancelled_mat_export_leaves_nothing(recording, tmp_path):
    from adichtion.export import ExportCancelled
    with AdichtFile(recording) as f:
        with pytest.raises(ExportCancelled):
            export_selections(f, [Selection(0, 0, TICKS[0])], [0], tmp_path / "c.mat", fmt="mat", stale=lambda: True)
    assert not (tmp_path / "c.mat").exists() and not (tmp_path / "c.mat.part").exists()


# -- RespMech's own loader -------------------------------------------------------------------------

@pytest.mark.parametrize("fmt, ext, decimal, offset", [("csv", "csv", ".", 2), ("csv", "csv", ",", 2), ("tsv", "txt", ".", 2),
                                                        ("tsv", "txt", ",", 2), ("xlsx", "xlsx", ".", 2), ("mat", "mat", ".", 1)])
def test_respmechs_own_loader_reads_every_exported_format(recording, tmp_path, fmt, ext, decimal, offset):
    """``offset``: the first channel's column number in RespMech (time is column 1 in a table;
    a MATLAB file has the channels from row 1)."""
    loaders = pytest.importorskip("respmech.core.io.loaders")
    pytest.importorskip("openpyxl")
    from types import SimpleNamespace as NS
    settings = NS(input=NS(data=NS(column_flow=offset, column_volume=offset + 2, column_poes=offset + 1,
                                   column_pgas=offset + 1, column_pdi=offset + 1, columns_entropy=[], columns_emg=[]),
                           format=NS(decimalcharacter=decimal, matlabfileformat=1, samplingfrequency=1000)),
                  processing=NS(mechanics=NS(inverseflow=False, integratevolumefromflow=False, inversevolume=False)))
    with AdichtFile(recording) as f:
        out = os.fspath(tmp_path / f"r.{ext}")
        export_selections(f, [Selection(0, 0, TICKS[0])], [0, 1, 2], out, fmt=fmt, decimal=decimal)
        flow, vol, poes, *_rest = loaders.load(out, settings)
        ref = _read(f, 0, [0, 1, 2])
        assert len(flow) == TICKS[0]
        for got, want in zip((flow, poes, vol), ref):
            assert np.allclose(got, want, rtol=1e-6, atol=0)


def test_mat_titles_keep_non_ascii_characters(tmp_path):
    from adichtion.matfile import put_chars, write_header
    with open(tmp_path / "t.mat", "wb") as fh:
        write_header(fh)
        put_chars(fh, "titles", ["æøå µ", "Flow", "ab\U0001F600"])
    m = sio.loadmat(tmp_path / "t.mat")
    assert [t.rstrip() for t in m["titles"]] == ["æøå µ", "Flow", "ab?"]
