"""LabChart for Mac documents ("ADInst Data Res File"): records, samples, units, comments.

The native document format of LabChart 7 for Mac, described in
``docs/MAC_FORMAT.md``.  This module turns a document into the same plain data
model the ``.adicht`` reader produces (:class:`adichtion.model.ChartData`), plus
a block index with the interface :class:`adichtion.file.AdichtFile` needs, so
the rest of the library (API, export, command line, viewer) works unchanged.

What is verified (against WinChart ``.adicht`` exports of the same recordings,
``MAC_FORMAT.md``): the storage blocks and LabChart blocks, the sample decoding,
the ranges and units conversions, the block start times and the comments.
Inferred: the tick rate (from the ``mTim`` clock), the meaning of the few fields
listed as such in the format notes.

Calculated channels are **not stored** in a document (LabChart for Mac
computed them when drawing).  They appear as channels without data (dtype
``"none"``); their definitions come from :mod:`adichtion.macchart`.  Nothing is
recomputed here.

Big-endian throughout.  Pure ``struct`` + numpy, no file handles kept: the
caller passes the file's bytes to :func:`decode_mac_block`.
"""
from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union

import numpy as np

from .blocks import BLOCK_TICKS, packbits_decode, plane_bytes
from .macchart import MAGIC, MacChannelSettings, MacChartError, MacResFile, channel_settings
from .model import BlockInfo, ChannelBlock, ChannelRecordInfo, ChartData, Comment, RecordInfo, UnitsConversion

__all__ = ["MacDocument", "MacBlock", "MacBlockIndex", "MacFormatError", "is_mac_document",
           "read_mac_document", "decode_mac_block", "ext80"]

MAC_TO_UNIX = 2082844800          # seconds from 1904-01-01 to 1970-01-01
MAP4_ENTRY = struct.Struct(">IIffHHI")   # offset, length, max, min, gain index, format, units << 16 | decimation
ALC2_SIZE = 34
CNT1_SIZE = 44
MTIM_SIZE = 12
GAIN_SIZE = 72
UNTA_SIZE = 130
COMMENT_HEADER = struct.Struct(">IHIHiI")   # text length, tick in block, 0, number, channel mask, kind
FORMAT_PACKED = 19
FORMAT_FLOAT32 = 16
NO_MINMAX = -2147483648.0         # min and max both this value: the index holds no min/max for the block
FULL_SCALE_COUNTS = 32768.0       # samples and the index's min/max are normalised to +-1 = +-32768 counts
COMMENT_KIND = 1                  # kind 2: a mark without text (LabChart's Marker?), not a comment
UNITS_ON = 0x0100                 # UntA flag: the conversion is switched on
BASE_UNIT_SCALE = {"V": 1.0, "mV": 1e-3, "µV": 1e-6, "uV": 1e-6}


class MacFormatError(MacChartError):
    pass


def is_mac_document(path: Union[str, os.PathLike]) -> bool:
    """True when ``path`` starts with the "ADInst Data Res File" magic (a document
    or a settings file; :func:`read_mac_document` refuses settings files)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def ext80(b: bytes) -> float:
    """A big-endian 80-bit IEEE extended float (the classic Mac ``extended``)."""
    se, mant = struct.unpack(">HQ", bytes(b[:10]))
    if se & 0x7FFF == 0 and mant == 0:
        return 0.0
    sign = -1.0 if se & 0x8000 else 1.0
    return sign * math.ldexp(mant, (se & 0x7FFF) - 16383 - 63)


def _pstr(b: bytes, offset: int, size: int) -> str:
    """A Pascal string in a fixed ``size``-byte field (length byte included), Mac Roman.
    The no-break space LabChart puts in front of unit names is dropped."""
    n = min(b[offset], size - 1)
    return b[offset + 1:offset + 1 + n].decode("mac_roman").replace("\xa0", " ").strip()


def _vers(b: bytes) -> tuple[int, int, int]:
    """A classic ``vers`` resource: BCD major, minor/bugfix nibbles."""
    major = (b[0] >> 4) * 10 + (b[0] & 0xF)
    return major, b[1] >> 4, b[1] & 0xF


# -- sample blocks --------------------------------------------------------------------
@dataclass(frozen=True)
class MacBlock:
    """Where one channel's samples of one storage block are (nothing decoded)."""

    key: int             # running number, unique in the document (cache key)
    offset: int          # file offset
    length: int          # bytes
    packed: bool         # format 19 (PackBits bit planes) or 16 (raw float32)
    n_samples: int


