"""High-level, UI-oriented access to ``.adicht`` files.

::

    from adichtion import AdichtFile

    with AdichtFile("recording.adicht") as f:
        for rec in f.records:
            print(rec.index, rec.start_local, rec.duration_s, [c.text for c in rec.comments])
        rec = f.records[0]
        t = rec.time(0, 4000)                       # seconds from record start
        pmo = rec.read(1, 0, 4000)                  # channel 2 in its units (cmH2O) when the file says so
        raw = rec.read_raw(1, 0, 4000)              # int16 counts (or float32 for calculated channels)
        t0, t1, lo, hi = rec.block_envelope(1)      # per-block min/max straight from the Map stream

Design notes for the UI layer:

* opening a file parses only metadata (a few ms even for 300 MB files); sample
  data are decoded per 16000-tick block on demand and kept in an LRU cache;
* random access is O(1) blocks: every block is self-contained;
* :meth:`Record.block_envelope` (no decoding) and :meth:`Record.overview`
  return min/max envelopes suitable for drawing a whole record at screen
  resolution;
* units are per record: each record carries (or references) a units object
  per channel, so :meth:`Record.read` needs no document-wide guess.
"""
from __future__ import annotations

import os
import struct
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

from .blocks import (BLOCK_TICKS, FLAG_32BIT, FLAG_PACKED, FLAG_UNPACKED, HEADER_SIZE, BlocksFormatError,
                     SubBlock, SubBlockIndex, decode_subblock, iter_subblocks)
from .chartdata import parse_chart_data
from .container import AdichtContainer, AdichtFormatError
from .decimate import minmax_envelope
from .model import ChannelInfo, ChannelRecordInfo, ChartData, Comment, RecordInfo, UnitsConversion, VideoClip
from .video import movies_folder, read_videos, video_path

__all__ = ["AdichtFile", "Record", "Selection"]


@dataclass(frozen=True)
class Selection:
    """A half-open tick range ``[start, stop)`` within a record."""

    record: int
    start: int
    stop: int

    @property
    def ticks(self) -> int:
        return self.stop - self.start


class _BlockCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._d: "OrderedDict[int, np.ndarray]" = OrderedDict()

    def get(self, key: int) -> Optional[np.ndarray]:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key: int, value: np.ndarray) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.capacity:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


def _subblocks_from_map(blocks: bytes, chart: ChartData) -> list[SubBlock]:
    """Build the sub-block list from the Map stream's block index (one header
    read per sub-block, no walk of the chain).  Order: record, block, channel."""
    out: list[SubBlock] = []
    view = memoryview(blocks)
    for rec in chart.records:
        for b in range(len(rec.blocks)):
            for ch in rec.channels:
                off = ch.blocks[b].offset
                if off + HEADER_SIZE > len(view):
                    raise BlocksFormatError(f"block index points outside the Blocks stream ({off})")
                n, _check, negn, flags = struct.unpack_from("<iiii", view, off)
                if negn != -n or n < 12:
                    raise BlocksFormatError(f"block index of record {rec.index} channel {ch.channel} block {b} "
                                            f"points at offset {off}, which holds no sub-block header")
                body_offset = off + HEADER_SIZE
                ssize = 4 if flags & FLAG_32BIT else 2
                if flags & FLAG_PACKED and not flags & FLAG_UNPACKED:
                    raw, = struct.unpack_from("<i", view, body_offset)
                    n_samples = raw // ssize
                else:
                    n_samples = (n - 12) // ssize
                out.append(SubBlock(index=len(out), offset=off, body_offset=body_offset, body_size=n - 12,
                                    flags=flags, n_samples=n_samples))
    return out


