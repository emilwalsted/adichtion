"""High-level, UI-oriented access to ``.adicht`` files and LabChart for Mac documents.

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
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

from .blocks import (BLOCK_TICKS, FLAG_32BIT, FLAG_PACKED, FLAG_UNPACKED, HEADER_SIZE, BlocksFormatError,
                     SubBlock, SubBlockIndex, decode_subblock, iter_subblocks)
from .chartdata import parse_chart_data
from .container import AdichtContainer, AdichtFormatError
from .decimate import minmax_envelope, upsample_like_labchart
from .macdoc import MacDocument, decode_mac_block, is_mac_document, read_mac_document
from .model import ChannelInfo, ChannelRecordInfo, ChartData, Comment, RecordInfo, UnitsConversion, VideoClip
from .qtmovie import MovieError, read_movie_timing
from .video import movies_folder, read_videos, video_candidate_path

__all__ = ["AdichtFile", "ChannelBlockTable", "Record", "Selection"]


@dataclass(frozen=True)
class Selection:
    """A half-open tick range ``[start, stop)`` within a record."""

    record: int
    start: int
    stop: int

    @property
    def ticks(self) -> int:
        return self.stop - self.start


# -- decoded-block cache ---------------------------------------------------------------------
CACHE_MIN_BYTES = 256 * 1024 * 1024         # the cache never gets less than this ...
CACHE_MAX_BYTES = 1024 * 1024 * 1024        # ... nor more than this
CACHE_RAM_FRACTION = 0.25                   # ... and in between a quarter of the memory that is free at open

#: A ``.adicht`` file of at least this many bytes has its sample stream served as a window on the file's
#: memory mapping instead of a copy (``AdichtFile(zero_copy=None)``, the default).  Smaller files are
#: copied: that costs next to nothing, and a copy cannot fault if the file is truncated after opening,
#: whereas a mapping can.  (The container maps the file whatever the size and keeps it open until
#: ``close()``, so the Windows lock on rename/delete is not affected by this choice; docs/beslutninger.md
#: 2026-10-04 (7).)  ``ADICHTION_ZERO_COPY`` = ``0``/``1`` forces
#: the choice for every file, ``auto`` (or unset) means this size rule; the ``zero_copy`` argument beats both.
ZERO_COPY_MIN_BYTES = 64 * 1024 * 1024


def _zero_copy_wanted(size: int, requested: Optional[bool]) -> bool:
    if requested is not None:
        return bool(requested)
    env = os.environ.get("ADICHTION_ZERO_COPY", "").strip().lower()
    if env in ("1", "on", "true", "yes"):
        return True
    if env in ("0", "off", "false", "no"):
        return False
    return size >= ZERO_COPY_MIN_BYTES


#: Buckets of the min/max summary kept for every decoded block (a block of 16000 ticks then
#: gets one bucket per 500 ticks).  A detail pass whose pixel buckets are at least that wide
#: is served from the summaries instead of from the samples (``Record.overview``).
SUMMARY_BUCKETS = 32


def _available_memory() -> Optional[int]:
    """Bytes of RAM that are free right now, or None when the platform will not say."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:                  # Linux
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        if os.name == "nt":                                                  # Windows
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_ext", ctypes.c_ulonglong)]
            st = _MemStatus()
            st.length = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):   # type: ignore[attr-defined]
                return int(st.avail_phys)
            return None
        # macOS and the rest: no portable "free" figure, so half of the installed memory stands in
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2
    except (OSError, ValueError, AttributeError, ImportError):
        return None


def default_cache_bytes() -> int:
    """Size of the decoded-block cache of a newly opened file: ``CACHE_RAM_FRACTION`` of the
    memory that is free, clamped to ``[CACHE_MIN_BYTES, CACHE_MAX_BYTES]``."""
    free = _available_memory()
    if free is None:
        return CACHE_MIN_BYTES
    return int(min(CACHE_MAX_BYTES, max(CACHE_MIN_BYTES, free * CACHE_RAM_FRACTION)))


