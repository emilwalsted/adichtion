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