class AdichtFile:
    """A LabChart ``.adicht`` file opened for reading."""

    def __init__(self, path: str | os.PathLike, cache_blocks: int = 256):
        self.path = os.fspath(path)
        self._container = AdichtContainer(self.path)
        try:
            self._map_entry = self._container.find_stream("Map")
            self._blocks_entry = self._container.find_stream("Blocks_0")
            if self._map_entry is None or self._blocks_entry is None:
                raise AdichtFormatError("file has no Map / Blocks_0 stream")
            self.chart: ChartData = parse_chart_data(self._container.read_stream(self._map_entry))
            self._blocks: bytes = self._container.read_stream(self._blocks_entry)
            if self.chart.parse_mode == "sequential":
                subblocks = _subblocks_from_map(self._blocks, self.chart)
            else:
                subblocks = list(iter_subblocks(self._blocks))
            dtypes = [c.dtype for c in self.chart.records[0].channels] if self.chart.records else None
            self.index = SubBlockIndex(subblocks, [[b.ticks for b in r.blocks] for r in self.chart.records],
                                       dtypes or None)
            if self.chart.parse_mode == "sequential" and self.index.n_channels != self.chart.n_channels:
                raise AdichtFormatError(f"{self.index.n_channels} channels of sample data for "
                                        f"{self.chart.n_channels} channel entries")
            self.videos: list[VideoClip] = read_videos(self._container)
        except Exception:
            self._container.close()
            raise
        self._cache = _BlockCache(cache_blocks)
        self._range_override: dict[int, float] = {}
        self.records: list[Record] = [Record(self, r) for r in self.chart.records]

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._cache.clear()
        self._container.close()

    def __enter__(self) -> "AdichtFile":
        return self
    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"AdichtFile({self.path!r}, records={len(self.records)}, channels={self.n_channels})"

    # -- static description --------------------------------------------------
    @property
    def format_version(self) -> int:
        return self._container.version

    @property
    def parse_mode(self) -> str:
        """``"sequential"`` when the Map stream followed the known grammar, ``"scan"`` otherwise."""
        return self.chart.parse_mode

    @property
    def n_channels(self) -> int:
        return self.index.n_channels

    @property
    def channel_titles(self) -> list[str]:
        titles = list(self.chart.titles[: self.n_channels])
        while len(titles) < self.n_channels:
            titles.append(f"Channel {len(titles) + 1}")
        return titles

    @property
    def comments(self) -> list[Comment]:
        return self.chart.comments

    def set_range_volts(self, channel: int, range_volts: float) -> None:
        """Override the input range of an ADC channel (only needed for files whose
        Map stream could not be parsed in full and carries no ``<_ChannelSetup>`` XML)."""
        self._range_override[channel] = range_volts

    def channel_record(self, record: int, channel: int) -> Optional[ChannelRecordInfo]:
        """The Map stream's per-record description of a channel (None for a legacy parse)."""
        return self.chart.channel_record(record, channel)

    def units_for(self, channel: int, record: int = 0) -> Optional[UnitsConversion]:
        """The units object of a channel in a record.  With a legacy parse the
        units records are associated 1:1 in channel order when their number
        matches the channel count (best effort)."""
        cr = self.channel_record(record, channel)
        if cr is not None:
            return cr.units
        recs = self.chart.units
        if len(recs) == self.n_channels:
            return recs[channel]
        return None

    def range_volts(self, channel: int, record: int = 0) -> Optional[float]:
        """Input range of an ADC channel: from the LSB stored in its units object,
        else from the ``<_ChannelSetup>`` XML, else an override, else None."""
        if channel in self._range_override:
            return self._range_override[channel]
        u = self.units_for(channel, record)
        if u is not None and u.has_lsb and self.index.channel_dtype(channel) == "int16":
            return u.lsb * 32768.0 / 1.024
        setup = self.chart.setups.get(channel)
        return setup.range_volts if setup else None

    def channel_info(self, channel: int) -> ChannelInfo:
        dtype = self.index.channel_dtype(channel)
        spt = self.index.samples_per_tick(0, channel) if self.records else 1.0
        return ChannelInfo(index=channel, title=self.channel_titles[channel], dtype=dtype,
                           units=self.units_for(channel), range_volts=self.range_volts(channel),
                           samples_per_tick=spt)

    @property
    def channels(self) -> list[ChannelInfo]:
        return [self.channel_info(c) for c in range(self.n_channels)]

    # -- videos ----------------------------------------------------------------
    @property
    def movies_folder(self) -> str:
        """Where LabChart keeps this document's videos (``<name> movies``); may not exist."""
        return movies_folder(self.path)

    def video_for(self, record: int) -> Optional[VideoClip]:
        for v in self.videos:
            if v.record_index == record:
                return v
        return None

    # -- decoding ------------------------------------------------------------
    def _body(self, sb: SubBlock) -> memoryview:
        return memoryview(self._blocks)[sb.body_offset: sb.body_offset + sb.body_size]

    def decode_block(self, record: int, block: int, channel: int) -> np.ndarray:
        """Decoded samples of one block: int32 counts or float32 values (cached).
        Every block is self-contained, so this is O(1) blocks."""
        sb = self.index.get(record, block, channel)
        hit = self._cache.get(sb.index)
        if hit is not None:
            return hit
        data = decode_subblock(self._body(sb), sb)
        self._cache.put(sb.index, data)
        return data

    # -- consistency checks (for tests and the "verify" command) ----------------------
    def walk_subblocks(self) -> list[SubBlock]:
        """Sub-blocks found by walking the Blocks_0 chain (independent of the Map index)."""
        return list(iter_subblocks(self._blocks))


