"""LabChart 7 for Mac files ("ADInst Data Res File"): settings and calculated-channel definitions.

The classic Mac LabChart wrote documents and settings files in its own
container: a Pascal-string header, a body of typed *chunks* and a table of
contents at the end.  Documents hold the sample data in the body as well;
that part is not read here.  What this module extracts is the **channel
settings**, in particular the definitions of calculated channels (Arithmetic
expressions, RMS sources, spirometry calibration, integrals), which the
Windows-format ``.adicht`` files written by the "Read/Write WinChart Files"
extension do not carry.  Big-endian throughout.

Layout (see ``docs/CALCULATED_CHANNELS.md``)::

    u8 20, "ADInst Data Res File", 3 bytes, uint32 toc_offset, uint32 toc_length
    chunks ...
    toc: 14-byte entries  char[4] type, uint16 id, uint32 offset, uint32 length

The channel settings live in the ``Mcro`` chunks with ids 30001/30002 (the
document's own settings; user macros have small ids) as a stream of
*property records*::

    u8 0, u8 type, uint16 key, uint32 channel (1-based, 0 = document), payload

with the payload size given by the type (1/0x0a: int16, 4: int32, 0x0c: 4
bytes, 0x0e: 6 bytes, 0x10: 8 bytes, 0x18: float64, 8: Pascal string).
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union

MAGIC = b"\x14ADInst Data Res File"
SETTINGS_CHUNK_IDS = (30001, 30002)

# property record types -> payload size (None: Pascal string)
PROPERTY_TYPES: dict[int, Optional[int]] = {0x01: 2, 0x0A: 2, 0x04: 4, 0x0C: 4, 0x0E: 6, 0x10: 8, 0x18: 8, 0x08: None}

# keys with a known meaning (channel-scoped unless noted)
KEY_TITLE = 0x290B              # channel title
KEY_CALCULATION = 0xE878        # extension computing the channel, as its menu label ("Arithmetic…", "RMS…"); "" for raw inputs
KEY_ARITH_EXPRESSION = 0x2BB8   # Arithmetic expression, e.g. "Ch9-Ch8" (stored with a trailing 0x01)
KEY_ARITH_UNITS = 0x2BBB        # Arithmetic output units
KEY_ARITH_TOP = 0x2BB9          # Arithmetic display range top
KEY_ARITH_BOTTOM = 0x2BBA       # Arithmetic display range bottom
KEY_RMS_SOURCE = 0x2DC4         # RMS source channel (1-based)
KEY_RMS_PARAMS = (0x2DC5, 0x2DC6, 0x2DC7, 0x2DC8)   # constant (340, 2, 1.0, 84) in every file seen; not the window length (see docs)
KEY_SPIRO_FLOW_SOURCE = 0x2E4B  # Spirometry flow: raw flow channel (1-based)
KEY_SPIRO_FLOW_CAL = 0x2E4E     # Spirometry flow: L/s per V of the raw channel
KEY_SPIRO_VOL_SOURCE = 0x2E55   # Spirometry volume: flow channel (1-based)
KEY_SPIRO_VOL_RATIO = 0x2E58    # Spirometry volume: volume ratio k (the BTPS factor, 1.09, by default)
KEY_N_CHANNELS = 0x287F         # document (channel 0), int16: number of channels = the Map4 chunks of every document seen
                                # (0x2849, read as the count at first, is 21 in every file whatever the count: MAC_FORMAT.md)

SOURCE_KEYS = {"RMS": KEY_RMS_SOURCE, "Spiro. Flow": KEY_SPIRO_FLOW_SOURCE, "Spiro. Volume": KEY_SPIRO_VOL_SOURCE}

# The keys of an extension's settings are not fixed.  LabChart gives every extension it
# loads a range of keys, and the ranges move from one installation to the next; the
# ``McrE`` chunk records them per file (268-byte entries: uint16 first, uint16 last,
# uint16 count, Str255 name, 6 bytes).  A channel property of an extension has the key
# 0x2000 + a key of its range.  The KEY_* constants above are the keys under the ranges of
# the 2016 files; ``extension_keys`` finds the keys of any file.
EXTENSION_ENTRY_SIZE = 268
CHANNEL_KEY_OFFSET = 0x2000
EXTENSION_KEY_OFFSETS: dict[str, dict[str, int]] = {        # extension name prefix -> key -> offset in its range
    "Arithmetic": {"arith_expression": 0, "arith_top": 1, "arith_bottom": 2, "arith_units": 3},
    "Root Mean Square": {"rms_source": 1},
    "Respiratory Flow": {"spiro_flow_source": 1, "spiro_flow_cal": 4},
    "Respiratory Volume": {"spiro_vol_source": 1, "spiro_vol_ratio": 4},
}
DEFAULT_EXTENSION_KEYS: dict[str, int] = {
    "arith_expression": KEY_ARITH_EXPRESSION, "arith_top": KEY_ARITH_TOP, "arith_bottom": KEY_ARITH_BOTTOM,
    "arith_units": KEY_ARITH_UNITS, "rms_source": KEY_RMS_SOURCE,
    "spiro_flow_source": KEY_SPIRO_FLOW_SOURCE, "spiro_flow_cal": KEY_SPIRO_FLOW_CAL,
    "spiro_vol_source": KEY_SPIRO_VOL_SOURCE, "spiro_vol_ratio": KEY_SPIRO_VOL_RATIO,
}

CALCULATION_LABELS = {
    "Arithmetic": "Arithmetic",
    "RMS": "Root mean square",
    "Integral": "Integral",
    "Spiro. Flow": "Spirometry flow",
    "Spiro. Volume": "Spirometry volume",
    "Digital Filter": "Digital filter",
}


class MacChartError(ValueError):
    pass


@dataclass(frozen=True)
class MacChunk:
    type: str
    id: int
    offset: int
    length: int


@dataclass(frozen=True)
class MacProperty:
    offset: int          # absolute file offset of the record
    type: int
    key: int
    channel: int         # 1-based channel, 0 = document level
    value: object        # int, float, str or bytes


@dataclass
class MacChannelSettings:
    """Settings of one channel as far as they are understood."""

    channel: int                                  # 1-based
    title: str = ""
    calculation: str = ""                         # extension name as stored ("" = raw input)
    expression: str = ""                          # Arithmetic expression
    units: str = ""                               # Arithmetic output units
    source: Optional[int] = None                  # source channel (1-based) for RMS / spirometry
    flow_calibration: Optional[float] = None      # spirometry: L/s per V
    volume_ratio: Optional[float] = None          # spirometry volume: k of the volume correction
    properties: dict[int, object] = field(default_factory=dict)   # every key seen for the channel (last value wins)

    @property
    def is_calculated(self) -> bool:
        return bool(self.calculation)

    def describe(self) -> str:
        """One line in LabChart's own terms, e.g. ``Arithmetic: Ch9-Ch8 [cmH2O]``."""
        c = self.calculation
        if not c:
            return "raw input"
        label = CALCULATION_LABELS.get(c, c)
        if c == "Arithmetic":
            return f"{label}: {self.expression}" + (f" [{self.units}]" if self.units else "")
        if c == "RMS":
            return f"{label} of Ch{self.source}" if self.source else label
        if c == "Spiro. Flow":
            cal = f", {self.flow_calibration:g} L/s per V" if self.flow_calibration is not None else ""
            return f"{label} of Ch{self.source}{cal}" if self.source else label + cal
        if c == "Spiro. Volume":
            k = f", volume ratio k = {self.volume_ratio:g}" if self.volume_ratio is not None else ""
            return f"{label} of Ch{self.source}{k}" if self.source else label + k
        return label


