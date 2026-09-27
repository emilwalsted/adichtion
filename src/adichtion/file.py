"""High-level, UI-oriented access to ``.adicht`` files.

::

    from adichtion import AdichtFile

    with AdichtFile("recording.adicht") as f:
        for rec in f.records:
            print(rec.index, rec.start_local, rec.duration_s, [c.text for c in rec.comments])
        rec = f.records[0]
        t = rec.time(0, 4000)                       # seconds from record start
        pmo = rec.read(1, 0, 4000)                  # channel 2, converted to units when possible
        raw = rec.read(1, 0, 4000, units=False)     # int16 counts (or float32 for calculated channels)

Design notes for the UI layer:

* opening a file parses only metadata (a few ms even for 200 MB files); sample
  data are decoded per 16000-tick block on demand and kept in an LRU cache;
* random access is O(1) blocks: a float32 block needs the last value of the
  previous block, which is the plain sum of that block's stored deltas;
* :meth:`Record.overview` returns a min/max envelope suitable for drawing a
  whole record at screen resolution.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import numpy as np

from .blocks import BLOCK_TICKS, SubBlock, SubBlockIndex, decode_float32, decode_float32_deltas, \
    decode_subblock, iter_subblocks, packbits_decode
from .chartdata import parse_chart_data
from .container import AdichtContainer, AdichtFormatError
from .decimate import minmax_envelope
from .model import ChannelInfo, ChartData, Comment, RecordInfo, UnitsConversion

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


class AdichtFile:
    """A LabChart 8 ``.adicht`` file opened for reading."""

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
            subblocks = list(iter_subblocks(self._blocks))
            self.index = SubBlockIndex(subblocks, [[b.ticks for b in r.blocks] for r in self.chart.records])
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
        """Override the input range of a channel (needed for format v26 files,
        whose Map stream carries no ``<_ChannelSetup>`` XML)."""
        self._range_override[channel] = range_volts

    def units_for(self, channel: int) -> Optional[UnitsConversion]:
        """Best-effort association of a units-conversion record with a channel:
        the records are stored in channel order, so a 1:1 mapping is used when
        their number matches the channel count; otherwise ``None``."""
        recs = [u for u in self.chart.units]
        if len(recs) == self.n_channels:
            u = recs[channel]
            return None if u.is_identity else u
        return None

    def range_volts(self, channel: int) -> Optional[float]:
        if channel in self._range_override:
            return self._range_override[channel]
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

    # -- decoding ------------------------------------------------------------
    def _body(self, sb: SubBlock) -> memoryview:
        return memoryview(self._blocks)[sb.body_offset: sb.body_offset + sb.body_size]

    def _block_last_value(self, sb: SubBlock) -> float:
        """Last sample of a float32 sub-block without decoding its predecessor:
        the stored deltas sum to the last value (the re-base at sample 31 makes
        the chain independent of the previous block from there on)."""
        body = self._body(sb)
        if not sb.is_packed:
            return float(np.frombuffer(bytes(body), dtype="<f4", count=sb.n_samples)[-1])
        deltas = decode_float32_deltas(packbits_decode(bytes(body)[4:]), sb.n_samples)
        return float(np.sum(deltas.astype(np.float64)))

    def decode_block(self, record: int, block: int, channel: int) -> np.ndarray:
        """Decoded samples of one block: int32 counts or float32 values (cached)."""
        sb = self.index.get(record, block, channel)
        hit = self._cache.get(sb.index)
        if hit is not None:
            return hit
        prev_last = 0.0
        if sb.is_float and block > 0:
            prev_last = self._block_last_value(self.index.get(record, block - 1, channel))
        data = decode_subblock(self._body(sb), sb, prev_last)
        self._cache.put(sb.index, data)
        return data


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
        """Samples as float64.  With ``units=True`` ADC counts are converted to
        volts (when the input range is known) and further to the channel's
        units (when a units-conversion record is associated); calculated
        channels are returned as stored."""
        raw = self.read_raw(channel, start, stop)
        if not units or raw.dtype != np.int32:
            return raw.astype(np.float64)
        return self.file_convert(channel, raw)

    def file_convert(self, channel: int, counts: np.ndarray) -> np.ndarray:
        x = counts.astype(np.float64)
        info = self.file.channel_info(channel)
        vpc = info.volts_per_count
        if vpc is None:
            return x
        volts = x * vpc
        return info.units.convert(volts) if info.units else volts

    def value_label(self, channel: int) -> str:
        """Unit label matching :meth:`read`: units, ``"V"``, or ``"counts"``."""
        info = self.file.channel_info(channel)
        if info.dtype == "float32":
            return info.units.units if info.units and info.units.units else ""
        if info.volts_per_count is None:
            return "counts"
        if info.units is None:
            return "V"
        return info.units.units or "converted"   # converted, but the units name is not stored in this file

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
    def overview(self, channel: int, n_points: int = 2000, start: int = 0, stop: Optional[int] = None,
                 units: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(time, min, max)`` envelope of ``channel`` over ``[start, stop)`` in
        ``n_points`` buckets, for drawing a long record at screen resolution."""
        x = self.read(channel, start, stop, units=units)
        stop = self.ticks if stop is None else min(stop, self.ticks)
        t0 = max(0, start) / self.tick_rate_hz
        t1 = stop / self.tick_rate_hz
        mins, maxs = minmax_envelope(x, n_points)
        t = np.linspace(t0, t1, len(mins), endpoint=False) + (t1 - t0) / max(len(mins), 1) / 2
        return t, mins, maxs
