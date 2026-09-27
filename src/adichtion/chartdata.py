"""Parser for the ``Map`` stream ("ADInstruments Chart Data").

The stream is a serialized object graph (MFC ``CArchive`` style): fixed
fields, self-checking ``(n, -n)`` lists, tagged UTF-16 strings and *objects*
introduced by a tag ``0x80000000 | id`` (a new object with running id ``id``)
or referenced later by the plain ``id``.  Its grammar, as far as it is known,
is written out in ``docs/FORMAT.md`` §3; in short::

    header
    record*              (separated by ``int32 next_record_number, int32 0``)
        record header    tick rate, start time, time-zone offset, block count so far, ticks
        block list       16-byte entries with the comments of each block
        channel entry*   per channel: data type, rate, units object (or reference),
                         [v29: ChannelDataSource object with <_ChannelSetup> XML],
                         block index (offset into Blocks_0, min, max per block)
    int32 -1, titles     channel titles for every channel slot
    trailer

:func:`parse_chart_data` parses this grammar sequentially.  Should a file not
follow it, the older heuristics (scanning for records, block lists, units
records, titles and XML by their self-checking markers) take over so that the
samples stay readable; :attr:`ChartData.parse_mode` tells which path was used.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Optional

from .model import (BlockInfo, ChannelBlock, ChannelRecordInfo, ChannelSetup, ChartData, Comment,
                    RecordInfo, UnitsConversion)
from .serialization import TAG, iter_tagged_strings, read_prefixed

BLOCK_TICKS = 16000
STREAM_MAGICS = (bytes.fromhex("aaaa1455"), bytes.fromhex("aaaa1555"))

ENTRY_TAG = 0x801B0000          # precedes every channel entry (after a 0x01 byte)
DTYPE_TAG_BASE = 0x80020000     # channel data-type tag: | 1 = int16, | 3 = float32
NEW_OBJECT = 0x80000000         # high bit of an object slot: a new object follows


class ChartDataError(ValueError):
    pass


# -- cursor ----------------------------------------------------------------------
class _Cursor:
    """Sequential reader with the primitives of the stream."""

    def __init__(self, blob: bytes, pos: int = 0):
        self.b = blob
        self.o = pos

    def _unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.o + size > len(self.b):
            raise ChartDataError(f"unexpected end of Map stream at {self.o}")
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += size
        return v

    def i32(self) -> int:
        return self._unpack("<i")[0]

    def u32(self) -> int:
        return self._unpack("<I")[0]

    def i64(self) -> int:
        return self._unpack("<q")[0]

    def f64(self) -> float:
        return self._unpack("<d")[0]

    def f32(self) -> float:
        return self._unpack("<f")[0]

    def i16(self) -> int:
        return self._unpack("<h")[0]

    def u16(self) -> int:
        return self._unpack("<H")[0]

    def u8(self) -> int:
        return self._unpack("<B")[0]

    def raw(self, n: int) -> bytes:
        if self.o + n > len(self.b):
            raise ChartDataError(f"unexpected end of Map stream at {self.o}")
        v = self.b[self.o : self.o + n]
        self.o += n
        return v

    def expect(self, value: bytes, what: str) -> None:
        got = self.raw(len(value))
        if got != value:
            raise ChartDataError(f"{what}: expected {value.hex()} at {self.o - len(value)}, found {got.hex()}")

    def tagged(self, what: str = "string") -> str:
        if self.b[self.o : self.o + 4] != TAG:
            raise ChartDataError(f"{what}: no string tag at {self.o}")
        r = read_prefixed(self.b, self.o + 4)
        if r is None:
            raise ChartDataError(f"{what}: malformed string at {self.o}")
        self.o = r[1]
        return r[0]

    def counted(self, what: str, limit: int = 1 << 24) -> int:
        n, neg = self._unpack("<ii")
        if neg != -n or not (0 <= n <= limit):
            raise ChartDataError(f"{what}: bad count {n}/{neg} at {self.o - 8}")
        return n


# -- header --------------------------------------------------------------------------
def parse_header(mp: bytes) -> tuple[str, tuple[int, int, int]]:
    if mp[:4] not in STREAM_MAGICS:
        raise ChartDataError("Map stream does not start with the aa aa 14/15 55 magic")
    n, = struct.unpack_from("<H", mp, 0x10)
    name = mp[0x12 : 0x12 + n].rstrip(b"\x00").decode("latin-1")
    ver = struct.unpack_from("<3h", mp, 0x12 + n)
    return name, (ver[0], ver[1], ver[2])


# -- sequential grammar ---------------------------------------------------------------
class _Objects:
    """Registry of serialized objects by id, for resolving references."""

    def __init__(self):
        self.units: dict[int, UnitsConversion] = {}
        self.setups: dict[int, ChannelSetup] = {}
        self.units_in_order: list[UnitsConversion] = []


def _parse_units_object(c: _Cursor, objects: _Objects) -> tuple[Optional[UnitsConversion], int]:
    """A units-object slot: ``(units or None, object id)``; 0 = no object."""
    start = c.o
    tag = c.u32()
    if not tag & NEW_OBJECT:
        if tag == 0:
            return None, 0
        u = objects.units.get(tag)
        if u is None:
            raise ChartDataError(f"units object slot references unknown object {tag} at {start}")
        return u, tag
    oid = tag & 0x7FFFFFFF
    c.i32()                 # 5: object version
    c.i32()                 # 2
    lsb = c.f64()
    c.raw(9)                # eight zero bytes + one flag byte (0x6b seen for calculated channels)
    c.raw(8)                # four int16 (display related; not interpreted)
    c.raw(9)                # nine bytes (not interpreted)
    base = c.tagged("units input")
    slope, offset = c.f64(), c.f64()
    mode, i1, flags, i3 = c.i32(), c.i32(), c.i32(), c.i32()
    units = c.tagged("units name")
    cal = tuple(c.tagged("calibration string") for _ in range(4))
    c.u8()                  # trailing 0x00 of the object
    if not (math.isfinite(slope) and math.isfinite(offset) and math.isfinite(lsb)):
        raise ChartDataError(f"units object at {start} holds non-finite numbers")
    u = UnitsConversion(base_units=base, units=units.strip(), slope=slope, offset=offset, lsb=lsb,
                        mode=mode, flags=flags, calibration=cal, object_id=oid, stream_offset=start)  # type: ignore[arg-type]
    objects.units[oid] = u
    objects.units_in_order.append(u)
    return u, oid


_SETUP_RE = re.compile(r'<_ChannelSetup value-code="(\d+)">', re.S)
_GAIN_RE = re.compile(r"<PhysicalGainIndex[^>]*>([^<]*)</PhysicalGainIndex>")
_SPEED_RE = re.compile(r"<SpeedIndex[^>]*>([^<]*)</SpeedIndex>")
_DEVICE_RE = re.compile(r"<Device>([^<]*)</Device>")


def _parse_volts(txt: str) -> Optional[float]:
    m = re.match(r"\s*([\d.,]+)\s*(m?V)", txt)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * 1e-3 if m.group(2) == "mV" else v


def _parse_rate(txt: str) -> Optional[float]:
    m = re.match(r"\s*([\d.,]+)\s*(k?)\s*/s", txt)
    if not m:
        return None
    return float(m.group(1).replace(",", ".")) * (1000.0 if m.group(2) == "k" else 1.0)


def channel_setup_from_xml(xml: str, fallback_index: int, object_id: int = -1) -> ChannelSetup:
    m = _SETUP_RE.search(xml)
    idx = int(m.group(1)) if m else fallback_index
    g = _GAIN_RE.search(xml)
    s = _SPEED_RE.search(xml)
    d = _DEVICE_RE.search(xml)
    return ChannelSetup(index=idx,
                        range_volts=_parse_volts(g.group(1)) if g else None,
                        sample_rate_hz=_parse_rate(s.group(1)) if s else None,
                        device=d.group(1).strip() if d else "",
                        xml=xml, object_id=object_id)


def _parse_datasource_object(c: _Cursor, objects: _Objects, channel: int) -> tuple[Optional[ChannelSetup], int]:
    """A ``ChannelDataSource`` slot (v29+): ``(setup or None, object id)``."""
    start = c.o
    tag = c.u32()
    if not tag & NEW_OBJECT:
        if tag == 0:
            return None, 0
        s = objects.setups.get(tag)
        if s is None:
            raise ChartDataError(f"data-source slot references unknown object {tag} at {start}")
        return s, tag
    oid = tag & 0x7FFFFFFF
    c.i32()                                  # 2: object version
    cls = c.tagged("data-source class")
    xml = c.tagged("data-source xml")
    if cls != "ChannelDataSource":
        raise ChartDataError(f"unexpected data-source class {cls!r} at {start}")
    s = channel_setup_from_xml(xml, channel, oid)
    objects.setups[oid] = s
    return s, oid


def _parse_channel_entry(c: _Cursor, record: RecordInfo, n_data_blocks: int, objects: _Objects) -> ChannelRecordInfo:
    start = c.o
    c.expect(b"\x01", "channel entry")
    tag = c.u32()
    if tag != ENTRY_TAG:
        raise ChartDataError(f"channel entry tag {tag:08x} at {start + 1}")
    version = c.i32()
    channel = c.i32()
    c.u32()                                  # 0x80000000 (v26) or 1 (v29)
    dtype_tag = c.u32()
    if dtype_tag & 0xFFFF0000 != DTYPE_TAG_BASE:
        raise ChartDataError(f"channel data-type tag {dtype_tag:08x} at {c.o - 4}")
    dtype = "float32" if dtype_tag & 0x2 else "int16"
    c.raw(12)                                # int32 1, 0, 1
    c.raw(12)                                # zeros
    c.raw(8)                                 # int32 1, 0
    rate = c.i64()
    units, units_ref = _parse_units_object(c, objects)
    c.i32()                                  # 1
    setup, setup_ref = (None, 0)
    if version >= 3:
        setup, setup_ref = _parse_datasource_object(c, objects, channel)
    ticks = c.i64()
    c.i32()                                  # record index
    block_ticks = c.i32()
    samples_per_block = c.i32()
    nb = c.counted("channel block index")
    if nb != n_data_blocks:
        raise ChartDataError(f"channel {channel} of record {record.index}: block index has {nb} entries, "
                             f"the record {n_data_blocks} blocks")
    blocks: list[ChannelBlock] = []
    for _ in range(nb):
        btag = c.u32()
        c.i64()                              # 0
        n_ticks = c.i32()
        c.i32()                              # 0
        mn, mx = c.f32(), c.f32()
        offset = c.i64()
        if btag & 0xFFFF0000 != DTYPE_TAG_BASE or offset < 0:
            raise ChartDataError(f"block index entry of channel {channel} at {c.o - 36} is malformed")
        blocks.append(ChannelBlock(ticks=n_ticks, min=mn, max=mx, offset=offset,
                                   dtype="float32" if btag & 0x2 else "int16"))
    return ChannelRecordInfo(channel=channel, record=record.index, dtype=dtype, tick_rate_hz=float(rate),
                             ticks=ticks, block_ticks=block_ticks, samples_per_block=samples_per_block,
                             units=units, units_ref=units_ref, setup=setup, setup_ref=setup_ref,
                             blocks=blocks, entry_version=version, stream_offset=start)


def _parse_block_list_at(c: _Cursor, record: RecordInfo) -> tuple[list[BlockInfo], list[Comment]]:
    n = c.counted("block list")
    blocks: list[BlockInfo] = []
    comments: list[Comment] = []
    tick0 = 0
    for _ in range(n):
        _three, bt, flags, _x, ncom = c._unpack("<iiihh")
        if not (0 <= bt <= BLOCK_TICKS and 0 <= ncom <= 100000):
            raise ChartDataError(f"block list entry of record {record.index} at {c.o - 16} is malformed")
        for _k in range(ncom):
            _ctype, _z, cid, ch, tick = c._unpack("<iiihh")
            text = c.tagged("comment")
            comments.append(Comment(id=cid, record_index=record.index, channel=ch,
                                    tick=tick0 + (tick & 0xFFFF), text=text))
        blocks.append(BlockInfo(ticks=bt, flags=flags, n_comments=ncom))
        tick0 += bt
    if tick0 != record.ticks:
        raise ChartDataError(f"record {record.index}: blocks cover {tick0} ticks, the record {record.ticks}")
    return blocks, comments


def _parse_record(c: _Cursor, index: int, n_channels: int, objects: _Objects) -> RecordInfo:
    start = c.o
    version = c.i32()
    c.i32()                                  # 1
    c.i32()                                  # 0
    rate = c.i64()
    start_unix = c.f64()
    tz = c.f64()
    if not (1 <= rate <= 100_000_000) or not math.isfinite(start_unix) or not math.isfinite(tz) or abs(tz) > 24 * 3600:
        raise ChartDataError(f"record header at {start} is implausible")
    c.raw(12)                                # zeros
    first_block = c.i32()
    ticks = c.i64()
    if ticks < 0:
        raise ChartDataError(f"record {index}: negative tick count")
    rec = RecordInfo(index=index, tick_rate_hz=float(rate), start_utc=start_unix, utc_offset_s=-tz,
                     ticks=ticks, stream_offset=start + 12, first_block_index=first_block, version=version)
    rec.name = c.tagged("record name")
    slot = c.u32()                           # object slot: a small object in the first v26 record, a reference later, 0 in v29
    if slot & NEW_OBJECT:
        c.i32()
        c.tagged("record object")
    c.u32()                                  # 0 (v26) / 0x30000000 (v29)
    rec.blocks, rec.comments = _parse_block_list_at(c, rec)
    n_data_blocks = sum(1 for b in rec.blocks if b.ticks > 0)
    n = c.counted("channel list")
    if n != n_channels:
        raise ChartDataError(f"record {index} lists {n} channels, the header {n_channels}")
    rec.channels = [_parse_channel_entry(c, rec, n_data_blocks, objects) for _ in range(n)]
    for i, ch in enumerate(rec.channels):
        if ch.channel != i:
            raise ChartDataError(f"record {index}: channel entry {i} is labelled {ch.channel}")
    # drop empty trailing blocks: they hold no samples and no sub-blocks
    rec.blocks = [b for b in rec.blocks if b.ticks > 0]
    return rec


def parse_sequential(mp: bytes) -> ChartData:
    """Parse the whole stream by its grammar (raises :class:`ChartDataError` on any deviation)."""
    name, version = parse_header(mp)
    c = _Cursor(mp, 0x12 + struct.unpack_from("<H", mp, 0x10)[0] + 6)
    n_channels = version[2]
    c.raw(9)
    objects = _Objects()
    records: list[RecordInfo] = []
    while True:
        rec = _parse_record(c, len(records), n_channels, objects)
        records.append(rec)
        marker = c.i32()
        if marker == -1:
            break
        if marker != len(records):
            raise ChartDataError(f"record separator {marker} after record {rec.index} at {c.o - 4}")
        c.i32()                              # 0
    n_titles = c.counted("channel titles")
    titles = [c.tagged("channel title") for _ in range(n_titles)]
    trailer = bytes(mp[c.o:])
    setups = {}
    for rec in records:
        for ch in rec.channels:
            if ch.setup is not None and ch.channel not in setups:
                setups[ch.channel] = ch.setup
    return ChartData(object_name=name, format_version=version, records=records, titles=titles,
                     units=objects.units_in_order, setups=setups, n_channels=n_channels,
                     parse_mode="sequential", trailer=trailer)


# -- legacy heuristics (fallback) ------------------------------------------------------
def scan_records(mp: bytes) -> list[RecordInfo]:
    """Find every record header: int64 tick rate, float64 start (Unix UTC),
    float64 (UTC - local) offset, 12 zero bytes, int32 counter, int64 ticks."""
    recs: list[RecordInfo] = []
    o = 0
    n = len(mp)
    while o + 48 <= n:
        rate, = struct.unpack_from("<q", mp, o)
        if 1 <= rate <= 10_000_000:
            start, tz = struct.unpack_from("<dd", mp, o + 8)
            if 5.0e8 < start < 4.0e9 and abs(tz) <= 15 * 3600 and tz == int(tz) and mp[o + 24 : o + 36] == bytes(12):
                ticks, = struct.unpack_from("<q", mp, o + 40)
                if 0 <= ticks < 2**40:
                    recs.append(RecordInfo(index=len(recs), tick_rate_hz=float(rate), start_utc=start,
                                           utc_offset_s=-tz, ticks=ticks, stream_offset=o))
                    o += 48
                    continue
        o += 1
    return recs


def parse_block_list(mp: bytes, k: int, record: RecordInfo) -> Optional[tuple[list[BlockInfo], list[Comment], int]]:
    """Parse a block list at offset ``k`` (legacy path).

    Layout: ``int32 n, int32 -n`` then per block a 16-byte entry
    ``(int32 3, int32 ticks, int32 flags, int16 ?, int16 n_comments)`` followed
    by ``n_comments`` comment records ``(int32 2, int32 0, int32 id,
    int16 channel, int16 tick_in_block, tagged text)``.
    Returns ``(blocks, comments, end_offset)`` or ``None`` if it does not parse.
    """
    if k + 8 > len(mp):
        return None
    n, neg = struct.unpack_from("<ii", mp, k)
    if not (1 <= n <= 1_000_000 and neg == -n):
        return None
    p = k + 8
    blocks: list[BlockInfo] = []
    comments: list[Comment] = []
    tick0 = 0
    for _ in range(n):
        if p + 16 > len(mp):
            return None
        _three, bt, flags, _x, ncom = struct.unpack_from("<iiihh", mp, p)
        if not (0 <= bt <= BLOCK_TICKS and 0 <= ncom <= 10000):
            return None
        p += 16
        for _c in range(ncom):
            if p + 16 > len(mp):
                return None
            _ctype, _z, cid, ch, tick = struct.unpack_from("<iiihh", mp, p)
            p += 16
            if mp[p : p + 4] != TAG:
                return None
            r = read_prefixed(mp, p + 4)
            if r is None:
                return None
            comments.append(Comment(id=cid, record_index=record.index, channel=ch,
                                    tick=tick0 + (tick & 0xFFFF), text=r[0]))
            p = r[1]
        if bt > 0:
            blocks.append(BlockInfo(ticks=bt, flags=flags, n_comments=ncom))
        tick0 += bt
    if tick0 != record.ticks or not blocks:
        return None
    return blocks, comments, p


def attach_block_lists(mp: bytes, records: list[RecordInfo]) -> None:
    """Fill ``record.blocks`` and ``record.comments`` for every record (in place)."""
    for r in records:
        o = r.stream_offset + 48
        for k in range(o, min(o + 256, len(mp) - 8)):
            res = parse_block_list(mp, k, r)
            if res is not None:
                r.blocks, r.comments, _end = res
                break
        else:
            r.blocks = [BlockInfo(ticks=min(BLOCK_TICKS, r.ticks - t), flags=0, n_comments=0)
                        for t in range(0, r.ticks, BLOCK_TICKS)]


def parse_channel_titles(mp: bytes) -> list[str]:
    """The channel table: ``int32 -1, int32 N, int32 -N`` then N tagged strings."""
    for m in re.finditer(rb"\xff\xff\xff\xff(?=.{8}\x01\x00\x00\x80)", mp, re.S):
        n, neg = struct.unpack_from("<ii", mp, m.start() + 4)
        if not (1 <= n <= 256 and neg == -n):
            continue
        names: list[str] = []
        i = m.start() + 12
        for _ in range(n):
            if mp[i : i + 4] != TAG:
                break
            r = read_prefixed(mp, i + 4)
            if r is None:
                break
            names.append(r[0])
            i = r[1]
        if len(names) == n:
            return names
    return []


def parse_units_records(mp: bytes) -> list[UnitsConversion]:
    """Scan for units objects by their string/float/string pattern (legacy path)."""
    out: list[UnitsConversion] = []
    for m in re.finditer(re.escape(TAG), mp):
        i = m.start()
        base = read_prefixed(mp, i + 4)
        if base is None or len(base[0]) > 12:
            continue
        j = base[1]
        if j + 16 + 16 + 4 > len(mp):
            continue
        slope, offset = struct.unpack_from("<dd", mp, j)
        if not (math.isfinite(slope) and math.isfinite(offset)) or abs(slope) > 1e12 or abs(offset) > 1e12:
            continue
        mode, _i1, flags, _i3 = struct.unpack_from("<iiii", mp, j + 16)
        k = j + 32
        if mp[k : k + 4] != TAG:
            continue
        strs: list[str] = []
        while len(strs) < 5 and mp[k : k + 4] == TAG:
            r = read_prefixed(mp, k + 4)
            if r is None:
                break
            strs.append(r[0])
            k = r[1]
        if len(strs) != 5:
            continue
        units = strs[0].strip()
        if len(units) > 16 or units.lower().startswith("channel "):
            continue
        lsb = math.nan
        if i >= 46 and mp[i - 26 : i - 18] == bytes(8):   # the eight zero bytes that follow the LSB in a units object
            cand, = struct.unpack_from("<d", mp, i - 34)
            if math.isfinite(cand) and 0 < cand < 1:
                lsb = cand
        out.append(UnitsConversion(base_units=base[0], units=units, slope=slope, offset=offset, lsb=lsb,
                                   mode=mode if 0 <= mode <= 8 else -1, flags=flags,
                                   calibration=(strs[1], strs[2], strs[3], strs[4]), stream_offset=i))
    return out


def parse_channel_setups(mp: bytes) -> dict[int, ChannelSetup]:
    """The XML sits in tagged strings (one per channel per record), at any byte alignment."""
    out: dict[int, ChannelSetup] = {}
    for _o, s in iter_tagged_strings(mp):
        if "<_ChannelSetup" not in s:
            continue
        setup = channel_setup_from_xml(s, len(out))
        out.setdefault(setup.index, setup)
    return out


def parse_scan(mp: bytes) -> ChartData:
    """Legacy heuristic parse (no channel entries, no block index)."""
    name, version = parse_header(mp)
    records = scan_records(mp)
    attach_block_lists(mp, records)
    return ChartData(object_name=name, format_version=version, records=records,
                     titles=parse_channel_titles(mp), units=parse_units_records(mp),
                     setups=parse_channel_setups(mp), n_channels=version[2] if 0 < version[2] < 1024 else 0,
                     parse_mode="scan")


# -- entry point --------------------------------------------------------------------------
def parse_chart_data(mp: bytes, strict: bool = False) -> ChartData:
    """Parse the Map stream: the full grammar first, the heuristics as a fallback
    (``strict=True`` disables the fallback and raises instead)."""
    try:
        return parse_sequential(mp)
    except ChartDataError:
        if strict:
            raise
        return parse_scan(mp)


def tagged_strings(mp: bytes) -> list[tuple[int, str]]:
    """All tagged strings of the stream (titles, units, comments, XML) for inspection."""
    return list(iter_tagged_strings(mp))