class _BlockCache:
    """LRU cache of decoded blocks, limited by memory (``max_bytes``) and, optionally, by a
    number of blocks (``capacity``).  Guarded by a lock so a UI can decode in a worker thread
    while its GUI thread reads values under the cursor.

    A block of an ADC channel is decoded to int32 but its counts are 16-bit; it is kept as int16
    (half the memory) when every value fits, and handed out as a fresh int32 array again, so
    callers see exactly what the decoder returned."""

    def __init__(self, max_bytes: Optional[int] = None, capacity: Optional[int] = None):
        self.max_bytes = default_cache_bytes() if max_bytes is None else int(max_bytes)
        self.capacity = capacity
        self.nbytes = 0                 # memory held by the cached arrays
        self._d: "OrderedDict[int, np.ndarray]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0                   # lookups answered from the cache ...
        self.misses = 0                 # ... and the ones that had to decode a block (counted under the lock)

    def get(self, key: int) -> Optional[np.ndarray]:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
                self.hits += 1
            else:
                self.misses += 1
        if v is not None and v.dtype == np.int16:
            return v.astype(np.int32)
        return v

    @staticmethod
    def _compact(value: np.ndarray) -> np.ndarray:
        if value.dtype == np.int32 and value.size and int(value.min()) >= -32768 and int(value.max()) <= 32767:
            return value.astype(np.int16)
        return value

    def put(self, key: int, value: np.ndarray) -> None:
        value = self._compact(value)
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self.nbytes -= old.nbytes
            self._d[key] = value
            self.nbytes += value.nbytes
            while len(self._d) > 1 and (self.nbytes > self.max_bytes
                                        or (self.capacity is not None and len(self._d) > self.capacity)):
                _k, evicted = self._d.popitem(last=False)
                self.nbytes -= evicted.nbytes

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.nbytes = 0


class _ChannelSummaries:
    """Min/max summary of every decoded block of one channel of one record, in the stored
    (raw) values.  ``bounds`` are the sample numbers at which the blocks start (and the end of the
    last); block ``b`` holds ``count[b]`` buckets of ``bounds[b+1]-bounds[b]`` samples divided as
    evenly as ``minmax_envelope`` divides them.  A block that was never decoded has ``have[b]`` False."""

    def __init__(self, bounds: np.ndarray):
        self.bounds = bounds
        nb = len(bounds) - 1
        self.have = np.zeros(nb, dtype=bool)
        self.count = np.zeros(nb, dtype=np.int32)          # buckets of the block (0: it holds no samples)
        self.n = np.zeros(nb, dtype=np.int64)              # samples the block held when it was decoded
        # float64 holds int32 counts and float32 values exactly, whatever mix of block types a channel has
        self.mins = np.zeros((nb, SUMMARY_BUCKETS), dtype=np.float64)
        self.maxs = np.zeros((nb, SUMMARY_BUCKETS), dtype=np.float64)
        self._lock = threading.Lock()

    def put(self, block: int, data: np.ndarray) -> bool:
        """Remember the summary of ``block``; True when it is new (False: another thread got there first)."""
        n = len(data)
        with self._lock:                   # two threads decoding the same block write the same numbers
            if self.have[block]:
                return False
            if n:
                k = min(SUMMARY_BUCKETS, n)
                edges = (np.arange(k, dtype=np.int64) * n) // k
                # fmin/fmax ignore NaN (a bucket of only NaN stays NaN), as the envelope spreading does
                self.mins[block, :k] = np.fmin.reduceat(data, edges)
                self.maxs[block, :k] = np.fmax.reduceat(data, edges)
                self.count[block] = k
                self.n[block] = n
            self.have[block] = True        # last: a reader that sees it also sees the numbers
        return True


def _subblocks_from_map(blocks: bytes | memoryview, chart: ChartData) -> list[SubBlock]:
    """Build the sub-block list from the Map stream's block index (one header
    read per sub-block, no walk of the chain).  Order: record, block, channel."""
    out: list[SubBlock] = []
    view = memoryview(blocks)
    try:        # release the view even when the index is bad: it may be a window on the file's mapping
        _fill_subblocks_from_map(view, chart, out)
    finally:
        view.release()
    return out


def _fill_subblocks_from_map(view: memoryview, chart: ChartData, out: list[SubBlock]) -> None:
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


@dataclass(frozen=True)
class ChannelBlockTable:
    """The block index of one channel over every record, as flat arrays (see
    :meth:`AdichtFile.block_table`).  Block ``i`` of record ``r`` lives at ``first[r] + i``;
    ``tick0``/``tick1`` are ticks within the record, ``mn``/``mx`` the stored min/max in the
    units :meth:`Record.read` returns (a negative slope has swapped them), NaN where the
    index holds none.  A record the channel has no data in has no blocks
    (``first[r] == first[r + 1]``)."""

    record: np.ndarray      # int64, record index of each block
    tick0: np.ndarray       # float64, first tick of the block within its record
    tick1: np.ndarray       # float64, end tick (exclusive)
    mn: np.ndarray
    mx: np.ndarray
    first: np.ndarray       # int64, len n_records + 1
    complete: bool          # no block lacks its min/max
    generation: int         # the file's envelope generation it was built at


