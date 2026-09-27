"""Writer for *synthetic* ``.adicht`` files.

This exists for the test-suite: it produces files that exercise every code
path of the reader (container, Map stream, packed/unpacked int16 and float32
sub-blocks, comments, units records, channel-setup XML) without any real
recording.  It writes only what :mod:`adichtion` reads back; LabChart itself
may or may not open the result, that is not the goal.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .blocks import (BLOCK_TICKS, FLAG_32BIT, FLAG_BASE, FLAG_PACKED, FLAG_UNPACKED, FLOAT32_PLANES,
                     INT16_PLANES, MAGIC as BLOCKS_MAGIC, REBASE_INDEX, packbits_encode, plane_bytes)
from .container import ENTRY_SIZE, HEADER_SIZE, MAGIC as CONTAINER_MAGIC, NAME_BYTES, TYPE_STORAGE, TYPE_STREAM
from .serialization import TAG


# -- primitives ---------------------------------------------------------------
def prefixed(text: str) -> bytes:
    data = (text + "\x00").encode("utf-16le")
    n = len(data) // 2
    return struct.pack("<ii", n, -n) + data


def tagged(text: str) -> bytes:
    return TAG + prefixed(text)


def pack_planes(symbols: np.ndarray, n_planes: int, msb_first: bool) -> bytes:
    """Inverse of :func:`adichtion.blocks.unpack_planes`."""
    n = len(symbols)
    pb = plane_bytes(n)
    out = bytearray()
    order = range(n_planes - 1, -1, -1) if msb_first else range(n_planes)
    sym = symbols.astype(np.uint64)
    for p in order:
        bits = ((sym >> np.uint64(p)) & np.uint64(1)).astype(np.uint8)
        packed = np.packbits(bits, bitorder="little").tobytes()
        out += packed + bytes(pb - len(packed))
    return bytes(out)


def encode_int16(x: np.ndarray) -> bytes:
    """17-plane sign-magnitude delta buffer of an int16 sequence."""
    x = np.asarray(x, dtype=np.int64)
    d = np.diff(np.concatenate([[0], x]))
    d = (d + 32768) % 65536 - 32768          # int16 wrap-around
    m = 2 * np.abs(d) + (d < 0)
    return pack_planes(m, INT16_PLANES, msb_first=False)


def encode_float32(x: np.ndarray, prev_last: float = 0.0) -> bytes:
    """32-plane buffer of float32 differences with the re-base at sample 31."""
    x = np.asarray(x, dtype=np.float32)
    prev = np.concatenate([[np.float32(prev_last)], x[:-1]])
    d = (x - prev).astype(np.float32)
    if len(x) > REBASE_INDEX:
        # d[31] carries the previous block's last value so that cumsum from 0 is exact from 31 on
        d[REBASE_INDEX] = np.float32(x[REBASE_INDEX] - float(np.sum(d[:REBASE_INDEX].astype(np.float64))))
    return pack_planes(d.view(np.uint32), FLOAT32_PLANES, msb_first=True)


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
    units: Optional[tuple[str, float, float]] = None   # (name, slope, offset) -> a units record
    range_volts: float = 10.0
    unpacked_blocks: Sequence[int] = ()     # (record, block) pairs written unpacked (float only)


@dataclass
class SynthRecord:
    ticks: int
    start_utc: float = 1_700_000_000.0
    utc_offset_s: float = 3600.0
    comments: list[tuple[int, int, str]] = field(default_factory=list)   # (tick, channel or -1, text)
    data: dict[int, np.ndarray] = field(default_factory=dict)            # channel -> samples (ticks long)


def build_map_stream(channels: Sequence[SynthChannel], records: Sequence[SynthRecord], tick_rate: int = 1000) -> bytes:
    out = bytearray(bytes.fromhex("aaaa1555") + struct.pack("<i", 2) + bytes(8))
    name = b"ADInstruments Chart Data\x00"
    out += struct.pack("<H", len(name)) + name + struct.pack("<3h", 3, 7, 3) + bytes(10)
    cid = 1
    for r in records:
        out += struct.pack("<qdd", tick_rate, r.start_utc, -r.utc_offset_s) + bytes(12) + struct.pack("<i", 0)
        out += struct.pack("<q", r.ticks)
        out += tagged("")
        blocks = [min(BLOCK_TICKS, r.ticks - t) for t in range(0, r.ticks, BLOCK_TICKS)]
        out += struct.pack("<ii", len(blocks), -len(blocks))
        t0 = 0
        for bt in blocks:
            cms = [(tk, ch, txt) for tk, ch, txt in r.comments if t0 <= tk < t0 + bt]
            out += struct.pack("<iiihh", 3, bt, 0, 0, len(cms))
            for tk, ch, txt in cms:
                out += struct.pack("<iiihh", 2, 0, cid, ch, tk - t0) + tagged(txt)
                cid += 1
            t0 += bt
    # units records, one per channel in channel order (identity for unconverted channels, as LabChart does)
    for c in channels:
        uname, slope, offset = c.units if c.units else ("", 1.0, 0.0)
        out += tagged("V") + struct.pack("<dd", slope, offset) + struct.pack("<iiii", 1, 1, 0x10000, 0)
        out += tagged(uname) + tagged("1 V" if c.units else "") + tagged("0 V" if c.units else "")
        out += tagged(f"{slope + offset:g}" if c.units else "") + tagged(f"{offset:g}" if c.units else "")
    # channel setup XML
    for i, c in enumerate(channels):
        rng = f"{c.range_volts * 1000:g} mV" if c.range_volts < 1 else f"{c.range_volts:g} V"
        xml = (f'<_ChannelSetup value-code="{i}">{i}\n<PhysicalGainIndex units-format="UnitsGain" value-code="5">{rng}</PhysicalGainIndex>\n'
               f'<SpeedIndex units-format="UnitsSamplingRate" value-code="12">{tick_rate} /s</SpeedIndex>\n<Device>Synthetic PowerLab</Device>\n</_ChannelSetup>')
        out += tagged(xml)
    # channel titles
    out += struct.pack("<iii", -1, len(channels), -len(channels))
    for c in channels:
        out += tagged(c.title)
    return bytes(out)


def build_blocks_stream(channels: Sequence[SynthChannel], records: Sequence[SynthRecord]) -> bytes:
    out = bytearray(BLOCKS_MAGIC)
    for ri, r in enumerate(records):
        blocks = [min(BLOCK_TICKS, r.ticks - t) for t in range(0, r.ticks, BLOCK_TICKS)]
        t0 = 0
        prev_last = [0.0] * len(channels)
        for bi, bt in enumerate(blocks):
            for ci, c in enumerate(channels):
                x = np.asarray(r.data[ci])[t0:t0 + bt]
                is_float = c.dtype == "float32"
                packed = not ((ri, bi) in c.unpacked_blocks)
                out += subblock(x, is_float, packed, prev_last[ci])
                prev_last[ci] = float(x[-1]) if is_float else 0.0
            t0 += bt
    return bytes(out)


def build_container(streams: dict[str, bytes], cluster_size: int = 4096, version: int = 29) -> bytes:
    """Lay out streams contiguously (cluster numbers start at 2), then the root
    table, then the cluster-map pages."""
    entries_per_page = cluster_size // 8
    layout: list[tuple[str, int, int, int]] = []   # name, first, size, last
    cluster = 2
    body = bytearray()
    for name, data in streams.items():
        n = max(1, math.ceil(len(data) / cluster_size))
        layout.append((name, cluster, len(data), cluster + n - 1))
        body += data + bytes(n * cluster_size - len(data))
        cluster += n
    # root table
    table = struct.pack("<ii", len(layout), -len(layout))
    for name, first, size, last in layout:
        e = bytearray(ENTRY_SIZE)
        nm = name.encode("utf-16le")
        e[:len(nm)] = nm
        struct.pack_into("<qqq", e, 0x48, first, size, last)
        struct.pack_into("<ii", e, 0x88, 0, TYPE_STREAM)
        table += bytes(e)
    root_cluster = cluster
    body += table + bytes(cluster_size - len(table))
    cluster += 1
    map_first = cluster
    total_data_clusters = cluster            # clusters 0..cluster-1 exist logically
    pages = math.ceil((total_data_clusters + 1) / entries_per_page)
    capacity = pages * entries_per_page
    # cluster map
    cmap = bytearray(pages * cluster_size)
    def link(c: int, nxt: int, prev: int) -> None:
        struct.pack_into("<ii", cmap, c * 8, nxt, prev)
    for name, first, size, last in layout:
        for c in range(first, last + 1):
            link(c, c + 1 if c < last else 0, c - 1 if c > first else 0)
    link(root_cluster, 0, 0)
    body += cmap
    # header
    hdr = bytearray(HEADER_SIZE)
    hdr[:len(CONTAINER_MAGIC)] = CONTAINER_MAGIC
    struct.pack_into("<7i", hdr, 0x80, version, HEADER_SIZE, 131072, 0x7FFFFFFF, 0, 0, int(math.log2(cluster_size)))
    struct.pack_into("<qqq", hdr, 0x9C, capacity, -capacity, map_first)
    root = bytearray(ENTRY_SIZE)
    nm = "ADIRootStorage".encode("utf-16le")
    root[:len(nm)] = nm
    struct.pack_into("<qqq", root, 0x48, root_cluster, len(table), root_cluster)
    struct.pack_into("<ii", root, 0x88, 0, TYPE_STORAGE)
    hdr[0xB8:0xB8 + ENTRY_SIZE] = root
    return bytes(hdr) + bytes(body)


def write_synthetic(path, channels: Sequence[SynthChannel], records: Sequence[SynthRecord], tick_rate: int = 1000,
                    cluster_size: int = 4096) -> None:
    streams = {
        "Version": tagged("adichtion synthetic"),
        "Map": build_map_stream(channels, records, tick_rate),
        "Blocks_0": build_blocks_stream(channels, records),
    }
    with open(path, "wb") as fh:
        fh.write(build_container(streams, cluster_size))