class MacResFile:
    """A LabChart 7 for Mac document or settings file."""

    def __init__(self, path: Union[str, os.PathLike]):
        self.path = os.fspath(path)
        with open(self.path, "rb") as fh:
            head = fh.read(32)
            if not head.startswith(MAGIC):
                raise MacChartError(f"{self.path!r} is not an ADInst Data Res File")
            self.toc_offset, self.toc_length = struct.unpack_from(">II", head, 24)
            fh.seek(0, os.SEEK_END)
            self.size = fh.tell()
            if self.toc_offset + self.toc_length != self.size or self.toc_length % 14:
                raise MacChartError("table of contents does not fit the file")
            fh.seek(self.toc_offset)
            toc = fh.read(self.toc_length)
        self.chunks: list[MacChunk] = []
        for i in range(0, self.toc_length, 14):
            typ = toc[i : i + 4].decode("latin-1")
            cid, off, ln = struct.unpack_from(">HII", toc, i + 4)
            self.chunks.append(MacChunk(typ, cid, off, ln))

    def find(self, typ: str, cid: Optional[int] = None) -> list[MacChunk]:
        return [c for c in self.chunks if c.type == typ and (cid is None or c.id == cid)]

    def read(self, chunk: MacChunk) -> bytes:
        with open(self.path, "rb") as fh:
            fh.seek(chunk.offset)
            return fh.read(chunk.length)

    @property
    def is_document(self) -> bool:
        """Documents carry sample data (``Map4`` chunks); settings files do not."""
        return bool(self.find("Map4"))

    def settings_chunks(self) -> list[MacChunk]:
        return [c for c in self.chunks if c.type == "Mcro" and c.id in SETTINGS_CHUNK_IDS]

    def channel_titles(self) -> list[str]:
        """Channel titles from the ``STR `` resources 1100.. (empty list when absent)."""
        out = []
        for c in sorted(self.find("STR "), key=lambda c: c.id):
            if c.id < 1100:
                continue
            data = self.read(c)
            if data and 1 + data[0] <= len(data):
                out.append(data[1 : 1 + data[0]].decode("mac_roman", "replace"))
        return out


