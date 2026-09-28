"""Writer for *synthetic* ``.adicht`` files.

This exists for the test-suite: it produces files that exercise every code
path of the reader (container with nested storages, Map stream grammar with
units objects and references, per-channel block index, packed/unpacked int16
and float32 sub-blocks, comments, v29 ``ChannelDataSource`` XML, the video
stream) without any real recording.  It writes only what :mod:`adichtion`
reads back; LabChart itself may or may not open the result, that is not the
goal.  ``layout="legacy"`` writes the loose, scan-only layout the first
version of the reader understood, to keep the fallback parser tested.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .blocks import (BLOCK_TICKS, FLAG_32BIT, FLAG_BASE, FLAG_PACKED, FLAG_UNPACKED, FLOAT32_PLANES,
                     INT16_PLANES, MAGIC as BLOCKS_MAGIC, WORD_SAMPLES, packbits_encode, plane_bytes)
from .chartdata import DTYPE_TAG_BASE, ENTRY_TAG, NEW_OBJECT
from .container import ENTRY_SIZE, HEADER_SIZE, MAGIC as CONTAINER_MAGIC, TYPE_STORAGE, TYPE_STREAM
from .serialization import TAG
from .video import VIDEO_GUID


# -- primitives ---------------------------------------------------------------
def prefixed(text: str) -> bytes:
    data = (text + "\x00").encode("utf-16le")
    n = len(data) // 2
    return struct.pack("<ii", n, -n) + data


def tagged(text: str) -> bytes:
    return TAG + prefixed(text)


def counted(n: int) -> bytes:
    return struct.pack("<ii", n, -n)


def pack_planes(symbols: np.ndarray, n_planes: int, msb_first: bool, word_reversed: bool = False) -> bytes:
    """Inverse of :func:`adichtion.blocks.unpack_planes`."""
    n = len(symbols)
    pb = plane_bytes(n)
    out = bytearray()
    order = range(n_planes - 1, -1, -1) if msb_first else range(n_planes)
    sym = np.zeros(pb * 8, dtype=np.uint64)
    sym[:n] = symbols.astype(np.uint64)
    if word_reversed:
        sym = sym.reshape(-1, WORD_SAMPLES)[:, ::-1].reshape(-1)
    for p in order:
        bits = ((sym >> np.uint64(p)) & np.uint64(1)).astype(np.uint8)
        out += np.packbits(bits, bitorder="little").tobytes()
    return bytes(out)


def encode_int16(x: np.ndarray) -> bytes:
    """17-plane sign-magnitude delta buffer of an int16 sequence."""
    x = np.asarray(x, dtype=np.int64)
    d = np.diff(np.concatenate([[0], x]))
    d = (d + 32768) % 65536 - 32768          # int16 wrap-around
    m = 2 * np.abs(d) + (d < 0)
    return pack_planes(m, INT16_PLANES, msb_first=False)


def encode_float32(x: np.ndarray, prev_last: float = 0.0) -> bytes:
    """32-plane buffer of float32 differences (first sample as-is), samples
    reversed within each 32-bit word.  Differences are formed so that a
    float32 running sum reproduces ``x`` exactly."""
    x = np.asarray(x, dtype=np.float32)
    d = np.empty(len(x), dtype=np.float32)
    acc = np.float32(0)
    for i, v in enumerate(x):
        d[i] = np.float32(v - acc)
        acc = np.float32(acc + d[i])
    return pack_planes(d.view(np.uint32), FLOAT32_PLANES, msb_first=True, word_reversed=True)


def subblock(samples: np.ndarray, is_float: bool, packed: bool = True, prev_last: float = 0.0) -> bytes:
    """One complete sub-block (header + body)."""
    if packed:
        buf = encode_float32(samples, prev_last) if is_float else encode_int16(samples)
        raw = len(samples) * (4 if is_float else 2)
        body = struct.pack("<i", raw) + packbits_encode(buf)
        flags = FLAG_BASE | FLAG_PACKED | (FLAG_32BIT if is_float else 0)
    else:
        body = np.asarray(samples, dtype="<f4" if is_float else "<i2").tobytes()
        flags = FLAG_UNPACKED | FLAG_BASE | (FLAG_32BIT if is_float else 0)
    n = len(body) + 12
    return struct.pack("<iiiI", n, 0, -n, flags & 0xFFFFFFFF) + body


# -- synthetic document -------------------------------------------------------
@dataclass
class SynthChannel:
    title: str
    dtype: str = "int16"                    # "int16" or "float32"
    units: Optional[tuple[str, float, float]] = None   # (name, slope, offset): an enabled units conversion
    range_volts: float = 10.0               # int16 channels: LSB = range * 1.024 / 32768
    unpacked_blocks: Sequence[tuple[int, int]] = ()     # (record, block) pairs written unpacked (float only)
    lsb: Optional[float] = None             # float32 channels: scale of the stored values (default 2**-15)

    @property
    def is_float(self) -> bool:
        return self.dtype == "float32"

    @property
    def stored_lsb(self) -> float:
        if self.is_float:
            return self.lsb if self.lsb is not None else 2.0 ** -15
        return self.range_volts * 1.024 / 32768.0


@dataclass
class SynthRecord:
    ticks: int
    start_utc: float = 1_700_000_000.0
    utc_offset_s: float = 3600.0
    comments: list[tuple[int, int, str]] = field(default_factory=list)   # (tick, channel or -1, text)
    data: dict[int, np.ndarray] = field(default_factory=dict)            # channel -> samples (ticks long)
    videos: Optional[tuple[str, float, float, float, float]] = None      # (file name, start, stop, video start, offset)


def _blocks_of(r: SynthRecord) -> list[int]:
    return [min(BLOCK_TICKS, r.ticks - t) for t in range(0, r.ticks, BLOCK_TICKS)]


def build_blocks_stream(channels: Sequence[SynthChannel], records: Sequence[SynthRecord]):
    """Return ``(stream bytes, index)`` where ``index[(record, block, channel)] = (offset, min, max)``."""
    out = bytearray(BLOCKS_MAGIC)
    index: dict[tuple[int, int, int], tuple[int, float, float]] = {}
    for ri, r in enumerate(records):
        t0 = 0
        prev_last = [0.0] * len(channels)
        for bi, bt in enumerate(_blocks_of(r)):
            for ci, c in enumerate(channels):
                x = np.asarray(r.data[ci])[t0:t0 + bt]
                packed = (ri, bi) not in tuple(c.unpacked_blocks)
                index[(ri, bi, ci)] = (len(out), float(x.min()), float(x.max()))
                out += subblock(x, c.is_float, packed, prev_last[ci])
                prev_last[ci] = float(x[-1]) if c.is_float else 0.0
            t0 += bt
    return bytes(out), index


def _channel_xml(i: int, c: SynthChannel, tick_rate: int) -> str:
    rng = f"{c.range_volts * 1000:g} mV" if c.range_volts < 1 else f"{c.range_volts:g} V"
    return (f'<_ChannelSetup value-code="{i}">{i}\n'
            f'<PhysicalGainIndex units-format="UnitsGain" value-code="5">{rng}</PhysicalGainIndex>\n'
            f'<SpeedIndex units-format="UnitsSamplingRate" value-code="12">{tick_rate} /s</SpeedIndex>\n'
            f'<Device>Synthetic PowerLab</Device>\n</_ChannelSetup>')


def _units_object(oid: int, c: SynthChannel) -> bytes:
    name, slope, offset = c.units if c.units else ("", 1.0, 0.0)
    enabled = c.units is not None
    out = bytearray(struct.pack("<Iii", NEW_OBJECT | oid, 5, 2))
    out += struct.pack("<d", c.stored_lsb)
    out += bytes(8) + (b"\x6b" if c.is_float else b"\x00")
    out += struct.pack("<4h", -2, 70, -2, 1000)
    out += bytes.fromhex("000000010000000000")
    out += tagged("" if c.is_float else "V")
    out += struct.pack("<dd", slope, offset)
    out += struct.pack("<iiii", 1 if enabled else 2, 1, 0x10000 if enabled else 0, 0)
    out += tagged(name)
    if enabled:
        out += tagged("1 V") + tagged("0 V") + tagged(f"{slope + offset:g}") + tagged(f"{offset:g}")
    else:
        out += tagged("") * 4
    out += b"\x00"
    return bytes(out)


def build_map_stream(channels: Sequence[SynthChannel], records: Sequence[SynthRecord], tick_rate: int,
                     block_index, blocks_size: int, version: int = 29) -> bytes:
    """The Map stream in the sequential grammar (format v26 or v29 flavour)."""
    v29 = version >= 29
    out = bytearray(bytes.fromhex("aaaa1555" if v29 else "aaaa1455") + struct.pack("<i", 2) + bytes(8))
    name = b"ADInstruments Chart Data\x00"
    out += struct.pack("<H", len(name)) + name + struct.pack("<3h", 3, 7 if v29 else 5, len(channels)) + bytes(9)
    next_id = 1
    units_ids: dict[int, int] = {}
    setup_ids: dict[int, int] = {}
    record_obj_id = 0
    cid = 1
    first_block = 0
    for ri, r in enumerate(records):
        blocks = _blocks_of(r)
        out += struct.pack("<iii", 3 if v29 else 2, 1, 0)
        out += struct.pack("<qdd", tick_rate, r.start_utc, -r.utc_offset_s) + bytes(12)
        out += struct.pack("<i", first_block) + struct.pack("<q", r.ticks)
        out += tagged("")
        if v29:
            out += struct.pack("<II", 0, 0x30000000)
        elif record_obj_id == 0:
            record_obj_id = next_id
            next_id += 1
            out += struct.pack("<Ii", NEW_OBJECT | record_obj_id, 1) + tagged("") + struct.pack("<I", 0)
        else:
            out += struct.pack("<II", record_obj_id, 0)
        out += counted(len(blocks))
        t0 = 0
        for bi, bt in enumerate(blocks):
            cms = [(tk, ch, txt) for tk, ch, txt in r.comments if t0 <= tk < t0 + bt]
            flags = (0x80000000 if ri == 0 else 0xC0000000) if bi == 0 else 0
            out += struct.pack("<iiIhh", 3, bt, flags, 0, len(cms))
            for tk, ch, txt in cms:
                out += struct.pack("<iiihh", 2, 0, cid, ch, tk - t0) + tagged(txt)
                cid += 1
            t0 += bt
        out += counted(len(channels))
        for ci, c in enumerate(channels):
            dtag = DTYPE_TAG_BASE | (3 if c.is_float else 1)
            out += b"\x01" + struct.pack("<IiiII", ENTRY_TAG, 3 if v29 else 2, ci, 1 if v29 else 0x80000000, dtag)
            out += struct.pack("<iii", 1, 0, 1) + bytes(12) + struct.pack("<ii", 1, 0) + struct.pack("<q", tick_rate)
            if ci in units_ids:
                out += struct.pack("<I", units_ids[ci])
            else:
                units_ids[ci] = next_id
                out += _units_object(next_id, c)
                next_id += 1
            out += struct.pack("<i", 1)
            if v29:
                if ci in setup_ids:
                    out += struct.pack("<I", setup_ids[ci])
                else:
                    setup_ids[ci] = next_id
                    out += struct.pack("<Ii", NEW_OBJECT | next_id, 2) + tagged("ChannelDataSource") + tagged(_channel_xml(ci, c, tick_rate))
                    next_id += 1
            out += struct.pack("<qiii", r.ticks, ri, BLOCK_TICKS, BLOCK_TICKS)
            out += counted(len(blocks))
            for bi, bt in enumerate(blocks):
                off, mn, mx = block_index[(ri, bi, ci)]
                out += struct.pack("<IqiiffQ", dtag, 0, bt, 0, mn, mx, off)
        first_block += len(blocks)
        out += struct.pack("<ii", ri + 1, 0) if ri + 1 < len(records) else struct.pack("<i", -1)
    out += counted(len(channels))
    for c in channels:
        out += tagged(c.title)
    if v29:
        raw_total = sum(r.ticks * (4 if c.is_float else 2) for r in records for c in channels)
        out += struct.pack("<qq", raw_total, blocks_size) + tagged("")
    else:
        out += bytes(16)
    return bytes(out)


def build_map_stream_legacy(channels: Sequence[SynthChannel], records: Sequence[SynthRecord], tick_rate: int = 1000) -> bytes:
    """The loose layout of adichtion 0.1: records with block lists, a units
    record per channel, ``<_ChannelSetup>`` XML strings and the titles table,
    without channel entries.  Only the scan parser understands it."""
    out = bytearray(bytes.fromhex("aaaa1555") + struct.pack("<i", 2) + bytes(8))
    name = b"ADInstruments Chart Data\x00"
    out += struct.pack("<H", len(name)) + name + struct.pack("<3h", 3, 7, len(channels)) + bytes(10)
    cid = 1
    for r in records:
        out += struct.pack("<qdd", tick_rate, r.start_utc, -r.utc_offset_s) + bytes(12) + struct.pack("<i", 0)
        out += struct.pack("<q", r.ticks)
        out += tagged("")
        blocks = _blocks_of(r)
        out += counted(len(blocks))
        t0 = 0
        for bt in blocks:
            cms = [(tk, ch, txt) for tk, ch, txt in r.comments if t0 <= tk < t0 + bt]
            out += struct.pack("<iiihh", 3, bt, 0, 0, len(cms))
            for tk, ch, txt in cms:
                out += struct.pack("<iiihh", 2, 0, cid, ch, tk - t0) + tagged(txt)
                cid += 1
            t0 += bt
    for c in channels:
        uname, slope, offset = c.units if c.units else ("", 1.0, 0.0)
        out += tagged("V") + struct.pack("<dd", slope, offset) + struct.pack("<iiii", 1, 1, 0x10000 if c.units else 0, 0)
        out += tagged(uname) + tagged("1 V" if c.units else "") + tagged("0 V" if c.units else "")
        out += tagged(f"{slope + offset:g}" if c.units else "") + tagged(f"{offset:g}" if c.units else "")
    for i, c in enumerate(channels):
        out += tagged(_channel_xml(i, c, tick_rate))
    out += struct.pack("<iii", -1, len(channels), -len(channels))
    for c in channels:
        out += tagged(c.title)
    return bytes(out)


def build_video_stream(records: Sequence[SynthRecord]) -> bytes:
    out = bytearray(VIDEO_GUID + bytes.fromhex("aaaa1555") + struct.pack("<ii", 3, len(records)))
    for r in records:
        name, a, b, c, d = r.videos if r.videos else ("", -1.0, -1.0, -1.0, -1.0)
        out += struct.pack("<4d", a, b, c, d) + tagged(name)
    out += struct.pack("<i", 3) + tagged("")
    return bytes(out)


# -- container ------------------------------------------------------------------
def build_container(streams: dict, cluster_size: int = 4096, version: int = 29) -> bytes:
    """Lay out streams contiguously (cluster numbers start at 2), then the
    directory tables (one per storage), then the cluster-map pages.  A value
    that is a ``dict`` becomes a nested storage."""
    entries_per_page = cluster_size // 8
    body = bytearray()
    cluster = 2
    links: list[tuple[int, int]] = []       # (first, last) runs to link in the cluster map

    def place(data: bytes) -> tuple[int, int]:
        nonlocal cluster, body
        n = max(1, math.ceil(len(data) / cluster_size))
        first = cluster
        body += data + bytes(n * cluster_size - len(data))
        cluster += n
        links.append((first, first + n - 1))
        return first, first + n - 1

    def entry(name: str, first: int, size: int, last: int, typ: int) -> bytes:
        e = bytearray(ENTRY_SIZE)
        nm = name.encode("utf-16le")
        e[:len(nm)] = nm
        struct.pack_into("<qqq", e, 0x48, first, size, last)
        struct.pack_into("<ii", e, 0x88, 0, typ)
        return bytes(e)

    def write_storage(items: dict) -> tuple[int, int]:
        """Write the children (streams and sub-storages) and then this storage's
        table; returns ``(table cluster, table size)``."""
        rows = []
        for name, data in items.items():
            if isinstance(data, dict):
                tc, tsize = write_storage(data)
                rows.append(entry(name, tc, tsize, tc, TYPE_STORAGE))
            else:
                first, last = place(data)
                rows.append(entry(name, first, len(data), last, TYPE_STREAM))
        table = counted(len(rows)) + b"".join(rows)
        tc, _ = place(table)
        return tc, len(table)

    root_cluster, root_size = write_storage(streams)
    map_first = cluster
    pages = math.ceil((cluster + 1) / entries_per_page)
    capacity = pages * entries_per_page
    cmap = bytearray(pages * cluster_size)
    for first, last in links:
        for c in range(first, last + 1):
            struct.pack_into("<ii", cmap, c * 8, c + 1 if c < last else 0, c - 1 if c > first else 0)
    body += cmap
    hdr = bytearray(HEADER_SIZE)
    hdr[:len(CONTAINER_MAGIC)] = CONTAINER_MAGIC
    struct.pack_into("<7i", hdr, 0x80, version, HEADER_SIZE, 131072, 0x7FFFFFFF, 0, 0, int(math.log2(cluster_size)))
    struct.pack_into("<qqq", hdr, 0x9C, capacity, -capacity, map_first)
    hdr[0xB8:0xB8 + ENTRY_SIZE] = entry("ADIRootStorage", root_cluster, root_size, root_cluster, TYPE_STORAGE)
    return bytes(hdr) + bytes(body)


def write_synthetic(path, channels: Sequence[SynthChannel], records: Sequence[SynthRecord], tick_rate: int = 1000,
                    cluster_size: int = 4096, version: int = 29, layout: str = "sequential",
                    video: bool = False) -> None:
    """Write a synthetic document.  ``version`` selects the v26 or v29 flavour
    of the Map stream (``layout="legacy"`` writes the scan-only layout);
    ``video=True`` adds the video module's stream."""
    blocks, index = build_blocks_stream(channels, records)
    if layout == "legacy":
        mp = build_map_stream_legacy(channels, records, tick_rate)
    else:
        mp = build_map_stream(channels, records, tick_rate, index, len(blocks), version)
    streams: dict = {"Version": tagged("adichtion synthetic"), "Map": mp, "Blocks_0": blocks}
    if video:
        streams["ChildDocuments"] = {"ADIStdPart4": build_video_stream(records)}
    with open(path, "wb") as fh:
        fh.write(build_container(streams, cluster_size, version))