def decode_mac_block(buf: Union[bytes, memoryview], block: MacBlock) -> np.ndarray:
    """ADC counts of one block as int32.

    Packed: byte ``0x01``, PackBits (LabChart's variant), 32 bit planes, plane 0 =
    bit 31, sample *i* at bit ``7 - i % 8`` of byte ``i // 8``; the symbols are
    sign-magnitude differences ``2|d| + (d < 0)``, the first against 0.
    Raw: big-endian float32 normalised to full scale (``counts / 32768``)."""
    n = block.n_samples
    buf = bytes(buf)
    if not block.packed:
        x = np.frombuffer(buf, dtype=">f4", count=n).astype(np.float64) * FULL_SCALE_COUNTS
        counts = np.rint(x)
        if not np.array_equal(counts, x):
            raise MacFormatError("a raw float32 block holds values that are not whole counts")
        return counts.astype(np.int32)
    if not buf or buf[0] != 1:
        raise MacFormatError(f"packed block at {block.offset} does not start with 0x01")
    planes_buf = packbits_decode(buf[1:])
    pb = plane_bytes(n)
    if len(planes_buf) < 32 * pb:
        raise MacFormatError(f"packed block at {block.offset}: {len(planes_buf)} bytes for {n} samples")
    planes = np.frombuffer(planes_buf, dtype=np.uint8, count=32 * pb).reshape(32, pb)
    bits = np.unpackbits(planes, axis=1, bitorder="big")[:, :n]
    # the 32 planes of a sample, MSB first, are its big-endian uint32 symbol
    sym = np.packbits(bits, axis=0, bitorder="big").T.copy().view(">u4").ravel().astype(np.int64)
    d = np.where(sym & 1, -(sym >> 1), sym >> 1)
    return np.cumsum(d).astype(np.int32)