def iter_properties(blob: bytes, base_offset: int = 0) -> Iterator[MacProperty]:
    """Scan a settings blob for property records, resynchronising on anything unknown."""
    p = 0
    n = len(blob)
    while p + 8 <= n:
        if blob[p] != 0 or blob[p + 1] not in PROPERTY_TYPES:
            p += 1
            continue
        typ = blob[p + 1]
        key, channel = struct.unpack_from(">HI", blob, p + 2)
        if key == 0 or channel > 256:
            p += 1
            continue
        q = p + 8
        size = PROPERTY_TYPES[typ]
        if size is None:
            if q >= n:
                break
            ln = blob[q]
            raw = blob[q + 1 : q + 1 + ln]
            if ln == 0 or len(raw) < ln:
                p += 1
                continue
            text = raw.decode("mac_roman", "replace").rstrip("\x00\x01\x02\x03")
            if any(ord(ch) < 32 for ch in text):
                p += 1
                continue
            yield MacProperty(base_offset + p, typ, key, channel, text)
            p = q + 1 + ln
            continue
        if q + size > n:
            break
        raw = blob[q : q + size]
        if typ == 0x18:
            value: object = struct.unpack(">d", raw)[0]
        elif typ == 0x01:
            value = struct.unpack(">h", raw)[0]
        elif typ == 0x0A:
            value = struct.unpack(">H", raw)[0]
        elif typ == 0x04:
            value = struct.unpack(">i", raw)[0]
        else:
            value = bytes(raw)
        yield MacProperty(base_offset + p, typ, key, channel, value)
        p = q + size


def extension_ranges(source: Union[str, os.PathLike, MacResFile]) -> dict[str, tuple[int, int]]:
    """Extension name -> (first, last) key of the range LabChart gave it, from the file's
    ``McrE`` registry; empty when the file has none."""
    f = source if isinstance(source, MacResFile) else MacResFile(source)
    out: dict[str, tuple[int, int]] = {}
    for c in f.find("McrE"):
        blob = f.read(c)
        for o in range(0, len(blob) - EXTENSION_ENTRY_SIZE + 1, EXTENSION_ENTRY_SIZE):
            first, last, count = struct.unpack_from(">HHH", blob, o)
            n = blob[o + 6]
            if n == 0 or last < first or count != last - first + 1:
                continue
            out.setdefault(blob[o + 7:o + 7 + n].decode("mac_roman", "replace"), (first, last))
    return out