class Record:
    """One LabChart block ("record" in ADI SDK terms) of an :class:`AdichtFile`."""

    def __init__(self, file: AdichtFile, info: RecordInfo):
        self.file = file
        self.info = info

    # -- description ---------------------------------------------------------
    @property
    def index(self) -> int:
        return self.info.index

    @property
    def tick_rate_hz(self) -> float:
        return self.info.tick_rate_hz

    @property
    def ticks(self) -> int:
        return self.info.ticks

    @property
    def duration_s(self) -> float:
        return self.info.duration_s

    @property
    def start_utc(self) -> datetime:
        return self.info.start_datetime_utc

    @property
    def start_local(self) -> datetime:
        return self.info.start_datetime_local

    @property
    def comments(self) -> list[Comment]:
        return self.info.comments

    @property
    def block_ticks(self) -> list[int]:
        return [b.ticks for b in self.info.blocks]

    @property
    def video(self) -> Optional[VideoClip]:
        """The video clip recorded with this record (None when the document has none)."""
        return self.file.video_for(self.index)

    @property
    def video_file(self) -> Optional[str]:
        """Path of the clip's file if it exists in the ``<name> movies`` folder."""
        v = self.video
        return video_path(self.file.path, v) if v else None

    def __repr__(self) -> str:
        return f"Record({self.index}, {self.start_local:%Y-%m-%d %H:%M:%S}, {self.duration_s:.2f} s, {len(self.comments)} comments)"

    # -- sample geometry -----------------------------------------------------
    def samples_per_tick(self, channel: int) -> float:
        return self.file.index.samples_per_tick(self.index, channel)

    def sample_rate_hz(self, channel: int) -> float:
        return self.tick_rate_hz * self.samples_per_tick(channel)

    def n_samples(self, channel: int) -> int:
        return int(round(self.ticks * self.samples_per_tick(channel)))

    def tick_to_sample(self, tick: int, channel: int) -> int:
        return int(round(tick * self.samples_per_tick(channel)))

    def time(self, start: int = 0, stop: Optional[int] = None, channel: int = 0) -> np.ndarray:
        """Time (s from record start) of the samples of ``channel`` in tick range ``[start, stop)``."""
        s0, s1 = self._sample_range(start, stop, channel)
        return np.arange(s0, s1, dtype=np.float64) / self.sample_rate_hz(channel)

    def _sample_range(self, start: int, stop: Optional[int], channel: int) -> tuple[int, int]:
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        return self.tick_to_sample(start, channel), self.tick_to_sample(stop, channel)

    # -- units ------------------------------------------------------------------
    def units(self, channel: int) -> Optional[UnitsConversion]:
        """This record's units object for ``channel`` (own or referenced)."""
        return self.file.units_for(channel, self.index)

    def value_label(self, channel: int) -> str:
        """Unit label matching :meth:`read`: the units name, ``"converted"`` (name
        not stored), ``"V"``, ``"counts"`` or ``""`` (calculated channel of unknown scale)."""
        u = self.units(channel)
        dtype = self.file.index.channel_dtype(channel)
        if u is not None and u.enabled:
            return u.units or "converted"
        if (u is not None and u.has_lsb) or (dtype == "int16" and self.file.range_volts(channel, self.index) is not None):
            return "V"
        return "counts" if dtype == "int16" else ""

    # -- data ------------------------------------------------------------------
    def read_raw(self, channel: int, start: int = 0, stop: Optional[int] = None) -> np.ndarray:
        """Stored values in tick range ``[start, stop)``: int32 counts for ADC
        channels, float32 for calculated channels."""
        s0, s1 = self._sample_range(start, stop, channel)
        spt = self.samples_per_tick(channel)
        out: list[np.ndarray] = []
        tick0 = 0
        for b, bt in enumerate(self.block_ticks):
            b0 = int(round(tick0 * spt))
            b1 = int(round((tick0 + bt) * spt))
            tick0 += bt
            if b1 <= s0:
                continue
            if b0 >= s1:
                break
            data = self.file.decode_block(self.index, b, channel)
            out.append(data[max(s0, b0) - b0: min(s1, b1) - b0])
        if not out:
            return np.zeros(0, dtype=np.float32 if self.file.index.channel_dtype(channel) == "float32" else np.int32)
        return np.concatenate(out)

    def read(self, channel: int, start: int = 0, stop: Optional[int] = None, units: bool = True) -> np.ndarray:
        """Samples as float64.  With ``units=True`` the stored values are scaled
        by the channel's LSB (counts -> volts, calculated values -> their base
        scale) and converted with the record's units conversion when it is
        switched on; see :meth:`value_label` for what comes out."""
        raw = self.read_raw(channel, start, stop)
        if not units:
            return raw.astype(np.float64)
        return self.convert(channel, raw)

    def convert(self, channel: int, stored: np.ndarray) -> np.ndarray:
        """Stored values -> what :meth:`read` returns (also used for block min/max)."""
        x = np.asarray(stored, dtype=np.float64)
        u = self.units(channel)
        if u is not None and u.has_lsb:
            return u.scale(x)
        if self.file.index.channel_dtype(channel) == "int16":
            info = self.file.channel_info(channel)
            vpc = info.volts_per_count
            if vpc is None:
                return x
            volts = x * vpc
            return u.convert(volts) if u is not None and u.enabled else volts
        return x

    file_convert = convert   # backwards compatible name

    # -- selections ----------------------------------------------------------
    def selection(self, start_s: float, stop_s: float) -> Selection:
        r = self.tick_rate_hz
        return Selection(self.index, max(0, int(round(start_s * r))), min(self.ticks, int(round(stop_s * r))))

    def selection_between(self, start_text: str, stop_text: str) -> Optional[Selection]:
        """Tick range from the first comment whose text starts with ``start_text``
        to the next comment whose text starts with ``stop_text``."""
        a = next((c for c in self.comments if c.text.startswith(start_text)), None)
        if a is None:
            return None
        b = next((c for c in self.comments if c.tick > a.tick and c.text.startswith(stop_text)), None)
        return Selection(self.index, a.tick, b.tick if b else self.ticks)

    # -- overview for plotting -----------------------------------------------
    def block_envelope(self, channel: int, units: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(t_start, t_end, min, max)`` per 16000-tick block, straight from the
        Map stream's block index (nothing is decoded).  Empty arrays for a
        legacy parse."""
        cr = self.file.channel_record(self.index, channel)
        if cr is None:
            e = np.zeros(0)
            return e, e, e, e
        ticks = np.array([b.ticks for b in cr.blocks], dtype=np.float64)
        t1 = np.cumsum(ticks) / self.tick_rate_hz
        t0 = t1 - ticks / self.tick_rate_hz
        mins = np.array([b.min for b in cr.blocks], dtype=np.float64)
        maxs = np.array([b.max for b in cr.blocks], dtype=np.float64)
        if units:
            mins, maxs = self.convert(channel, mins), self.convert(channel, maxs)
            if len(mins) and mins[0] > maxs[0]:     # a negative slope swaps them
                mins, maxs = maxs, mins
        return t0, t1, mins, maxs

    def overview(self, channel: int, n_points: int = 2000, start: int = 0, stop: Optional[int] = None,
                 units: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(time, min, max)`` envelope of ``channel`` over ``[start, stop)`` in
        ``n_points`` buckets, for drawing a long record at screen resolution.
        Buckets of a whole block or more are served from the Map stream's
        block index without decoding."""
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        t0 = start / self.tick_rate_hz
        t1 = stop / self.tick_rate_hz
        cr = self.file.channel_record(self.index, channel)
        span = stop - start
        if cr is not None and n_points > 0 and span >= n_points * BLOCK_TICKS:
            b0, b1, mins, maxs = self.block_envelope(channel, units)
            edges = np.linspace(t0, t1, n_points + 1)
            lo = np.full(n_points, np.nan)
            hi = np.full(n_points, np.nan)
            width = (t1 - t0) / n_points
            for s, e, m, x in zip(b0, b1, mins, maxs):
                if e <= t0 or s >= t1:
                    continue
                k0 = int(max(0, (s - t0) // width))
                k1 = int(min(n_points - 1, (e - t0 - 1e-9) // width))
                for k in range(k0, k1 + 1):           # a block feeds every bucket it overlaps
                    lo[k] = m if np.isnan(lo[k]) else min(lo[k], m)
                    hi[k] = x if np.isnan(hi[k]) else max(hi[k], x)
            t = (edges[:-1] + edges[1:]) / 2
            return t, lo, hi
        x = self.read(channel, start, stop, units=units)
        mins, maxs = minmax_envelope(x, n_points)
        t = np.linspace(t0, t1, len(mins), endpoint=False) + (t1 - t0) / max(len(mins), 1) / 2
        return t, mins, maxs
