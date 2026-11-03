"""Damaged files: the reader answers with data or with an :class:`AdichtionError`, nothing else.

Valid synthetic documents (``synth.py``, the executable form of the format description) are
mutated (bit flips, truncation, zeroed or 0xFF-filled runs, swapped runs, extreme integers in
header and length fields) and then read in full: metadata, every channel of every record, the
block min/max.  The property, checked by hypothesis:

* an undamaged file reads back exactly (the other test files pin that sample for sample);
* a damaged file either reads (the values may then be wrong, but have the right shape) or raises
  an ``AdichtionError`` subclass; never another exception type;
* it never hangs (a watchdog thread, so the test also works where ``signal.alarm`` does not), and
  never allocates without bound (``tracemalloc`` peak, which numpy reports to).

The quick runs below are part of ``pytest -q``; ``test_fuzz_long`` is marked ``slow`` and runs with
``--runslow`` (or ``ADICHTION_SLOW=1``).  The fixed tests at the bottom pin every defect the fuzzing
found (docs/FORMAT.md §9).
"""
from __future__ import annotations

import functools
import os
import struct
import tempfile
import threading
import tracemalloc
import warnings

import numpy as np
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from conftest import make_document  # noqa: E402

from adichtion import AdichtContainer, AdichtFile, AdichtFormatError, AdichtionError  # noqa: E402
from adichtion.blocks import BlocksFormatError, SubBlock, decode_subblock, unpack_planes  # noqa: E402
from adichtion.chartdata import ChartDataError  # noqa: E402
from adichtion.macchart import MacChartError  # noqa: E402
from adichtion.macdoc import MacFormatError  # noqa: E402
from adichtion.qtmovie import MovieError  # noqa: E402
from adichtion.synth import SynthMacChannel, SynthMacRecord, write_mac_document, write_synthetic  # noqa: E402
from adichtion.video import VideoStreamError  # noqa: E402

TIME_LIMIT_S = 20.0             # one damaged file; an undamaged one reads in well under a second
MEMORY_LIMIT = 256 * 1024 ** 2  # peak Python/numpy allocation while reading one (small) damaged file


