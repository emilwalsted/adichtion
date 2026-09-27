"""Parser for the ``Map`` stream ("ADInstruments Chart Data").

The stream holds the document-level metadata: records with their start time
and length, the 16000-tick block list of each record with the comments that
fall in each block, the channel titles, the units-conversion records and (in
format v29 and later) an XML ``<_ChannelSetup>`` block per channel.

The stream is not a fixed-layout structure; this module locates the parts by
their self-checking ``(n, -n)`` length fields and known markers.  See
``docs/FORMAT.md``.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Optional

from .model import BlockInfo, ChannelSetup, ChartData, Comment, RecordInfo, UnitsConversion
from .serialization import TAG, iter_tagged_strings, read_prefixed

BLOCK_TICKS = 16000
STREAM_MAGICS = (bytes.fromhex("aaaa1455"), bytes.fromhex("aaaa1555"))


class ChartDataError(ValueError):
    pass


# -- header ------------------------------------------------------------------
def parse_header(mp: bytes) -> tuple[str, tuple[int, int, int]]:
    if mp[:4] not in STREAM_MAGICS:
        raise ChartDataError("Map stream does not start with the aa aa 14/15 55 magic")
    n, = struct.unpack_from("<H", mp, 0x10)
    name = mp[0x12 : 0x12 + n].rstrip(b"\x00").decode("latin-1")
    ver = struct.unpack_from("<3h", mp, 0x12 + n)
    return name, (ver[0], ver[1], ver[2])


# -- records -----------------------------------------------------------------
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
    """Parse a block list at offset ``k``.

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
        if not (1 <= bt <= BLOCK_TICKS and 0 <= ncom <= 10000):
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
        blocks.append(BlockInfo(ticks=bt, flags=flags, n_comments=ncom))
        tick0 += bt
    if tick0 != record.ticks:
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
            # older files (v26) carry a different per-block index; synthesise the block list
            r.blocks = [BlockInfo(ticks=min(BLOCK_TICKS, r.ticks - t), flags=0, n_comments=0)
                        for t in range(0, r.ticks, BLOCK_TICKS)]


# -- channel titles ----------------------------------------------------------
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


# -- units conversion records --------------------------------------------------
def parse_units_records(mp: bytes) -> list[UnitsConversion]:
    """Scan for units-conversion records (see ``docs/FORMAT.md``)."""
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
        out.append(UnitsConversion(base_units=base[0], units=units, slope=slope, offset=offset,
                                   calibration=(strs[1], strs[2], strs[3], strs[4]), stream_offset=i))
    return out


# -- channel setup XML (v29+) --------------------------------------------------
_SETUP_RE = re.compile(r'<_ChannelSetup value-code="(\d+)">(.*?)</_ChannelSetup>', re.S)
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


def parse_channel_setups(mp: bytes) -> dict[int, ChannelSetup]:
    """The XML sits in tagged strings (one per channel), at any byte alignment."""
    text = "\n".join(s for _o, s in iter_tagged_strings(mp) if "<_ChannelSetup" in s)
    out: dict[int, ChannelSetup] = {}
    for m in _SETUP_RE.finditer(text):
        idx = int(m.group(1))
        xml = m.group(2)
        g = _GAIN_RE.search(xml)
        s = _SPEED_RE.search(xml)
        d = _DEVICE_RE.search(xml)
        out[idx] = ChannelSetup(index=idx,
                                range_volts=_parse_volts(g.group(1)) if g else None,
                                sample_rate_hz=_parse_rate(s.group(1)) if s else None,
                                device=d.group(1).strip() if d else "",
                                xml=xml)
    return out


# -- everything ----------------------------------------------------------------
def parse_chart_data(mp: bytes) -> ChartData:
    name, version = parse_header(mp)
    records = scan_records(mp)
    attach_block_lists(mp, records)
    return ChartData(object_name=name, format_version=version, records=records,
                     titles=parse_channel_titles(mp), units=parse_units_records(mp),
                     setups=parse_channel_setups(mp))


def tagged_strings(mp: bytes) -> list[tuple[int, str]]:
    """All tagged strings of the stream (titles, units, comments, XML) for inspection."""
    return list(iter_tagged_strings(mp))
