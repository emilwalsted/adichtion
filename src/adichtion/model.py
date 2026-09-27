"""Plain data classes describing the content of an ``.adicht`` file.

They carry no file handles and no numpy arrays, so a UI can keep them around,
pickle them or show them in a table without touching the file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class UnitsConversion:
    """LabChart two-point units conversion of one channel: ``value = slope * volts + offset``."""

    base_units: str          # e.g. "V"
    units: str               # e.g. "cmH2O" ("" when the channel is not converted)
    slope: float
    offset: float
    calibration: tuple[str, str, str, str] = ("", "", "", "")  # the four dialog strings as stored
    stream_offset: int = -1  # where the record was found in the Map stream (debugging aid)

    @property
    def is_identity(self) -> bool:
        return self.slope == 1.0 and self.offset == 0.0 and not self.units

    def convert(self, volts):
        return self.slope * volts + self.offset


@dataclass(frozen=True)
class ChannelSetup:
    """Input settings of one channel as stored in the ``<_ChannelSetup>`` XML (format v29+)."""

    index: int                      # 0-based channel index
    range_volts: Optional[float]    # nominal full-scale range, e.g. 10.0 or 0.2
    sample_rate_hz: Optional[float]
    device: str = ""
    xml: str = ""


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
    units: list[UnitsConversion]            # units-conversion records in stream order
    setups: dict[int, ChannelSetup]         # channel index -> input setup (v29+ only)

    @property
    def comments(self) -> list[Comment]:
        return [c for r in self.records for c in r.comments]


@dataclass(frozen=True)
class ChannelInfo:
    """Static description of one recorded channel, as exposed by :class:`adichtion.AdichtFile`."""

    index: int                          # 0-based
    title: str
    dtype: str                          # "int16" (raw ADC counts) or "float32" (calculated channel)
    units: Optional[UnitsConversion]    # None when no units record could be associated
    range_volts: Optional[float]        # None when unknown (format v26 files)
    samples_per_tick: float             # 1.0 for channels sampled at the tick rate

    @property
    def volts_per_count(self) -> Optional[float]:
        """LSB size in volts.  The nominal "10 V" range has a 312.5 uV LSB, i.e. the
        converter spans +-range*1.024 over 2**16 counts."""
        if self.range_volts is None:
            return None
        return self.range_volts * 1.024 / 32768.0