class MacBlockIndex:
    """Maps ``(record, block, channel)`` to a :class:`MacBlock`; the counterpart of
    :class:`adichtion.blocks.SubBlockIndex` for what :class:`AdichtFile` uses."""

    def __init__(self, n_channels: int, blocks: list[list[list[Optional[MacBlock]]]],
                 decimation: list[list[int]]):
        self.n_channels = n_channels
        self._blocks = blocks              # [record][block][channel]
        self._decimation = decimation      # [record][channel], 0 = no data
        self.subblocks: list = []          # the Windows chain walk has no counterpart here

    def get(self, record: int, block: int, channel: int) -> Optional[MacBlock]:
        return self._blocks[record][block][channel]

    def has_data(self, channel: int) -> bool:
        return any(dec[channel] for dec in self._decimation)

    def channel_dtype(self, channel: int) -> str:
        """``"int16"`` for a channel with stored ADC samples, ``"none"`` for one
        without (a calculated channel, or an input that was switched off)."""
        return "int16" if self.has_data(channel) else "none"

    def decimation(self, record: int, channel: int) -> int:
        return self._decimation[record][channel]

    def samples_per_tick(self, record: int, channel: int) -> float:
        d = self._decimation[record][channel]
        return 1.0 / d if d else 0.0

    def tick_to_sample(self, record: int, channel: int, tick: int) -> int:
        """Sample *k* is taken at tick ``k * d``: the samples before ``tick`` number
        ``ceil(tick / d)`` (verified against the exports, ``MAC_FORMAT.md`` §4)."""
        d = self._decimation[record][channel]
        return -(-int(tick) // d) if d else 0


# -- the document --------------------------------------------------------------------------
@dataclass
class MacDocument:
    path: str
    chart: ChartData
    index: MacBlockIndex
    version: tuple[int, int, int]                   # the LabChart version that saved it (``vers``)
    created_version: tuple[int, int, int]
    settings: dict[int, MacChannelSettings]         # 1-based channel -> settings (titles, calculations)
    record_movies: list[Optional[str]] = field(default_factory=list)   # per LabChart block: movie file or None
    marks: list[tuple[int, int]] = field(default_factory=list)   # (record, tick) of kind-2 entries

    @property
    def movie_files(self) -> list[str]:
        """The movie files the document names, in block order."""
        return [m for m in self.record_movies if m]

    def definition(self, channel: int) -> Optional[str]:
        """The stored calculation of a 0-based channel, in LabChart's terms, or None."""
        s = self.settings.get(channel + 1)
        return s.describe() if s is not None and s.is_calculated else None


def _gains(raw: bytes) -> list[tuple[str, float]]:
    """``Gain`` entries -> (base units as stored, volts per count)."""
    out = []
    for o in range(0, len(raw) - GAIN_SIZE + 1, GAIN_SIZE):
        base = _pstr(raw, o, 34)
        full_scale = struct.unpack_from(">d", raw, o + 34)[0]
        if base not in BASE_UNIT_SCALE:
            raise MacFormatError(f"unknown base units {base!r} in a range entry")
        out.append((base, full_scale * BASE_UNIT_SCALE[base] / FULL_SCALE_COUNTS))
    return out


def _units_entries(raw: bytes) -> list[dict]:
    out = []
    for o in range(0, len(raw) - UNTA_SIZE + 1, UNTA_SIZE):
        e = raw[o:o + UNTA_SIZE]
        cal = [_pstr(e, 0x52 + 12 * k, 12) for k in range(4)]
        out.append(dict(on=bool(struct.unpack_from(">H", e, 4)[0] & UNITS_ON), offset=ext80(e[6:16]),
                        slope=ext80(e[16:26]), units=_pstr(e, 26, 8),
                        calibration=(cal[0], cal[2], cal[1], cal[3])))   # as the .adicht stores them: V1, V2, value1, value2
    return out


def _cnt_items(raw: bytes) -> list[tuple[int, str, bytes]]:
    """``CntD``: a run of items ``char[4] tag, uint16 1, uint32 length, uint32 0``
    followed by ``length`` bytes (``MAC_FORMAT.md`` §7).  Returns (offset, tag, payload)."""
    out, o = [], 0
    while o + 14 <= len(raw):
        tag = raw[o:o + 4].decode("latin-1")
        ln = struct.unpack_from(">I", raw, o + 6)[0]
        if o + 14 + ln > len(raw):
            raise MacFormatError(f"the movie table (CntD) item at {o} runs past its end")
        out.append((o, tag, raw[o + 14:o + 14 + ln]))
        o += 14 + ln
    return out


def _record_movies(cnt1: bytes, cntd: bytes, n_records: int) -> list[Optional[str]]:
    """The movie file each LabChart block names, None for none.  A ``Cnt1`` entry
    with ``uint16 1`` at 24 points at its group of ``CntD`` items (``uint32`` size
    at 16, offset at 20); the group's ``ExQN`` item holds the file name as a Pascal
    string, empty when the capture kept no movie."""
    items = _cnt_items(cntd)
    out: list[Optional[str]] = []
    for r in range(n_records):
        name = None
        o = r * CNT1_SIZE
        if o + CNT1_SIZE <= len(cnt1) and struct.unpack_from(">H", cnt1, o + 24)[0]:
            size, start = struct.unpack_from(">II", cnt1, o + 16)
            for io, tag, payload in items:
                if start <= io < start + size and tag == "ExQN" and payload and payload[0]:
                    name = payload[1:1 + payload[0]].decode("mac_roman", "replace")
                    break
        out.append(name)
    return out


def _comment_list(data: bytes, ptr: int, start_tick: int, record: int) -> tuple[list[Comment], list[int]]:
    """``u16 count``, ``count`` 20-byte headers, then the text part of every kind-1
    header (``u32 length, u16 0, u8 n, text``).  Returns (comments, mark ticks)."""
    if ptr + 2 > len(data):
        raise MacFormatError(f"comment list offset {ptr} outside the file")
    n = struct.unpack_from(">H", data, ptr)[0]
    headers = [COMMENT_HEADER.unpack_from(data, ptr + 2 + COMMENT_HEADER.size * i) for i in range(n)]
    o = ptr + 2 + COMMENT_HEADER.size * n
    comments: list[Comment] = []
    marks: list[int] = []
    for _length, tick, _zero, number, mask, kind in headers:
        if kind != COMMENT_KIND:
            marks.append(start_tick + tick)
            continue
        o += 4                                  # the text part's length (not relied upon, MAC_FORMAT.md §6)
        if struct.unpack_from(">H", data, o)[0] != 0:
            raise MacFormatError(f"comment text part at {o} does not start with 0")
        tl = data[o + 2]
        text = data[o + 3:o + 3 + tl].decode("mac_roman")
        o += 3 + tl
        if mask == -1 or mask == 0:
            channel = -1
        else:
            channel = (mask & -mask).bit_length() - 1      # lowest set bit: bit k = channel k + 1
        comments.append(Comment(id=number, record_index=record, channel=channel, tick=start_tick + tick, text=text))
    return comments, marks


def read_mac_document(path: Union[str, os.PathLike], data: Optional[bytes] = None) -> MacDocument:
    """Parse a LabChart for Mac document.  ``data`` may pass the file's bytes when
    the caller already holds them (they are needed again for decoding)."""
    path = os.fspath(path)
    res = MacResFile(path)
    if not res.is_document:
        raise MacFormatError(f"{os.path.basename(path)!r} is a LabChart for Mac settings file: it holds no data")
    if data is None:
        with open(path, "rb") as fh:
            data = fh.read()

    def chunk(typ: str, cid: int = 1) -> bytes:
        found = res.find(typ, cid)
        if not found:
            raise MacFormatError(f"no {typ!r} chunk {cid}")
        c = found[0]
        return data[c.offset:c.offset + c.length]

    # storage blocks
    alc = chunk("Alc2")
    n_blocks = len(alc) // ALC2_SIZE
    if n_blocks == 0 or len(alc) % ALC2_SIZE:
        raise MacFormatError("the storage block table (Alc2) is empty or truncated")
    blocks = []
    for k in range(n_blocks):
        o = k * ALC2_SIZE
        ticks, rec, code, start = struct.unpack_from(">HHHI", alc, o)
        secs, frac = struct.unpack_from(">Id", alc, o + 10)
        ptr = struct.unpack_from(">I", alc, o + 30)[0]
        blocks.append(dict(ticks=ticks, record=rec - 1, code=code, start=start, when=secs - MAC_TO_UNIX + frac,
                           comments=None if ptr == 0xFFFFFFFF else ptr))
    mt = chunk("mTim")
    clock = [struct.unpack_from(">Q", mt, k * MTIM_SIZE)[0] for k in range(min(n_blocks, len(mt) // MTIM_SIZE))]
    n_records = max(b["record"] for b in blocks) + 1
    by_record: list[list[int]] = [[] for _ in range(n_records)]
    for k, b in enumerate(blocks):
        if not 0 <= b["record"] < n_records:
            raise MacFormatError(f"storage block {k} names LabChart block {b['record'] + 1}")
        by_record[b["record"]].append(k)
    for r, ks in enumerate(by_record):
        if not ks:
            raise MacFormatError(f"LabChart block {r + 1} has no storage blocks")
        if ks != list(range(ks[0], ks[-1] + 1)) or blocks[ks[0]]["start"] != 0:
            raise MacFormatError(f"the storage blocks of LabChart block {r + 1} are not contiguous")

    # tick rate per LabChart block, from the clock steps inside it (MAC_FORMAT.md §2)
    rates: list[Optional[float]] = []
    by_code: dict[int, float] = {}
    for ks in by_record:
        rate = None
        for a, b in zip(ks, ks[1:]):
            if b < len(clock) and clock[b] > clock[a]:
                rate = float(f"{blocks[a]['ticks'] * 1e6 / (clock[b] - clock[a]):.9g}")
                break
        rates.append(rate)
        if rate is not None:
            by_code.setdefault(blocks[ks[0]]["code"], rate)
    for r, ks in enumerate(by_record):
        if rates[r] is None:
            code = blocks[ks[0]]["code"]
            known = set(by_code.values())
            if code in by_code:
                rates[r] = by_code[code]
            elif len(known) == 1:
                rates[r] = known.pop()
            else:
                raise MacFormatError(f"the tick rate of LabChart block {r + 1} cannot be determined")

    # channels: one block index (Map4) per channel
    ids = sorted(c.id for c in res.find("Map4"))
    if not ids or ids != list(range(1, len(ids) + 1)):
        raise MacFormatError(f"block indexes for channels {ids[:5]}… are not numbered 1..n")
    n_channels = len(ids)
    index = {}
    for cid in ids:
        raw = chunk("Map4", cid)
        if len(raw) != n_blocks * MAP4_ENTRY.size:
            raise MacFormatError(f"the block index of channel {cid} has {len(raw) // MAP4_ENTRY.size} entries "
                                 f"for {n_blocks} storage blocks")
        index[cid - 1] = [MAP4_ENTRY.unpack_from(raw, k * MAP4_ENTRY.size) for k in range(n_blocks)]
    gains = _gains(chunk("Gain")) if res.find("Gain") else []
    units = _units_entries(chunk("UntA")) if res.find("UntA") else []

    unit_objects: dict[tuple, UnitsConversion] = {}

    def units_object(gain_i: int, units_i: int) -> UnitsConversion:
        key = (gain_i, units_i)
        if key not in unit_objects:
            if not 1 <= gain_i <= len(gains):
                raise MacFormatError(f"range index {gain_i} outside the {len(gains)} range entries")
            _base, lsb = gains[gain_i - 1]
            if units_i:
                if units_i > len(units):
                    raise MacFormatError(f"units index {units_i} outside the {len(units)} units entries")
                u = units[units_i - 1]
                unit_objects[key] = UnitsConversion(base_units="V", units=u["units"], slope=u["slope"],
                                                    offset=u["offset"], lsb=lsb, mode=0,
                                                    flags=0x10000 if u["on"] else 0, calibration=u["calibration"])
            else:
                unit_objects[key] = UnitsConversion(base_units="V", units="", slope=1.0, offset=0.0, lsb=lsb, mode=0)
        return unit_objects[key]

    key = 0
    block_table: list[list[list[Optional[MacBlock]]]] = []
    decimation: list[list[int]] = []
    records: list[RecordInfo] = []
    marks: list[tuple[int, int]] = []
    for r, ks in enumerate(by_record):
        rec_blocks: list[list[Optional[MacBlock]]] = []
        rec_dec = [0] * n_channels
        ch_infos: list[ChannelRecordInfo] = []
        for c in range(n_channels):
            ents = [index[c][k] for k in ks]
            stored = [e for e in ents if e[1]]
            if stored and len(stored) != len(ents):
                raise MacFormatError(f"channel {c + 1} has data in only part of LabChart block {r + 1}")
            refs = {(e[4], e[6] >> 16, e[6] & 0xFFFF) for e in stored}
            if len(refs) > 1:
                raise MacFormatError(f"channel {c + 1} changes range, units or rate inside LabChart block {r + 1} "
                                     "(not supported yet)")
            if stored:
                gain_i, units_i, dec = refs.pop()
                dec = dec or 1
                rec_dec[c] = dec
                uo = units_object(gain_i, units_i)
            else:
                dec, uo = 0, None
            cblocks = []
            for k, e in zip(ks, ents):
                t = blocks[k]["ticks"]
                if e[1] and not (e[2] == NO_MINMAX and e[3] == NO_MINMAX):
                    mx, mn = e[2] * FULL_SCALE_COUNTS, e[3] * FULL_SCALE_COUNTS
                else:
                    mx = mn = math.nan
                cblocks.append(ChannelBlock(ticks=t, min=mn, max=mx, offset=e[0], dtype="int16" if e[1] else "none"))
            ticks = sum(blocks[k]["ticks"] for k in ks)
            ch_infos.append(ChannelRecordInfo(channel=c, record=r, dtype="int16" if stored else "none",
                                              tick_rate_hz=rates[r], ticks=ticks, block_ticks=BLOCK_TICKS,
                                              samples_per_block=-(-BLOCK_TICKS // dec) if dec else 0,
                                              units=uo, units_ref=0, setup=None, setup_ref=0, blocks=cblocks))
        for k in ks:
            row: list[Optional[MacBlock]] = []
            for c in range(n_channels):
                off, ln, _mx, _mn, _g, fmt, _tag = index[c][k]
                if not ln:
                    row.append(None)
                    continue
                if fmt not in (FORMAT_PACKED, FORMAT_FLOAT32):
                    raise MacFormatError(f"channel {c + 1}, storage block {k}: unknown block format {fmt}")
                if off + ln > len(data):
                    raise MacFormatError(f"channel {c + 1}, storage block {k}: block outside the file")
                n = -(-blocks[k]["ticks"] // rec_dec[c])
                row.append(MacBlock(key=key, offset=off, length=ln, packed=fmt == FORMAT_PACKED, n_samples=n))
                key += 1
            rec_blocks.append(row)
        # comments
        rec_comments: list[Comment] = []
        infos = []
        for k in ks:
            b = blocks[k]
            if b["comments"] is not None:
                cms, mk = _comment_list(data, b["comments"], b["start"], r)
                rec_comments += cms
                marks += [(r, t) for t in mk]
                infos.append(BlockInfo(ticks=b["ticks"], flags=0, n_comments=len(cms)))
            else:
                infos.append(BlockInfo(ticks=b["ticks"], flags=0, n_comments=0))
        first = blocks[ks[0]]
        records.append(RecordInfo(index=r, tick_rate_hz=rates[r], start_utc=first["when"], utc_offset_s=0.0,
                                  ticks=sum(blocks[k]["ticks"] for k in ks), stream_offset=-1, blocks=infos,
                                  comments=rec_comments, channels=ch_infos, first_block_index=ks[0]))
        block_table.append(rec_blocks)
        decimation.append(rec_dec)

    titles = list(res.channel_titles()[:n_channels])
    while len(titles) < n_channels:
        titles.append(f"Channel {len(titles) + 1}")
    vers = {c.id: data[c.offset:c.offset + c.length] for c in res.find("vers")}
    saved = _vers(vers.get(2) or vers.get(100) or vers.get(1) or b"\0\0\0\0")
    created = _vers(vers.get(1) or b"\0\0\0\0")
    try:
        settings = channel_settings(res)
    except MacChartError:
        settings = {}
    chart = ChartData(object_name="LabChart for Mac document", format_version=saved, records=records,
                      titles=titles, units=list(unit_objects.values()), setups={}, n_channels=n_channels,
                      parse_mode="sequential")
    movies = _record_movies(chunk("Cnt1") if res.find("Cnt1") else b"",
                            chunk("CntD") if res.find("CntD") else b"", n_records)
    return MacDocument(path=path, chart=chart, index=MacBlockIndex(n_channels, block_table, decimation),
                       version=saved, created_version=created, settings=settings, record_movies=movies, marks=marks)
