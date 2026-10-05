"""The file's time axis: every block (LabChart's word; ``Record`` in the API) laid
end to end, as LabChart shows them.  Qt-free.

*File time* is seconds from the start of the first block with the blocks
abutting (the wall-clock pauses between them are not part of the axis); the
data panel names the block, the time within it and the clock time instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from ..file import AdichtFile, Selection
from ..model import Comment


@dataclass(frozen=True)
class BlockSpan:
    index: int              # 0-based record index
    start_s: float          # file time of the block's first tick
    duration_s: float
    ticks: int
    tick_rate_hz: float
    start_local: datetime

    @property
    def stop_s(self) -> float:
        return self.start_s + self.duration_s

    @property
    def number(self) -> int:
        """1-based, as LabChart numbers its blocks."""
        return self.index + 1


@dataclass(frozen=True)
class Position:
    """A file time resolved to a block."""
    block: BlockSpan
    tick: int               # tick within the block (clamped to the block)
    file_time_s: float

    @property
    def block_time_s(self) -> float:
        return self.tick / self.block.tick_rate_hz

    @property
    def clock(self) -> datetime:
        return self.block.start_local + timedelta(seconds=self.block_time_s)


@dataclass(frozen=True)
class CommentMark:
    file_time_s: float
    block: BlockSpan
    comment: Comment


class Timeline:
    def __init__(self, f: AdichtFile):
        self.blocks: list[BlockSpan] = []
        t = 0.0
        for r in f.records:
            self.blocks.append(BlockSpan(r.index, t, r.duration_s, r.ticks, r.tick_rate_hz, r.start_local))
            t += r.duration_s
        self.duration_s = t
        self._starts = np.array([b.start_s for b in self.blocks], dtype=np.float64)
        # the same per block as arrays, for the vectorised curves of ``lod`` (index = record index)
        self.starts = self._starts
        self.stops = np.array([b.stop_s for b in self.blocks], dtype=np.float64)
        self.rates = np.array([b.tick_rate_hz for b in self.blocks], dtype=np.float64)
        self.block_ticks = np.array([b.ticks for b in self.blocks], dtype=np.int64)
        self.comments: list[CommentMark] = []
        for b, r in zip(self.blocks, f.records):
            for c in r.comments:
                self.comments.append(CommentMark(b.start_s + c.tick / b.tick_rate_hz, b, c))

    # -- lookups ------------------------------------------------------------------
    @property
    def boundaries(self) -> list[float]:
        """File times where a new block starts (the first block excluded)."""
        return [b.start_s for b in self.blocks[1:]]

    def block_at(self, t: float) -> Optional[BlockSpan]:
        """The block containing file time ``t`` (clamped to the file)."""
        if not self.blocks:
            return None
        i = int(np.searchsorted(self._starts, t, side="right")) - 1
        return self.blocks[max(0, min(i, len(self.blocks) - 1))]

    def locate(self, t: float) -> Optional[Position]:
        b = self.block_at(t)
        if b is None:
            return None
        tick = int(round((t - b.start_s) * b.tick_rate_hz))
        tick = max(0, min(tick, b.ticks - 1))
        return Position(b, tick, b.start_s + tick / b.tick_rate_hz)

    def file_time(self, record_index: int, tick: float) -> float:
        b = self.blocks[record_index]
        return b.start_s + tick / b.tick_rate_hz

    def selections(self, t0: float, t1: float) -> list[Selection]:
        """The tick ranges of every block overlapped by file times ``[t0, t1]``."""
        if t1 < t0:
            t0, t1 = t1, t0
        out = []
        for b in self.blocks:
            lo, hi = max(t0, b.start_s), min(t1, b.stop_s)
            if hi <= lo and not (t0 == t1 and b.start_s <= t0 <= b.stop_s):
                continue
            start = max(0, int(round((lo - b.start_s) * b.tick_rate_hz)))
            stop = min(b.ticks, int(round((hi - b.start_s) * b.tick_rate_hz)))
            if stop > start:
                out.append(Selection(b.index, start, stop))
        return out

    def block_interval(self, index: int) -> tuple[float, float]:
        """The file times a block spans: its first tick to its end."""
        b = self.blocks[index]
        return b.start_s, b.stop_s

    def comment_interval(self, i: int) -> tuple[float, float]:
        """The interval that belongs to comment ``i`` (an index into ``comments``): from the comment to
        just before the next comment later in the file (one tick of the block that comment is in),
        or to the end of the file when it is the last one.  Comments at the same time as this one are
        not "next"; they share its interval."""
        t0 = self.comments[i].file_time_s
        later = [m for m in self.comments if m.file_time_s > t0]
        if not later:
            return t0, self.duration_s
        nxt = min(later, key=lambda m: m.file_time_s)
        return t0, max(t0, nxt.file_time_s - 1.0 / nxt.block.tick_rate_hz)

    def describe(self, t: float) -> str:
        """``Block 3 · 12.345 s · 13:45:12.345`` for a file time."""
        pos = self.locate(t)
        if pos is None:
            return ""
        return f"Block {pos.block.number} · {pos.block_time_s:.3f} s · {pos.clock:%H:%M:%S.%f}"[:-3]