# -- LabChart for Mac documents (docs/MAC_FORMAT.md) ------------------------------------------
@dataclass
class SynthMacChannel:
    """One channel of a synthetic LabChart for Mac document."""

    title: str
    decimation: int = 1                     # store every d-th tick; 0 = no data (a calculated channel)
    base_units: str = "V"                   # the range entry's units: "V" or "mV"
    full_scale: float = 10.24               # range x 1.024, in base_units
    units: Optional[tuple[str, float, float, bool]] = None   # (name <= 6 chars, slope, offset, switched on)
    calculation: str = ""                   # a calculated channel's menu label, e.g. "Arithmetic…"
    expression: str = ""                    # its Arithmetic expression
    raw_blocks: Sequence[tuple[int, int]] = ()        # (record, block) stored as raw float32
    no_minmax_blocks: Sequence[tuple[int, int]] = ()  # (record, block) whose index holds no min/max


@dataclass
class SynthMacRecord:
    """One LabChart block: ``data[c]`` holds channel ``c``'s ADC counts at its own
    rate (``ceil(ticks / d)`` values), None for a channel without data."""

    ticks: int
    data: Sequence[Optional[np.ndarray]]
    start_unix: float                       # local wall-clock start, seconds since 1970
    comments: Sequence[tuple[int, int, str]] = ()   # (tick, 0-based channel or -1, text)
    marks: Sequence[int] = ()               # ticks of text-less kind-2 entries