class AdichtFile:
    """A LabChart ``.adicht`` file, or a LabChart for Mac document, opened for reading.

    Both give the same API.  :attr:`kind` says which it is; a Mac document
    (``docs/MAC_FORMAT.md``) does not store its calculated channels, which then
    have no data (:meth:`has_data` False, dtype ``"none"``) and a stored
    definition (:meth:`channel_definition`)."""

    def __init__(self, path: str | os.PathLike, cache_blocks: Optional[int] = None,
                 cache_bytes: Optional[int] = None, zero_copy: Optional[bool] = None):
        self.path = os.fspath(path)
        self.zero_copy = False          # True when the sample stream is a window on the file's mapping
        self.mac: Optional[MacDocument] = None
        self._container: Optional[AdichtContainer] = None
        self._envelopes: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self._envelope_gen = 0          # bumped whenever a filled min/max is remembered or forgotten
        self._tables: dict[int, ChannelBlockTable] = {}
        if is_mac_document(self.path):
            self.kind = "mac"
            with open(self.path, "rb") as fh:
                self._blocks = fh.read()
            self.mac = read_mac_document(self.path, self._blocks)
            self.chart = self.mac.chart
            self.index = self.mac.index
            self.videos: list[VideoClip] = self._mac_videos()
        else:
            self.kind = "adicht"
            self._open_adicht(zero_copy)
        self._cache = _BlockCache(cache_bytes, cache_blocks)
        self._tls = threading.local()   # per thread: how many blocks it has asked for (see :meth:`thread_block_requests`)
        self._summaries: dict[tuple[int, int], _ChannelSummaries] = {}
        self._summaries_lock = threading.Lock()
        self._n_summarised = 0          # blocks that have a summary, all channels and records (kept in step with the stores)
        self._range_override: dict[int, float] = {}
        self.records: list[Record] = [Record(self, r) for r in self.chart.records]

    def _open_adicht(self, zero_copy: Optional[bool] = None) -> None:
        self._container = AdichtContainer(self.path)
        try:
            self._map_entry = self._container.find_stream("Map")
            self._blocks_entry = self._container.find_stream("Blocks_0")
            if self._map_entry is None or self._blocks_entry is None:
                raise AdichtFormatError("file has no Map / Blocks_0 stream")
            self.chart: ChartData = parse_chart_data(self._container.read_stream(self._map_entry))
            view = (self._container.stream_view(self._blocks_entry)
                    if _zero_copy_wanted(len(self._container.data), zero_copy) else None)
            self.zero_copy = view is not None       # None: not wanted, or no contiguous run: copy
            self._blocks: bytes | memoryview = view if view is not None else self._container.read_stream(self._blocks_entry)
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
            self.videos = read_videos(self._container)
        except Exception:
            self._blocks = b""              # drop our own window first; the container releases its views
            self._container.close()
            raise

    # -- lifecycle -----------------------------------------------------------
    def cache_stats(self) -> tuple[int, int]:
        """``(hits, misses)`` of the decoded-block cache since the file was opened (or the
        counters last reset by :meth:`clear_cache`); a miss is one block decoded."""
        return self._cache.hits, self._cache.misses

    def summarised_blocks(self) -> int:
        """How many blocks have a min/max summary so far (O(1); a background warm-up reports its progress from it)."""
        return self._n_summarised

    def thread_block_requests(self) -> int:
        """How many blocks the CALLING thread has asked :meth:`decode_block` for so far; the difference over a
        stretch of work says whether that work needed decoded samples, whatever other threads decoded meanwhile
        (:meth:`cache_stats` is the file's, shared by every thread)."""
        return getattr(self._tls, "n", 0)

    def clear_cache(self, reset_counters: bool = True) -> None:
        """Forget every decoded block, block summary and filled min/max envelope (a cold start, for
        ``adichtion bench``); the counters of :meth:`cache_stats` restart too."""
        self._cache.clear()
        self._envelopes.clear()
        self._tables.clear()
        self._envelope_gen += 1
        with self._summaries_lock:
            self._summaries.clear()
            self._n_summarised = 0
        if reset_counters:
            self._cache.hits = self._cache.misses = 0

    def cache_usage(self) -> tuple[int, int]:
        """``(bytes held, byte limit)`` of the decoded-block cache."""
        return self._cache.nbytes, self._cache.max_bytes

    def close(self) -> None:
        self._cache.clear()
        self._envelopes.clear()
        self._tables.clear()
        self._summaries.clear()
        if self._container is not None:
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
        """The ``.adicht`` container version (26, 29, ...); 0 for a Mac document."""
        return self._container.version if self._container is not None else 0

    @property
    def format_label(self) -> str:
        """What kind of file this is, for people: ``"LabChart format v29"`` or
        ``"LabChart for Mac document (7.2)"``."""
        if self.mac is not None:
            major, minor, bugfix = self.mac.version
            return f"LabChart for Mac document ({major}.{minor}" + (f".{bugfix}" if bugfix else "") + ")"
        return f"LabChart format v{self.format_version}"

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

    def has_data(self, channel: int) -> bool:
        """False for a channel whose samples the file does not store: a calculated
        channel of a Mac document (LabChart for Mac computed those when drawing)."""
        return self.index.has_data(channel)

    def channel_definition(self, channel: int) -> Optional[str]:
        """The calculation a Mac document stores for a calculated channel, in
        LabChart's terms (``"Arithmetic: Ch9-Ch8 [cmH2O]"``); None otherwise.  A
        ``.adicht`` file stores no definitions (see :mod:`adichtion.infer`)."""
        return self.mac.definition(channel) if self.mac is not None else None

    @property
    def movie_files(self) -> list[str]:
        """Movie files a Mac document names (its video capture), in block order; they are
        :attr:`videos` too, timed where the file could be read."""
        return list(self.mac.movie_files) if self.mac is not None else []

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

    def _mac_videos(self) -> list[VideoClip]:
        """The movies of a Mac document as clips, timed from their frame stamps where the
        file in ``<name> movies`` can be read (:mod:`adichtion.qtmovie`)."""
        clips: list[VideoClip] = []
        folder = movies_folder(self.path)
        for r, name in enumerate(self.mac.record_movies):
            if not name:
                continue
            info = self.chart.records[r]
            clock0 = self.mac.record_clock_us[r] if r < len(self.mac.record_clock_us) else None
            start = clock0 / 1e6 if clock0 is not None else 0.0
            stop = start + info.ticks / info.tick_rate_hz
            offset, stamped = 0.0, False
            path = os.path.join(folder, name)
            if clock0 is not None and os.path.isfile(path):
                try:
                    offset, stamped = read_movie_timing(path).offset_s(clock0), True
                except (OSError, MovieError):
                    pass
            clips.append(VideoClip(record_index=r, file_name=name, start_s=start, stop_s=stop,
                                   video_start_s=start - offset, offset_s=offset, stamped=stamped))
        return clips

    def video_for(self, record: int) -> Optional[VideoClip]:
        for v in self.videos:
            if v.record_index == record:
                return v
        return None

    # -- decoding ------------------------------------------------------------
    def _check_not_truncated(self) -> None:
        """A window on a mapping faults (SIGBUS, access violation) if the file is cut short after it was
        opened; one ``fstat`` per decoded block turns the detectable cases into an ordinary error."""
        try:
            size = os.fstat(self._container._fh.fileno()).st_size
        except (OSError, ValueError):
            return
        if size < len(self._container.data):
            raise AdichtFormatError(f"{self.path!r} was shortened after it was opened; open it again")

    def _body(self, sb: SubBlock) -> memoryview:
        return memoryview(self._blocks)[sb.body_offset: sb.body_offset + sb.body_size]

    def decode_block(self, record: int, block: int, channel: int, cache: bool = True) -> np.ndarray:
        """Decoded samples of one block: int32 counts or float32 values (cached).
        Every block is self-contained, so this is O(1) blocks.  A block decoded for the
        first time also leaves its min/max summary behind (:meth:`summary_store`).

        ``cache=False`` decodes a block that is not cached yet without keeping it: the summary is
        left, the block cache (and what the user's view has in it) is not disturbed.  A background
        warm-up of a file bigger than the cache would otherwise push out the blocks being looked at."""
        self._tls.n = getattr(self._tls, "n", 0) + 1
        if self.mac is not None:
            mb = self.index.get(record, block, channel)
            if mb is None:
                return np.zeros(0, dtype=np.int32)
            key = mb.key
        else:
            sb = self.index.get(record, block, channel)
            key = sb.index
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if self.zero_copy:
            self._check_not_truncated()
        if self.mac is not None:
            data = decode_mac_block(memoryview(self._blocks)[mb.offset: mb.offset + mb.length], mb)
        else:
            data = decode_subblock(self._body(sb), sb)
        self._remember_summary(record, block, channel, data)
        if cache:
            self._cache.put(key, data)
        return data

    def _remember_summary(self, record: int, block: int, channel: int, data: np.ndarray) -> None:
        store = self.summary_store(record, channel, create=True)
        if store is not None and not store.have[block] and store.put(block, data):
            with self._summaries_lock:
                self._n_summarised += 1

    def summary_store(self, record: int, channel: int, create: bool = False) -> Optional[_ChannelSummaries]:
        """The min/max summaries of the blocks of ``channel`` in ``record`` that have been decoded
        so far (None when there are none yet and ``create`` is False, or when the channel stores no
        samples).  They live as long as the file is open and do not count against the block cache."""
        key = (record, channel)
        store = self._summaries.get(key)
        if store is not None or not create:
            return store
        rec = self.records[record]
        cr = self.channel_record(record, channel)
        if cr is None or cr.dtype == "none" or not self.index.has_data(channel):
            return None
        with self._summaries_lock:
            store = self._summaries.get(key)
            if store is None:
                ticks = np.cumsum([0] + rec.block_ticks)
                bounds = np.array([rec.tick_to_sample(int(t), channel) for t in ticks], dtype=np.int64)
                store = self._summaries[key] = _ChannelSummaries(bounds)
        return store

    # -- consistency checks (for tests and the "verify" command) ----------------------
    def walk_subblocks(self) -> list[SubBlock]:
        """Sub-blocks found by walking the Blocks_0 chain (independent of the Map index);
        empty for a Mac document, which has no such chain."""
        if self.mac is not None:
            return []
        return list(iter_subblocks(self._blocks))

    def block_minmax(self, record: int, channel: int, fill: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """Stored min/max per block of a record.  Blocks whose min/max the index does
        not hold (some Mac documents, MAC_FORMAT.md §3) are NaN, or with ``fill`` are
        decoded once and remembered; a viewer's GUI thread passes ``fill=False`` and
        gets the filled values once a worker has filled them."""
        key = (record, channel)
        if key in self._envelopes:
            mins, maxs = self._envelopes[key]
            return mins.copy(), maxs.copy()
        cr = self.channel_record(record, channel)
        mins = np.array([b.min for b in cr.blocks], dtype=np.float64)
        maxs = np.array([b.max for b in cr.blocks], dtype=np.float64)
        missing = np.flatnonzero(np.isnan(mins) | np.isnan(maxs))
        if len(missing) and not fill:
            return mins, maxs
        for b in missing:
            x = self.decode_block(record, int(b), channel)
            if len(x):
                mins[b], maxs[b] = float(x.min()), float(x.max())
        self._envelopes[key] = (mins, maxs)
        self._envelope_gen += 1
        return mins.copy(), maxs.copy()

    def block_table(self, channel: int, fill: bool = False) -> ChannelBlockTable:
        """The block index of ``channel`` over all records as one :class:`ChannelBlockTable`, built
        once and kept (the same numbers :meth:`Record.block_envelope` returns record by record,
        nothing decoded unless ``fill`` asks for the blocks whose min/max the index lacks, as
        :meth:`block_minmax` does).  A table that still has such blocks is rebuilt when ``fill`` is
        asked for, or when another call has filled some since."""
        t = self._tables.get(channel)
        if t is not None and (t.complete or (not fill and t.generation == self._envelope_gen)):
            return t
        gen = self._envelope_gen
        n = len(self.records)
        counts = np.zeros(n, dtype=np.int64)
        ticks: list[int] = []
        mins: list[float] = []
        maxs: list[float] = []
        unit_of = np.zeros(n, dtype=np.int64)
        groups: dict[object, int] = {}
        for r in range(n):
            cr = self.channel_record(r, channel)
            if cr is None or cr.dtype == "none" or not cr.blocks:
                continue
            bl = cr.blocks
            counts[r] = len(bl)
            ticks.extend([b.ticks for b in bl])
            mins.extend([b.min for b in bl])
            maxs.extend([b.max for b in bl])
            unit_of[r] = groups.setdefault(id(cr.units), len(groups)) + 1      # 0 = no data
        first = np.concatenate([[0], np.cumsum(counts)])
        mn = np.array(mins, dtype=np.float64)
        mx = np.array(maxs, dtype=np.float64)
        record = np.repeat(np.arange(n, dtype=np.int64), counts)
        bad = np.isnan(mn) | np.isnan(mx)
        if bad.any():
            for r in np.unique(record[bad]):
                a, b = int(first[r]), int(first[r + 1])
                mn[a:b], mx[a:b] = self.block_minmax(int(r), channel, fill)
        # nothing more to fill: no NaN left, or a fill was asked for and what stays NaN is NaN in the samples
        complete = fill or not (np.isnan(mn) | np.isnan(mx)).any()
        tk = np.array(ticks, dtype=np.int64)
        before = np.cumsum(tk) - tk
        tick0 = (before - before[first[record]]).astype(np.float64) if len(tk) else np.zeros(0)
        # stored -> units once per distinct units object, on all its blocks at once; min and max are
        # then put in order (a negative slope swaps them) and a block missing either stays NaN in both
        unit_of_block = np.repeat(unit_of, counts)
        for g in np.unique(unit_of_block):
            sel = unit_of_block == g
            rec = self.records[int(record[sel][0])]
            a, b = rec.convert(channel, mn[sel]), rec.convert(channel, mx[sel])
            lo, hi = np.fmin(a, b), np.fmax(a, b)
            gap = np.isnan(a) | np.isnan(b)
            lo[gap] = hi[gap] = np.nan
            mn[sel], mx[sel] = lo, hi
        t = ChannelBlockTable(record, tick0, tick0 + tk, mn, mx, first, complete, gen)
        self._tables[channel] = t
        return t


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
    def video_candidate(self) -> Optional[str]:
        """Where :attr:`video_file` looks (``None`` when it cannot be a usable clip); a path
        computation only, so it is cheap and safe to call for every record of a big file."""
        v = self.video
        if v is None or (self.file.kind == "mac" and not v.stamped):
            return None
        return video_candidate_path(self.file.path, v)

    @property
    def video_file(self) -> Optional[str]:
        """Path of the clip's file if it exists in the ``<name> movies`` folder (for a Mac
        document: and its timing could be read, which rules out cloud placeholders). One
        file-system lookup per call: ask for it when the clip is shown, not for every record."""
        p = self.video_candidate
        return p if p is not None and os.path.exists(p) else None

    def __repr__(self) -> str:
        return f"Record({self.index}, {self.start_local:%Y-%m-%d %H:%M:%S}, {self.duration_s:.2f} s, {len(self.comments)} comments)"

    # -- sample geometry -----------------------------------------------------
    def samples_per_tick(self, channel: int) -> float:
        return self.file.index.samples_per_tick(self.index, channel)

    def sample_rate_hz(self, channel: int) -> float:
        return self.tick_rate_hz * self.samples_per_tick(channel)

    def n_samples(self, channel: int) -> int:
        return self.tick_to_sample(self.ticks, channel)

    def tick_to_sample(self, tick: int, channel: int) -> int:
        """Number of the channel's samples before ``tick`` (each format's own rule)."""
        return self.file.index.tick_to_sample(self.index, channel, tick)

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
        channels, float32 for calculated channels (empty for a channel the file
        does not store)."""
        return self._read_samples(channel, *self._sample_range(start, stop, channel))

    def _read_samples(self, channel: int, s0: int, s1: int) -> np.ndarray:
        """Stored values of samples ``[s0, s1)``."""
        out: list[np.ndarray] = []
        tick0 = 0
        for b, bt in enumerate(self.block_ticks):
            b0 = self.tick_to_sample(tick0, channel)
            b1 = self.tick_to_sample(tick0 + bt, channel)
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

    def sample_at(self, channel: int, tick: int, units: bool = True) -> Optional[float]:
        """The value (as :meth:`read` gives it) of the stored sample at or before
        ``tick``: for a channel sampled slower than the tick rate, the latest
        sample.  None outside the record or for a channel without data."""
        n = self.n_samples(channel)
        if n == 0 or not 0 <= tick < self.ticks:
            return None
        k = min(max(self.tick_to_sample(int(tick) + 1, channel) - 1, 0), n - 1)
        v = self._read_samples(channel, k, k + 1)
        if not len(v):
            return None
        return float(self.convert(channel, v)[0]) if units else float(v[0])

    def read(self, channel: int, start: int = 0, stop: Optional[int] = None, units: bool = True) -> np.ndarray:
        """Samples as float64.  With ``units=True`` the stored values are scaled
        by the channel's LSB (counts -> volts, calculated values -> their base
        scale) and converted with the record's units conversion when it is
        switched on; see :meth:`value_label` for what comes out."""
        raw = self.read_raw(channel, start, stop)
        if not units:
            return raw.astype(np.float64)
        return self.convert(channel, raw)

    def read_ticks(self, channel: int, start: int = 0, stop: Optional[int] = None, units: bool = True) -> np.ndarray:
        """One value per tick in ``[start, stop)``, as float64 like :meth:`read`.  A channel
        stored at every tick is read as it is; one stored at every *d*-th tick is
        up-sampled the way LabChart's own export does (:func:`decimate.upsample_like_labchart`:
        linear interpolation, ADC counts rounded to whole counts, the last sample held)."""
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        spt = self.samples_per_tick(channel)
        if stop <= start or spt == 0:
            return np.zeros(0)
        if spt == 1.0:
            return self.read(channel, start, stop, units=units)
        d = int(round(1.0 / spt))
        if d < 1 or abs(d * spt - 1.0) > 1e-9:
            raise ValueError(f"channel {channel + 1} is stored at {spt:g} samples per tick; "
                             "only whole-number rate ratios can be up-sampled")
        total = self.n_samples(channel)
        k0 = start // d
        k1 = min(total, (stop - 1) // d + 2)         # up to the sample after the last tick's interval
        raw = upsample_like_labchart(self._read_samples(channel, k0, k1), d, start, stop, first=k0, total=total)
        return self.convert(channel, raw) if units else raw.astype(np.float64)

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
    def block_envelope(self, channel: int, units: bool = True,
                       fill: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(t_start, t_end, min, max)`` per 16000-tick block, straight from the
        Map stream's block index (nothing is decoded).  Empty arrays for a
        legacy parse or a channel without data.  A block whose min/max the index
        does not hold is decoded (``fill``) or NaN (``fill=False``)."""
        cr = self.file.channel_record(self.index, channel)
        if cr is None or cr.dtype == "none":
            e = np.zeros(0)
            return e, e, e, e
        ticks = np.array([b.ticks for b in cr.blocks], dtype=np.float64)
        t1 = np.cumsum(ticks) / self.tick_rate_hz
        t0 = t1 - ticks / self.tick_rate_hz
        mins, maxs = self.file.block_minmax(self.index, channel, fill)
        if units:
            mins, maxs = self.convert(channel, mins), self.convert(channel, maxs)
            if len(mins) and mins[0] > maxs[0]:     # a negative slope swaps them
                mins, maxs = maxs, mins
        return t0, t1, mins, maxs

    @staticmethod
    def _spread(t0: float, t1: float, n_points: int, b0: np.ndarray, b1: np.ndarray,
                mins: np.ndarray, maxs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(time, min, max)`` in ``n_points`` buckets over ``[t0, t1]`` from pieces that each
        span ``[b0, b1]`` with the given min/max; a piece feeds every bucket it overlaps (one or
        two when the buckets are wider than a piece, a run of them when it is wider)."""
        edges = np.linspace(t0, t1, n_points + 1)
        lo = np.full(n_points, np.inf)
        hi = np.full(n_points, -np.inf)
        width = (t1 - t0) / n_points
        keep = (b1 > t0) & (b0 < t1)
        b0, b1, mins, maxs = b0[keep], b1[keep], mins[keep], maxs[keep]
        k0 = np.clip(np.floor((b0 - t0) / width), 0, n_points - 1).astype(np.int64)
        k1 = np.clip(np.floor((b1 - t0 - 1e-9) / width), 0, n_points - 1).astype(np.int64)
        counts = k1 - k0 + 1
        idx = np.repeat(k0, counts) + (np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts))
        # fmin/fmax skip the NaN of pieces whose min/max is not stored (and not yet filled)
        np.fmin.at(lo, idx, np.repeat(mins, counts))
        np.fmax.at(hi, idx, np.repeat(maxs, counts))
        lo[~np.isfinite(lo)] = np.nan
        hi[~np.isfinite(hi)] = np.nan
        return (edges[:-1] + edges[1:]) / 2, lo, hi

    def summary_overview(self, channel: int, n_points: int, start: int = 0, stop: Optional[int] = None,
                         units: bool = True) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """The :meth:`overview` envelope of ``[start, stop)`` in ``n_points`` buckets taken from the
        min/max summaries that decoding leaves behind (``SUMMARY_BUCKETS`` per block), without
        decoding.  None when that cannot be done: a block in the range was never decoded, or a
        pixel bucket is narrower than a summary bucket.

        The result differs from the one decoded from the samples only at the ends of each bucket: a
        summary bucket feeds every pixel bucket it overlaps, so a pixel bucket can show extremes up to
        one summary bucket (a 1/32 block, and never more than one pixel bucket) outside its own
        samples; it never misses one.  The ends of the requested range are treated alike."""
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        store = self.file.summary_store(self.index, channel)
        if store is None or n_points <= 0 or stop <= start:
            return None
        s0, s1 = self.tick_to_sample(start, channel), self.tick_to_sample(stop, channel)
        bounds = store.bounds
        nb = len(bounds) - 1
        if s1 <= s0 or nb == 0:
            return None
        b_lo = max(int(np.searchsorted(bounds, s0, side="right")) - 1, 0)
        b_hi = min(int(np.searchsorted(bounds, s1, side="left")), nb)
        if b_hi <= b_lo or not store.have[b_lo:b_hi].all():
            return None
        cnt = store.count[b_lo:b_hi].astype(np.int64)
        ok = cnt > 0
        if not ok.any():
            return None
        n_b, cnt, base = store.n[b_lo:b_hi][ok], cnt[ok], bounds[b_lo: b_hi][ok]
        mins, maxs = store.mins[b_lo:b_hi][ok], store.maxs[b_lo:b_hi][ok]
        j = np.arange(SUMMARY_BUCKETS + 1, dtype=np.int64)[None, :]
        edges = base[:, None] + (j * n_b[:, None]) // cnt[:, None]          # sample edges, blocks x (K+1)
        valid = j[:, :-1] < cnt[:, None]
        rate = self.sample_rate_hz(channel)
        ta, tb = (edges[:, :-1] / rate)[valid], (edges[:, 1:] / rate)[valid]
        t0, t1 = start / self.tick_rate_hz, stop / self.tick_rate_hz
        if float((tb - ta).max()) > (t1 - t0) / n_points * (1.0 + 1e-9):
            return None
        mn, mx = mins[valid], maxs[valid]
        if units:
            a, b = self.convert(channel, mn), self.convert(channel, mx)
            mn, mx = np.fmin(a, b), np.fmax(a, b)                            # a negative slope swaps them
        else:
            mn, mx = mn.astype(np.float64), mx.astype(np.float64)
        return self._spread(t0, t1, n_points, ta, tb, mn, mx)

    def quick_overview(self, channel: int, n_points: int, start: int = 0, stop: Optional[int] = None,
                       units: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(time, min, max)`` of ``[start, stop)`` in ``n_points`` buckets from what is at hand,
        with nothing decoded: the min/max summaries of the blocks that were decoded before
        (``SUMMARY_BUCKETS`` per block) and the block index for the others (NaN where the index
        holds no min/max).  Each piece feeds every bucket it overlaps, so the envelope is never
        narrower than the samples; it is a first picture for a viewer, not a measurement."""
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        n_points = max(1, int(n_points))
        t0, t1 = start / self.tick_rate_hz, stop / self.tick_rate_hz
        b0, b1, mins, maxs = self.block_envelope(channel, units, fill=False)
        store = self.file.summary_store(self.index, channel)
        if store is not None and len(b0) == len(store.have) and stop > start:
            k0 = max(int(np.searchsorted(b1, t0, side="right")), 0)
            k1 = min(int(np.searchsorted(b0, t1, side="left")), len(b0))
            fine = np.flatnonzero(store.have[k0:k1] & (store.count[k0:k1] > 0)) + k0
            if len(fine):
                cnt = store.count[fine].astype(np.int64)
                j = np.arange(SUMMARY_BUCKETS + 1, dtype=np.int64)[None, :]
                edges = store.bounds[fine][:, None] + (j * store.n[fine][:, None]) // cnt[:, None]
                valid = j[:, :-1] < cnt[:, None]
                rate = self.sample_rate_hz(channel)
                mn, mx = store.mins[fine][valid], store.maxs[fine][valid]
                if units:
                    a, b = self.convert(channel, mn), self.convert(channel, mx)
                    mn, mx = np.fmin(a, b), np.fmax(a, b)                    # a negative slope swaps them
                rest = np.ones(len(b0), dtype=bool)
                rest[fine] = False
                b0 = np.concatenate([b0[rest], (edges[:, :-1] / rate)[valid]])
                b1 = np.concatenate([b1[rest], (edges[:, 1:] / rate)[valid]])
                mins, maxs = np.concatenate([mins[rest], mn]), np.concatenate([maxs[rest], mx])
        return self._spread(t0, t1, n_points, b0, b1, mins, maxs)

    def overview(self, channel: int, n_points: int = 2000, start: int = 0, stop: Optional[int] = None,
                 units: bool = True, coarse: bool = False, fill: bool = True,
                 from_summaries: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(time, min, max)`` envelope of ``channel`` over ``[start, stop)`` in
        ``n_points`` buckets, for drawing a long record at screen resolution.
        Buckets of a whole block or more are served from the Map stream's
        block index without decoding; with ``coarse=True`` the index is used
        whenever the range holds at least one block, each block's min/max then
        covering every bucket it overlaps (a quick first picture a viewer can
        refine once the samples are decoded).  With ``from_summaries=True`` a range
        whose blocks have all been decoded before, and whose buckets are at least a
        summary bucket wide, comes from the block summaries instead of from the samples
        (:meth:`summary_overview` says how that differs); anything else is decoded as usual."""
        stop = self.ticks if stop is None else min(stop, self.ticks)
        start = max(0, start)
        t0 = start / self.tick_rate_hz
        t1 = stop / self.tick_rate_hz
        cr = self.file.channel_record(self.index, channel)
        span = stop - start
        if cr is not None and n_points > 0 and (span >= n_points * BLOCK_TICKS or (coarse and span >= BLOCK_TICKS)):
            b0, b1, mins, maxs = self.block_envelope(channel, units, fill)
            return self._spread(t0, t1, n_points, b0, b1, mins, maxs)
        if from_summaries:
            got = self.summary_overview(channel, n_points, start, stop, units)
            if got is not None:
                return got
        x = self.read(channel, start, stop, units=units)
        mins, maxs = minmax_envelope(x, n_points)
        t = np.linspace(t0, t1, len(mins), endpoint=False) + (t1 - t0) / max(len(mins), 1) / 2
        return t, mins, maxs