def extension_keys(source: Union[str, os.PathLike, MacResFile]) -> dict[str, int]:
    """The keys of the extension settings read here (names as in ``DEFAULT_EXTENSION_KEYS``)
    in this file: from its registry, or the 2016 keys for an extension it does not list."""
    keys = dict(DEFAULT_EXTENSION_KEYS)
    ranges = extension_ranges(source)
    for prefix, offsets in EXTENSION_KEY_OFFSETS.items():
        first = next((r[0] for name, r in ranges.items() if name.startswith(prefix)), None)
        if first is not None:
            for name, off in offsets.items():
                keys[name] = CHANNEL_KEY_OFFSET + first + off
    return keys


def channel_settings(source: Union[str, os.PathLike, MacResFile]) -> dict[int, MacChannelSettings]:
    """Per-channel settings (1-based keys) of a document or settings file."""
    f = source if isinstance(source, MacResFile) else MacResFile(source)
    chunks = f.settings_chunks()
    if not chunks:
        raise MacChartError("no settings chunks (Mcro 30001/30002) in this file")
    keys = extension_keys(f)
    props: list[MacProperty] = []
    for c in chunks:
        props.extend(iter_properties(f.read(c), c.offset))
    out: dict[int, MacChannelSettings] = {}
    for pr in props:
        if pr.channel == 0:
            continue
        s = out.setdefault(pr.channel, MacChannelSettings(channel=pr.channel))
        s.properties[pr.key] = pr.value
        if pr.key == KEY_TITLE and isinstance(pr.value, str):
            s.title = pr.value
        elif pr.key == KEY_CALCULATION and isinstance(pr.value, str):
            s.calculation = pr.value.rstrip("\u2026")        # stored as the menu label, e.g. "Arithmetic…"
        elif pr.key == keys["arith_expression"] and isinstance(pr.value, str):
            s.expression = pr.value
        elif pr.key == keys["arith_units"] and isinstance(pr.value, str):
            s.units = pr.value
        elif pr.key == keys["spiro_flow_cal"] and isinstance(pr.value, float):
            s.flow_calibration = pr.value
        elif pr.key == keys["spiro_vol_ratio"] and isinstance(pr.value, float):
            s.volume_ratio = pr.value
    # every extension's dialog leaves its own keys behind on a channel; only those of the
    # calculation the channel actually uses mean anything
    source_keys = {"RMS": keys["rms_source"], "Spiro. Flow": keys["spiro_flow_source"],
                   "Spiro. Volume": keys["spiro_vol_source"]}
    for s in out.values():
        key = source_keys.get(s.calculation)
        src = s.properties.get(key) if key is not None else None
        s.source = src if isinstance(src, int) and src > 0 else None
        if s.calculation != "Spiro. Flow":
            s.flow_calibration = None
        if s.calculation != "Spiro. Volume":
            s.volume_ratio = None
    titles = f.channel_titles()
    for i, t in enumerate(titles, start=1):
        s = out.setdefault(i, MacChannelSettings(channel=i))
        if not s.title:
            s.title = t
    return dict(sorted(out.items()))


def document_properties(source: Union[str, os.PathLike, MacResFile]) -> dict[int, object]:
    """Document-level (channel 0) properties, e.g. the channel count under KEY_N_CHANNELS."""
    f = source if isinstance(source, MacResFile) else MacResFile(source)
    out: dict[int, object] = {}
    for c in f.settings_chunks():
        for pr in iter_properties(f.read(c), c.offset):
            if pr.channel == 0:
                out[pr.key] = pr.value
    return out