def _ext80(v: float) -> bytes:
    """A float as a big-endian 80-bit IEEE extended (the inverse of ``macdoc.ext80``)."""
    if v == 0:
        return bytes(10)
    m, e = math.frexp(abs(v))                  # |v| = m * 2**e, 0.5 <= m < 1
    mant = int(m * 2.0**64)                    # explicit integer bit set
    se = (e - 1 + 16383) | (0x8000 if v < 0 else 0)
    return struct.pack(">HQ", se, mant)


def _mac_pstr(text: str, size: int, nbsp: bool = False) -> bytes:
    raw = (("\xa0" if nbsp else "") + text).encode("mac_roman")
    return (bytes([len(raw)]) + raw).ljust(size, b"\0")[:size]


def encode_mac_block(counts: np.ndarray, packed: bool = True) -> bytes:
    """One Mac sample block: 0x01 + PackBits of 32 MSB-first bit planes of
    sign-magnitude differences (big-endian bit order), or raw float32."""
    x = np.asarray(counts, dtype=np.int64)
    if not packed:
        return (x / 32768.0).astype(">f4").tobytes()
    d = np.diff(np.concatenate([[0], x]))
    sym = (2 * np.abs(d) + (d < 0)).astype(np.uint64)
    n = len(sym)
    padded = np.zeros(plane_bytes(n) * 8, dtype=np.uint64)
    padded[:n] = sym
    planes = b"".join(np.packbits(((padded >> np.uint64(31 - p)) & np.uint64(1)).astype(np.uint8),
                                  bitorder="big").tobytes() for p in range(32))
    return b"\x01" + packbits_encode(planes)


