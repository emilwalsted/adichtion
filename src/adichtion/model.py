"""Plain data classes describing the content of an ``.adicht`` file.

They carry no file handles and no numpy arrays, so a UI can keep them around,
pickle them or show them in a table without touching the file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

UNITS_ENABLED_FLAG = 0x10000   # bit 16 of UnitsConversion.flags: the conversion is switched on


@dataclass(frozen=True)
class UnitsConversion:
    """The *units object* stored per channel: the scale of the stored samples
    and LabChart's units conversion.

    ``value = slope * (lsb * x) + offset`` when :attr:`enabled`, else
    ``lsb * x``, where ``x`` is the stored sample (int16 ADC count or float32
    calculated value).  For ADC channels ``lsb`` is the volts per count
    (``range * 1.024 / 32768``); calculated channels store their values as
    "counts" of a virtual range and carry the matching ``lsb`` (``2**-15`` for
    channels derived from converted channels, the source channel's LSB for
    channels such as an RMS of a raw channel).
    """

    base_units: str          # the "input" units string, e.g. "V" (empty for calculated channels)
    units: str               # e.g. "cmH2O" ("" when the channel is not converted or the name is not stored)
    slope: float
    offset: float
    lsb: float = math.nan    # scale of the stored samples; NaN when unknown (legacy scan parse)
    mode: int = -1           # first of the four int32 after slope/offset (0..4 seen); -1 = not read (legacy parse)
    flags: int = 0           # third of those int32: bit 16 = enabled
    calibration: tuple[str, str, str, str] = ("", "", "", "")  # the four dialog strings as stored
    object_id: int = -1      # serialization object id (later records reference it)
    stream_offset: int = -1  # where the object was found in the Map stream (debugging aid)

    @property
    def enabled(self) -> bool:
        """True when LabChart applies the conversion (bit 16 of :attr:`flags`).
        For a legacy scan parse (no flags read) a non-identity slope/offset counts as enabled."""
        if self.mode >= 0:
            return bool(self.flags & UNITS_ENABLED_FLAG)
        return not (self.slope == 1.0 and self.offset == 0.0)

    @property
    def is_identity(self) -> bool:
        return not self.enabled

    @property
    def has_lsb(self) -> bool:
        return not math.isnan(self.lsb)

    def convert(self, volts):
        """Apply slope/offset to values already scaled to the input units (volts)."""
        return self.slope * volts + self.offset

    def scale(self, x):
        """Stored samples -> physical values (or volts when the conversion is off)."""
        v = x * self.lsb
        return self.slope * v + self.offset if self.enabled else v


@dataclass(frozen=True)
class ChannelSetup:
    """Input settings of one channel as stored in the ``<_ChannelSetup>`` XML
    of a ``ChannelDataSource`` object (format v29+; one per channel per record,
    later records usually reference an earlier object)."""

    index: int                      # 0-based channel index (from value-code)
    range_volts: Optional[float]    # nominal full-scale range, e.g. 10.0 or 0.2
    sample_rate_hz: Optional[float]
    device: str = ""
    xml: str = ""
    object_id: int = -1


@dataclass(frozen=True)
class Comment:
    id: int
    record_index: int        # 0-based record
    channel: int             # 0-based channel, -1 = all channels
    tick: int                # tick within the record
    text: str

    def time_s(self, tick_rate_hz: float) -> float:
        return self.tick / tick_rate_hz


@dataclass(frozen=True)
class BlockInfo:
    ticks: int
    flags: int
    n_comments: int


@dataclass(frozen=True)
class ChannelBlock:
    """One entry of the per-channel block index kept in the Map stream."""

    ticks: int          # ticks covered by the block
    min: float          # of the stored samples (counts or float32 values)
    max: float
    offset: int         # byte offset of the sub-block header in the Blocks_0 stream
    dtype: str          # "int16" or "float32" (from the entry tag)


@dataclass
class ChannelRecordInfo:
    """What the Map stream says about one channel within one record."""

    channel: int
    record: int
    dtype: str                          # "int16" or "float32"
    tick_rate_hz: float
    ticks: int
    block_ticks: int                    # nominal block length (16000)
    samples_per_block: int              # samples per full block for this channel
    units: Optional[UnitsConversion]    # resolved (own object or the referenced one)
    units_ref: int                      # object id of the units object (0 = none)
    setup: Optional[ChannelSetup]       # resolved ChannelDataSource XML (v29+), else None
    setup_ref: int
    blocks: list[ChannelBlock] = field(default_factory=list)
    entry_version: int = 0
    stream_offset: int = -1


@dataclass
class RecordInfo:
    """One LabChart *block* (called a record here, following the ADI SDK)."""

    index: int
    tick_rate_hz: float
    start_utc: float          # seconds since 1970-01-01 UTC (as stored: float64)
    utc_offset_s: float       # local time = UTC + utc_offset_s
    ticks: int
    stream_offset: int
    blocks: list[BlockInfo] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    channels: list[ChannelRecordInfo] = field(default_factory=list)   # empty for a legacy scan parse
    first_block_index: int = 0   # running block count before this record (as stored)
    version: int = 0             # record structure version (2 = v26 files, 3 = v29 files)
    name: str = ""

    @property
    def duration_s(self) -> float:
        return self.ticks / self.tick_rate_hz

    @property
    def start_datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.start_utc, tz=timezone.utc)

    @property
    def start_datetime_local(self) -> datetime:
        tz = timezone(timedelta(seconds=self.utc_offset_s))
        return datetime.fromtimestamp(self.start_utc, tz=tz)


@dataclass
class ChartData:
    """Everything parsed from the ``Map`` ("ADInstruments Chart Data") stream."""

    object_name: str
    format_version: tuple[int, int, int]
    records: list[RecordInfo]
    titles: list[str]                       # channel titles, index 0 = channel 1
    units: list[UnitsConversion]            # every units object in stream order
    setups: dict[int, ChannelSetup]         # channel index -> first input setup seen (v29+ only)
    n_channels: int = 0                     # recorded channels (from the header); 0 when unknown
    parse_mode: str = "sequential"          # "sequential" (full grammar) or "scan" (legacy heuristics)
    trailer: bytes = b""                    # bytes after the channel titles, as stored

    @property
    def comments(self) -> list[Comment]:
        return [c for r in self.records for c in r.comments]

    def channel_record(self, record: int, channel: int) -> Optional[ChannelRecordInfo]:
        r = self.records[record]
        return r.channels[channel] if channel < len(r.channels) else None


@dataclass(frozen=True)
class ChannelInfo:
    """Static description of one recorded channel, as exposed by :class:`adichtion.AdichtFile`."""

    index: int                          # 0-based
    title: str
    dtype: str                          # "int16" (raw ADC counts) or "float32" (calculated channel)
    units: Optional[UnitsConversion]    # units object of the first record (None when unknown)
    range_volts: Optional[float]        # input range of an ADC channel; None when unknown
    samples_per_tick: float             # 1.0 for channels sampled at the tick rate

    @property
    def volts_per_count(self) -> Optional[float]:
        """LSB size in volts.  The nominal "10 V" range has a 312.5 uV LSB, i.e. the
        converter spans +-range*1.024 over 2**16 counts."""
        if self.units is not None and self.units.has_lsb and self.dtype == "int16":
            return self.units.lsb
        if self.range_volts is None:
            return None
        return self.range_volts * 1.024 / 32768.0


@dataclass(frozen=True)
class VideoClip:
    """One entry of the video module's stream: the video recorded with one record.

    ``offset_s`` is the position in the video file of the record's first
    sample *including the camera delay* LabChart measured in its video setup
    before the recording (``offset = start - video_start + delay``); the frame
    for record time ``t`` is at ``offset_s + t`` in the file.  The delay is set
    per recording, so it is constant within a file and may differ between
    files; ``delay_s`` exposes it for a viewer's manual correction.

    A LabChart for Mac movie (``stamped``) is timed by the capture stamp of every
    frame instead (:mod:`adichtion.qtmovie`): its clock is the Mac's
    ``Microseconds()``, ``offset_s`` is measured from the stamps and no camera
    delay is known (``delay_s`` 0).  ``stamped`` is False for such a movie
    whose file could not be read; its offset is then unknown (0).
    """

    record_index: int          # 0-based record
    file_name: str             # e.g. "Record5.wmv" ("" when no video was recorded; "*.tmp" when unfinished)
    start_s: float             # video-clock time (s) when the record started
    stop_s: float              # video-clock time (s) when the record stopped
    video_start_s: float       # video-clock time (s) of the first frame of the file
    offset_s: float            # record start expressed in file time (s), camera delay included, as stored by LabChart
    stamped: bool = False      # timed from the movie's frame stamps (LabChart for Mac)

    @property
    def has_video(self) -> bool:
        return bool(self.file_name) and not self.file_name.lower().endswith(".tmp")

    @property
    def wall_duration_s(self) -> float:
        return self.stop_s - self.start_s

    @property
    def delay_s(self) -> float:
        """The camera delay LabChart compensated for: ``offset - (start - video_start)``."""
        return self.offset_s - (self.start_s - self.video_start_s)

    def file_time(self, t: float) -> float:
        """Position (s) in the video file of record time ``t`` (seconds from the record start)."""
        return self.offset_s + t