@functools.lru_cache(maxsize=None)
def bases() -> dict[str, bytes]:
    """The undamaged documents, once per session: the three .adicht layouts, a fragmented
    container and a LabChart for Mac document."""
    folder = tempfile.mkdtemp(prefix="adichtion-fuzz-")
    channels, records = make_document()
    out = {}
    for name, kw in {"v29": dict(version=29, video=True), "v26": dict(version=26),
                     "legacy": dict(version=29, layout="legacy"), "fragmented": dict(version=29, fragment=True)}.items():
        path = os.path.join(folder, name + ".adicht")
        write_synthetic(path, channels, records, **kw)
        with open(path, "rb") as fh:
            out[name] = fh.read()
    rng = np.random.default_rng(11)
    mch = [SynthMacChannel("EMG", 1, "V", 2.048),
           SynthMacChannel("Pdi", 0, calculation="Arithmetic", expression="Ch3-Ch1"),
           SynthMacChannel("Poes", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True), no_minmax_blocks=[(0, 1)]),
           SynthMacChannel("Pgas", 40, "V", 10.24, raw_blocks=[(1, 0)])]
    mrec = []
    for ticks in (40000, 5000):
        data = [np.cumsum(rng.integers(-400, 401, ticks)), None] + \
               [np.cumsum(rng.integers(-30, 31, -(-ticks // 40))) for _ in range(2)]
        mrec.append(SynthMacRecord(ticks, data, 1_700_000_000.0, comments=[(100, -1, "start")], marks=[]))
    path = os.path.join(folder, "mac document")
    write_mac_document(path, mch, mrec, tick_rate=4000)
    with open(path, "rb") as fh:
        out["mac"] = fh.read()
    return out


def read_everything(path: str) -> None:
    """What the viewer and an export would touch: metadata, all samples, the block min/max."""
    with AdichtFile(path) as f:
        _ = f.channels, f.comments, f.videos, f.format_label
        for r in f.records:
            for c in range(f.n_channels):
                if not f.has_data(c):
                    continue
                x = r.read(c)
                assert len(x) == r.n_samples(c)
                mins, maxs = f.block_minmax(r.index, c)
                assert len(mins) == len(maxs)


def outcome(data: bytes) -> str:
    """Read ``data`` as a file under the time and memory limits.  Returns ``"read"`` or the name
    of the AdichtionError subclass raised; any other exception fails the test."""
    fd, path = tempfile.mkstemp(suffix=".adicht")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    result: dict[str, object] = {}

    def run() -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)     # NaN/overflow in garbage samples is fine
                read_everything(path)
            result["ok"] = "read"
        except BaseException as e:      # handed to the test thread below
            result["error"] = e

    tracemalloc.start()
    try:
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(TIME_LIMIT_S)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert not worker.is_alive(), f"reading a damaged file did not finish within {TIME_LIMIT_S:g} s"
        assert peak <= MEMORY_LIMIT, f"reading a damaged file allocated {peak / 2**20:.0f} MiB"
        if "error" in result:
            e = result["error"]
            if isinstance(e, AdichtionError):
                return type(e).__name__
            raise AssertionError(f"{type(e).__name__} instead of an AdichtionError: {e}") from e
        return str(result["ok"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass        # Windows: a hung reader would still hold it


EXTREMES = (0, 1, -1, 7, 2 ** 15, 2 ** 31 - 1, -2 ** 31, 2 ** 32, 1 << 40, 2 ** 63 - 1, -2 ** 63)


@st.composite
def mutation(draw, size: int):
    kind = draw(st.sampled_from(["flip", "truncate", "zero", "ff", "swap", "int32", "int64", "header"]))
    if kind == "flip":
        return kind, draw(st.lists(st.tuples(st.integers(0, size - 1), st.integers(0, 7)), min_size=1, max_size=16))
    if kind == "truncate":
        return kind, draw(st.integers(0, size - 1))
    if kind in ("zero", "ff"):
        return kind, (draw(st.integers(0, size - 1)), draw(st.integers(1, 4096)))
    if kind == "swap":
        return kind, (draw(st.integers(0, size - 1)), draw(st.integers(0, size - 1)), draw(st.integers(1, 4096)))
    if kind == "header":        # the container header and root entry, where every field matters
        return kind, (draw(st.integers(0x80, 0xB8 + 160 - 8)), draw(st.sampled_from(EXTREMES)))
    return kind, (draw(st.integers(0, size - 8)), draw(st.sampled_from(EXTREMES)))


def apply(data: bytes, kind: str, arg) -> bytes:
    b = bytearray(data)
    if kind == "flip":
        for i, bit in arg:
            b[i] ^= 1 << bit
    elif kind == "truncate":
        del b[arg:]
    elif kind in ("zero", "ff"):
        i, n = arg
        b[i:i + n] = (b"\x00" if kind == "zero" else b"\xff") * len(b[i:i + n])
    elif kind == "swap":
        i, j, n = arg
        x, y = bytes(b[i:i + n]), bytes(b[j:j + n])
        if len(x) == len(y):
            b[i:i + n], b[j:j + n] = y, x
    else:
        i, v = arg
        if kind == "int32" or (kind == "header" and i < 0x9C):      # the header's int32 fields end at 0x9C
            b[i:i + 4] = struct.pack("<I", v & 0xFFFFFFFF)
        else:
            b[i:i + 8] = struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
    return bytes(b)


@st.composite
def damaged(draw):
    name = draw(st.sampled_from(sorted(bases())))
    data = bases()[name]
    for _ in range(draw(st.integers(1, 3))):
        if len(data) < 16:
            break
        kind, arg = draw(mutation(len(data)))          # sized to the data as it is now (after a truncation)
        data = apply(data, kind, arg)
    return name, data


_QUICK = settings(max_examples=150, deadline=None, derandomize=True, database=None,
                  suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


def test_undamaged_documents_read():
    for name, data in bases().items():
        assert outcome(data) == "read", name


@_QUICK
@given(damaged())
def test_fuzz_damaged_files(case):
    outcome(case[1])


@pytest.mark.slow
@settings(max_examples=3000, deadline=None, database=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(damaged())
def test_fuzz_long(case):
    outcome(case[1])


def test_every_truncation_of_the_header_and_map_is_clean():
    """Truncate each document at every 61st byte (a prime, so the cut falls everywhere in a
    cluster): what a copy cut short by a full disk or an interrupted transfer looks like."""
    for name, data in bases().items():
        for n in range(0, len(data), 61 if len(data) < 200_000 else 997):
            outcome(data[:n])


# -- the defects found, one fixed test each ----------------------------------------------------
def test_every_reader_error_is_an_adichtion_error():
    for cls in (AdichtFormatError, ChartDataError, BlocksFormatError, MacChartError, MacFormatError,
                VideoStreamError, MovieError):
        assert issubclass(cls, AdichtionError) and issubclass(cls, ValueError), cls


def test_scan_parsed_file_has_block_minmax():
    """A legacy (scan) parse has no per-record block index; block_minmax gave AttributeError."""
    p = os.path.join(tempfile.mkdtemp(), "legacy.adicht")
    with open(p, "wb") as fh:
        fh.write(bases()["legacy"])
    with AdichtFile(p) as f:
        assert f.parse_mode == "scan"
        r = f.records[0]
        mins, maxs = f.block_minmax(0, 0)
        assert len(mins) == len(r.block_ticks)
        x = r.read_raw(0)
        assert mins.min() == x.min() and maxs.max() == x.max()


def _patched(name: str, offset: int, fmt: str, value) -> bytes:
    b = bytearray(bases()[name])
    struct.pack_into(fmt, b, offset, value)
    return bytes(b)


def test_bad_cluster_size_closes_cleanly(tmp_path):
    """The container raised AttributeError from close() when the header failed this early."""
    p = tmp_path / "x.adicht"
    p.write_bytes(_patched("v29", 0x98, "<i", 40))
    with pytest.raises(AdichtFormatError, match="cluster size"):
        AdichtContainer(p)


@pytest.mark.parametrize("size", [0, 100, -512, 1 << 30])
def test_implausible_header_size(tmp_path, size):
    p = tmp_path / "x.adicht"
    p.write_bytes(_patched("v29", 0x84, "<i", size))
    with pytest.raises(AdichtFormatError, match="header size"):
        AdichtFile(p)


@pytest.mark.parametrize("name", ["v29", "fragmented"])
def test_cluster_map_outside_the_file(tmp_path, name):
    """A first map cluster past the end of the file gave struct.error."""
    p = tmp_path / "x.adicht"
    p.write_bytes(_patched(name, 0xAC, "<q", 1 << 40))
    with pytest.raises(AdichtionError):
        read_everything(str(p))


def test_bit_planes_too_short_for_the_sample_count():
    """A plane size taken from the buffer that holds fewer bits than samples gave a numpy
    broadcast error; it is a BlocksFormatError now (a valid buffer decodes as before)."""
    with pytest.raises(BlocksFormatError, match="cannot hold"):
        unpack_planes(bytes(17 * 4), 17, 8000)
    with pytest.raises(BlocksFormatError, match="cannot hold"):
        unpack_planes(bytes(32 * 8), 32, 1000, word_reversed=True)
    assert unpack_planes(bytes(17 * 4), 17, 32).shape == (17, 32)


def test_unpacked_sub_block_shorter_than_its_samples():
    sb = SubBlock(index=3, offset=0, body_offset=16, body_size=10, flags=0, n_samples=100)
    with pytest.raises(BlocksFormatError, match="unpacked"):
        decode_subblock(bytes(10), sb)


def test_mac_chunk_length_past_the_end_of_the_file():
    """A chunk length of about 4 GB in the table of contents made the reader allocate it before
    reading (found by the long fuzzing run); the chunk is refused when the table is read."""
    b = bytearray(bases()["mac"])
    toc_offset = struct.unpack_from(">I", b, 24)[0]
    struct.pack_into(">I", b, toc_offset + 10, 0xFF000005)        # the first entry's length
    assert outcome(bytes(b)) == "MacChartError"


def test_damaged_mac_document_raises_mac_format_error():
    """Fields that point past their chunk or hold an impossible exponent gave struct.error and
    OverflowError from the Mac parser.  Fill each 64-byte window with 0xFF in turn."""
    data = bases()["mac"]
    seen = set()
    for i in range(0, len(data), 64):
        b = bytearray(data)
        b[i:i + 64] = b"\xff" * len(b[i:i + 64])
        seen.add(outcome(bytes(b)))
    assert "MacFormatError" in seen


# -- adichtion check on damaged files ------------------------------------------------------------
def _check(path, capsys):
    from adichtion.cli import main as cli_main
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["check", str(path)])
    return exit_info.value.code, capsys.readouterr().out


def _with_block_offsets():
    data = bases()["v29"]
    p = os.path.join(tempfile.mkdtemp(), "v29.adicht")
    with open(p, "wb") as fh:
        fh.write(data)
    with AdichtFile(p) as f:
        return data, f._container.offset(f._blocks_entry.first), f.index.get(0, 1, 0)


def test_check_undamaged(tmp_path, capsys):
    p = tmp_path / "ok.adicht"
    p.write_bytes(bases()["v29"])
    code, out = _check(p, capsys)
    assert code == 0 and "VERDICT: match" in out and "DAMAGED" not in out


def test_check_names_the_damaged_header_and_what_still_reads(tmp_path, capsys):
    data, base, sb = _with_block_offsets()
    b = bytearray(data)
    b[base + sb.offset + 8: base + sb.offset + 12] = bytes(4)       # the -n check field of record 1 block 2, channel 1
    p = tmp_path / "x.adicht"
    p.write_bytes(bytes(b))
    code, out = _check(p, capsys)
    assert code == 1 and "VERDICT: DAMAGED" in out
    assert "where: the sample data" in out
    assert "record 1: 2 of 3 blocks readable in every channel; the first unreadable block is block 2 at 16.000 s" in out
    assert "record 2: 1 of 1 blocks readable" in out


def test_check_reports_a_cut_off_file(tmp_path, capsys):
    data, base, sb = _with_block_offsets()
    p = tmp_path / "x.adicht"
    p.write_bytes(data[: base + sb.offset + 100])
    code, out = _check(p, capsys)
    assert code == 1 and "VERDICT: DAMAGED" in out and "where: the container" in out
    assert "readable: nothing" in out


def test_check_reports_an_impossible_sample_count(tmp_path, capsys):
    data, base, sb = _with_block_offsets()
    b = bytearray(data)
    struct.pack_into("<i", b, base + sb.body_offset, 1 << 30)        # the packed block claims 2**29 samples
    p = tmp_path / "x.adicht"
    p.write_bytes(bytes(b))
    code, out = _check(p, capsys)
    assert code == 1 and "VERDICT: DAMAGED" in out and "536870912 samples for 16000 ticks" in out
    assert "record 1: 2 of 3 blocks readable in every channel; the first unreadable block is block 2" in out


def test_check_reports_an_unreadable_block_and_reads_the_rest(tmp_path, capsys, monkeypatch):
    """A block that fails only when it is decoded (the file opens): named with its time, the
    other blocks are still checked."""
    p = tmp_path / "x.adicht"
    p.write_bytes(bases()["v29"])
    real = AdichtFile.decode_block

    def failing(self, record, block, channel, cache=True):
        if (record, block, channel) == (0, 2, 1):
            raise BlocksFormatError("synthetic damage")
        return real(self, record, block, channel, cache)

    monkeypatch.setattr(AdichtFile, "decode_block", failing)
    code, out = _check(p, capsys)
    assert code == 1 and "VERDICT: DAMAGED" in out
    assert "DAMAGED: record 1 channel 2 block 3 (32.000-40.000 s of the record): synthetic damage" in out
    assert "15 blocks checked, 0 mismatches, 1 blocks unreadable (the other blocks read)" in out