def write_mac_document(path, channels: Sequence[SynthMacChannel], records: Sequence[SynthMacRecord],
                       tick_rate: int = 4000, movies: Optional[dict[int, str]] = None) -> None:
    """Write a synthetic LabChart for Mac document with everything the reader
    uses: storage blocks (``Alc2``, ``mTim``), per-channel block indexes
    (``Map4``) and sample runs, ranges (``Gain``), units (``UntA``), comment lists,
    titles (``STR ``), settings (``Mcro``: the channel count, calculated channels),
    and the movie table (``CntD`` groups referenced from ``Cnt1``).  ``movies``
    maps a record to its movie file name; ``""`` writes the empty entry LabChart
    leaves when the capture kept no movie."""
    from .macchart import KEY_ARITH_EXPRESSION, KEY_CALCULATION, KEY_N_CHANNELS, KEY_TITLE, MAGIC as MAC_MAGIC
    from .macdoc import MAC_TO_UNIX, NO_MINMAX

    blocks = []                               # (record, start tick, ticks)
    for r, rec in enumerate(records):
        start = 0
        while start < rec.ticks:
            n = min(BLOCK_TICKS, rec.ticks - start)
            blocks.append((r, start, n))
            start += n
    body = bytearray(MAC_MAGIC + b"\xf0\x00\x01" + bytes(8))
    toc: list[tuple[str, int, int, int]] = []

    def add(typ: str, cid: int, data: bytes) -> int:
        off = len(body)
        body.extend(data)
        toc.append((typ, cid, off, len(data)))
        return off

    def cnt_item(tag: bytes, payload: bytes) -> bytes:
        return tag + struct.pack(">HII", 1, len(payload), 0) + payload

    movies = movies or {}
    cntd = bytearray()
    groups: dict[int, tuple[int, int]] = {}             # record -> (offset, size) of its CntD group
    for r in sorted(movies):
        name = movies[r]
        start = len(cntd)
        if name:
            cntd += cnt_item(b"ExQN", _mac_pstr(name, len(name.encode("mac_roman")) + 1))
        else:
            cntd += cnt_item(b"ExQN", b"\0") + cnt_item(b"ExQI", bytes(4))
        groups[r] = (start, len(cntd) - start)
    if cntd:
        add("CntD", 1, bytes(cntd))
    cnt1 = bytearray()
    for r in range(len(records)):
        ks = [k for k, b in enumerate(blocks) if b[0] == r]
        e = bytearray(44)
        struct.pack_into(">HH", e, 0, ks[0] + 1, ks[-1] + 1)
        if r in groups:
            struct.pack_into(">IIH", e, 16, groups[r][1], groups[r][0], 1)
        struct.pack_into(">H", e, 26, 1)
        cnt1 += e
    add("Cnt1", 1, bytes(cnt1))

    # comment lists, outside the table of contents (reached through Alc2)
    comment_ptr: dict[int, int] = {}
    number = 0
    for k, (r, start, n) in enumerate(blocks):
        rec = records[r]
        items = [(t - start, ch, text, 1) for t, ch, text in rec.comments if start <= t < start + n]
        items += [(t - start, -1, "", 2) for t in rec.marks if start <= t < start + n]
        if not items:
            continue
        comment_ptr[k] = len(body)
        head, texts = bytearray(struct.pack(">H", len(items))), bytearray()
        for tib, ch, text, kind in items:
            raw = text.encode("mac_roman")
            length = 3 + len(raw) if kind == 1 else 0
            if kind == 1:
                number += 1
            head += struct.pack(">IHIHiI", length, tib, 0, number if kind == 1 else 0,
                                -1 if ch < 0 else 1 << ch, kind)
            if kind == 1:
                texts += struct.pack(">IHB", length, 0, len(raw)) + raw
        body.extend(head + texts)

    t0 = records[0].start_unix if records else 0.0
    alc, mtim = bytearray(), bytearray()
    for k, (r, start, n) in enumerate(blocks):
        when = records[r].start_unix
        secs = int(math.floor(when))
        alc += struct.pack(">HHHII", n, r + 1, 14, start, secs + MAC_TO_UNIX) + struct.pack(">d", when - secs)
        alc += struct.pack(">H6xI", 1, comment_ptr.get(k, 0xFFFFFFFF))
        micros = int(round(1e9 + (when - t0) * 1e6 + start * 1e6 / tick_rate))
        mtim += struct.pack(">Qf", micros, 0.0)
    add("Alc2", 1, bytes(alc))
    add("CmtM", 1, bytes(8))

    gains: list[tuple[str, float]] = []
    units_list: list[tuple[str, float, float, bool]] = []
    gain_of, units_of = [], []
    for c in channels:
        g = (c.base_units, c.full_scale)
        if c.decimation and g not in gains:
            gains.append(g)
        gain_of.append(gains.index(g) + 1 if c.decimation else 0)
        if c.decimation and c.units is not None:
            units_list.append(c.units)
            units_of.append(len(units_list))
        else:
            units_of.append(0)
    unta = bytearray()
    for name, slope, offset, on in units_list:
        e = bytearray(130)
        struct.pack_into(">HHH", e, 0, 0x0205, 0, 0x0100 if on else 0)
        e[6:16], e[16:26] = _ext80(offset), _ext80(slope)
        e[26:34] = _mac_pstr(name, 8, nbsp=True)
        cal = ("0.0 V", f"{offset:g}", "1.0 V", f"{slope + offset:g}")
        for i, s in enumerate(cal):
            e[0x52 + 12 * i:0x52 + 12 * (i + 1)] = _mac_pstr(s, 12)
        unta += e
    add("UntA", 1, bytes(unta))
    gain = bytearray()
    for i, (base, fs) in enumerate(gains):
        gain += _mac_pstr(base, 34, nbsp=(base == "V")) + struct.pack(">dddd", fs, fs, 32767 / 32768, -32767 / 32768)
        gain += struct.pack(">HHH", 0, 3, i + 1)
    add("Gain", 1, bytes(gain))
    add("mTim", 1, bytes(mtim))
    add("vers", 1, bytes.fromhex("07008000"))
    add("vers", 2, bytes.fromhex("07208000"))
    add("vers", 100, bytes.fromhex("07208000"))

    for ci, c in enumerate(channels):
        entries = bytearray()
        for k, (r, start, n) in enumerate(blocks):
            if not c.decimation:
                entries += bytes(24)
                continue
            d = c.decimation
            data = np.asarray(records[r].data[ci], dtype=np.int64)
            x = data[-(-start // d): -(-(start + n) // d)]
            b = sum(1 for kk in range(k) if blocks[kk][0] == r)       # block number within the record
            packed = (r, b) not in c.raw_blocks
            blob = encode_mac_block(x, packed)
            off = len(body)
            body.extend(blob)
            if (r, b) in c.no_minmax_blocks:
                mx = mn = NO_MINMAX
            else:
                mx, mn = float(np.max(x)) / 32768.0, float(np.min(x)) / 32768.0
            entries += struct.pack(">IIffHHI", off, len(blob), mx, mn, gain_of[ci], 19 if packed else 16,
                                   units_of[ci] << 16 | (d if d > 1 else 0))
        add("Map4", ci + 1, bytes(entries))

    props = bytearray(b"\x00\x01" + struct.pack(">HIh", KEY_N_CHANNELS, 0, len(channels)))   # int16, document level
    for ci, c in enumerate(channels):
        def prop(key: int, text: str, trailer: bytes = b"") -> bytes:
            raw = text.encode("mac_roman") + trailer
            return b"\x00\x08" + struct.pack(">HI", key, ci + 1) + bytes([len(raw)]) + raw
        props += prop(KEY_TITLE, c.title)
        if c.calculation:
            props += prop(KEY_CALCULATION, c.calculation)
        if c.expression:
            props += prop(KEY_ARITH_EXPRESSION, c.expression, b"\x01")
    add("Mcro", 30001, bytes(props))
    for ci, c in enumerate(channels):
        add("STR ", 1100 + ci, _mac_pstr(c.title, len(c.title.encode("mac_roman")) + 1))

    table = b"".join(typ.encode("latin-1") + struct.pack(">HII", cid, off, ln) for typ, cid, off, ln in toc)
    struct.pack_into(">II", body, 24, len(body), len(table))
    with open(path, "wb") as fh:
        fh.write(bytes(body) + table)
