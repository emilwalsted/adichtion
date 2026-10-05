"""Background decoding while the user looks at the data.  Qt-free.

Two low-priority jobs share one worker thread (a :class:`~adichtion.ui.workers.JobRunner` of their own, so
they never queue in front of a render):

* **Prefetch.**  After a detail pass the blocks just before and after the decoded range are decoded (and
  cached), so a pan or a zoom out lands on a warm cache.  :meth:`Warmer.request_prefetch` replaces the
  request of a channel; the newest view wins.
* **Warm-up.**  The rest of the file is walked in a low-priority sweep, visible channels first and, within
  a channel, the blocks nearest the last view first, leaving the min/max summaries
  (``AdichtFile.summary_store``) that make the first full-file view and the first zoom out quick.  A warm-up
  block is decoded with ``cache=False``: only its summary is kept, so a file bigger than the block cache does
  not push out what the view is using.

Both jobs yield to the user.  :class:`Activity` is touched by every interaction and every render; a job
waits until it has been quiet for ``IDLE_S`` (prefetch: the shorter ``PREFETCH_IDLE_S``) before it decodes
the next block, and sleeps after each block so it uses at most ``DUTY`` of one core (the GUI thread and the
worker share the GIL, so a job that never paused would itself cause the stalls it is meant to avoid).
Everything stops at once when the job is stale (a new file, closing) or the warmer is cancelled.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .. import perf
from ..file import AdichtFile
from .timeline import Timeline

IDLE_S = 0.4                    # warm-up decodes only after this long without a move, a click or a render
PREFETCH_IDLE_S = 0.12          # the neighbours of the view are wanted sooner
DUTY = 0.3                      # at most this share of one core, averaged over a block and its pause
MAX_PAUSE_S = 0.25
MIN_PAUSE_S = 0.002             # always hands the GIL over, even after a very short block
PREFETCH_SPAN = 0.5             # screen widths decoded on each side of the range a detail pass decoded
PREFETCH_MAX_BLOCKS = 32        # per side and channel
PROGRESS_INTERVAL_S = 0.25
MAX_FAILURES = 5                # a file that keeps failing to decode is left alone
POLL_S = 0.02                   # how often a waiting job looks at the activity and at stale()
IDLE_POLL_S = 0.25              # how often an idle job looks for new work


class Activity:
    """When the user last did something (a thread-safe float): touched on the GUI thread, read by the worker."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last = float("-inf")

    def touch(self) -> None:
        self._last = self._clock()

    def quiet_for(self, seconds: float) -> bool:
        return self._clock() - self._last >= seconds


@dataclass(frozen=True)
class ChannelBlocks:
    """The storage blocks of one channel over every record, in file order, with their file times."""

    record: np.ndarray          # record index of each block
    block: np.ndarray           # block number within the record
    start: np.ndarray           # file time of the block's first tick (s), ascending
    stop: np.ndarray            # file time where it ends (s), ascending

    def __len__(self) -> int:
        return len(self.record)

    def in_range(self, t0: float, t1: float) -> slice:
        """The blocks that overlap file times ``[t0, t1]``."""
        i0 = int(np.searchsorted(self.stop, t0, side="right"))
        i1 = int(np.searchsorted(self.start, t1, side="left"))
        return slice(i0, max(i0, i1))


def channel_blocks(f: AdichtFile, tl: Timeline, channel: int) -> ChannelBlocks:
    tab = f.block_table(channel)
    rec = tab.record
    idx = np.arange(len(rec), dtype=np.int64)
    block = idx - tab.first[rec] if len(rec) else idx
    rate = tl.rates[rec] if len(rec) else np.zeros(0)
    start = tl.starts[rec] + tab.tick0 / rate if len(rec) else np.zeros(0)
    stop = tl.starts[rec] + tab.tick1 / rate if len(rec) else np.zeros(0)
    return ChannelBlocks(rec, block, start, stop)


class Warmer:
    """The background decoder of one open file.  ``run`` is the job (``fn(stale, report)``) of a
    :class:`~adichtion.ui.workers.JobRunner`; everything else is called from the GUI thread."""

    def __init__(self, f: AdichtFile, tl: Timeline, activity: Optional[Activity] = None, warm: bool = True,
                 idle_s: float = IDLE_S, prefetch_idle_s: float = PREFETCH_IDLE_S, duty: float = DUTY):
        self.f = f
        self.tl = tl
        self.activity = activity if activity is not None else Activity()
        self.idle_s = idle_s
        self.prefetch_idle_s = prefetch_idle_s
        self.duty = min(1.0, max(0.01, duty))
        self.channels = [c for c in range(f.n_channels) if f.has_data(c)]
        self.decoded = 0                              # blocks decoded by this warmer (both jobs)
        self._warm = bool(warm)
        self._lock = threading.Lock()                 # the requests below
        self._step = threading.Lock()                 # held while one block is decoded
        self._wake = threading.Event()
        self._cancelled = False
        self._failures = 0
        self._finished = False
        self._requests: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}   # channel -> (before, after)
        self._queue: dict[int, deque] = {}            # worker only: what is left of each channel's request
        self._view: Optional[tuple[float, list[int]]] = None
        self._order_dirty = True
        self._maps: dict[int, ChannelBlocks] = {}
        self._order: Optional[tuple[np.ndarray, np.ndarray]] = None     # (channel, flat block index) of the sweep
        self._pos = 0
        self._total: Optional[int] = None

    # -- requests (GUI thread) ----------------------------------------------------------------------------
    @property
    def warm(self) -> bool:
        return self._warm

    def set_warm(self, on: bool) -> None:
        self._warm = bool(on)
        self._wake.set()

    def request_prefetch(self, channel: int, before: tuple[float, float], after: tuple[float, float]) -> None:
        """Decode (and cache) the blocks of the file-time ranges ``before`` and ``after`` the view for ``channel`` when
        the user is quiet, the ones next to the view first; replaces the channel's earlier request."""
        with self._lock:
            self._requests[channel] = (before, after)
        self._wake.set()

    def set_view(self, centre_s: float, channels: list[int]) -> None:
        """Where the user is looking: the warm-up starts there, with ``channels`` (the visible ones, in order) first."""
        with self._lock:
            self._view = (float(centre_s), [c for c in channels if c in self.channels])
            self._order_dirty = True
        self._wake.set()

    def cancel(self) -> None:
        """Stop for good, and return only when no block is being decoded (the file may be closed next)."""
        self._cancelled = True
        self._wake.set()
        with self._step:
            pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # -- progress ------------------------------------------------------------------------------------------
    def progress(self) -> tuple[int, int]:
        """``(blocks with a summary, blocks of every channel with data)``: the truth, whoever decoded them, and O(1)
        (the file counts its summaries as they are made)."""
        if self._total is None:
            self._total = sum(len(self._blocks(c)) for c in self.channels)
        return min(self.f.summarised_blocks(), self._total), self._total

    # -- the job (worker thread) --------------------------------------------------------------------------
    def run(self, stale, report=None) -> int:
        """Decode until stale or cancelled; returns the number of blocks decoded.  Posts ``(done, total)`` through
        ``report`` now and then while warming (and once more when the sweep is complete)."""
        last_report = 0.0
        complete = False
        while not self._stopped(stale):
            if self._has_prefetch():
                if self._wait_quiet(stale, self.prefetch_idle_s) is False:
                    break
                item = self._pop_prefetch()
                if item is not None:
                    self._decode(*item, cache=True, kind="prefetch")
                continue
            if self._warm and not complete:
                quiet = self._wait_quiet(stale, self.idle_s, self._has_prefetch)    # before looking for the next block too:
                if quiet is False:                                                    # re-ordering the sweep is work as well
                    break
                if quiet is None:
                    continue                                       # a prefetch request came in: it goes first
                item = self._next_warm()
                if item is None:
                    complete = True
                    done, total = self.progress()
                    self._finished = done >= total        # nothing left to sweep: a later move needs no second look
                    if report is not None:
                        report((done, total))
                    continue
                self._decode(*item, cache=False, kind="warm")
                now = time.monotonic()
                if report is not None and now - last_report >= PROGRESS_INTERVAL_S:
                    last_report = now
                    report(self.progress())
                continue
            self._wake.wait(IDLE_POLL_S)
            self._wake.clear()
            if complete and not self._finished:
                with self._lock:
                    complete = not self._order_dirty      # the user went somewhere else: sweep again from there
        if report is not None and self._failures >= MAX_FAILURES:
            report((0, 0))                        # the sweep gave up: the status bar must not keep the last percentage
        return self.decoded

    def _stopped(self, stale) -> bool:
        return self._cancelled or self._failures >= MAX_FAILURES or bool(stale())

    def _wait_quiet(self, stale, idle_s: float, interrupt: Optional[Callable[[], bool]] = None) -> Optional[bool]:
        """Wait until the user has been quiet for ``idle_s``: True when so, False if the job must stop first, and None
        when ``interrupt()`` became true meanwhile (the longer wait of the sweep must not starve a prefetch request)."""
        while not self._stopped(stale):
            if interrupt is not None and interrupt():
                return None
            if self.activity.quiet_for(idle_s):
                return True
            time.sleep(POLL_S)
        return False

    def _blocks(self, channel: int) -> ChannelBlocks:
        m = self._maps.get(channel)
        if m is None:
            m = self._maps[channel] = channel_blocks(self.f, self.tl, channel)
        return m

    def _has_prefetch(self) -> bool:
        with self._lock:
            return bool(self._requests) or any(self._queue.values())

    def _resolve(self, channel: int, before: tuple[float, float], after: tuple[float, float]) -> list[tuple[int, int]]:
        """``(record, block)`` of the blocks in the two ranges, nearest the view first, the sides alternating."""
        blocks = self._blocks(channel)
        b = blocks.in_range(*before)
        a = blocks.in_range(*after)
        near_before = np.arange(b.start, b.stop)[::-1][:PREFETCH_MAX_BLOCKS]       # the block next to the view first
        near_after = np.arange(a.start, a.stop)[:PREFETCH_MAX_BLOCKS]
        out: list[tuple[int, int]] = []
        for k in range(max(len(near_before), len(near_after))):
            for side in (near_after, near_before):
                if k < len(side):
                    i = int(side[k])
                    out.append((int(blocks.record[i]), int(blocks.block[i])))
        return out

    def _pop_prefetch(self) -> Optional[tuple[int, int, int]]:
        """The next block to prefetch as ``(channel, record, block)``; the newest request of a channel replaces
        what is left of its older one."""
        with self._lock:
            fresh, self._requests = self._requests, {}
        for channel, (before, after) in fresh.items():
            self._queue[channel] = deque(self._resolve(channel, before, after))
        for channel in list(self._queue):
            q = self._queue[channel]
            if q:
                record, block = q.popleft()
                return channel, record, block
            del self._queue[channel]
        return None

    def _next_warm(self) -> Optional[tuple[int, int, int]]:
        """The next block the sweep still has to decode, or None when the whole file has its summaries."""
        with self._lock:
            dirty, view = self._order_dirty, self._view
            self._order_dirty = False
        if self._order is None or dirty:
            self._build_order(view)
        chans, idx = self._order
        while self._pos < len(idx):
            channel, i = int(chans[self._pos]), int(idx[self._pos])
            self._pos += 1
            blocks = self._blocks(channel)
            record, block = int(blocks.record[i]), int(blocks.block[i])
            store = self.f.summary_store(record, channel)
            if store is not None and store.have[block]:
                continue                                    # the view (or a prefetch) decoded it already
            return channel, record, block
        return None

    def _build_order(self, view: Optional[tuple[float, list[int]]]) -> None:
        """Visible channels first (in the order given), then the others; within a channel the blocks nearest the view's
        centre first.  Only what the sweep has not passed yet is ordered again (all of it the first time), so a
        re-order after a move costs in proportion to the work left, not to the size of the file."""
        centre, shown = view if view is not None else (0.0, [])
        if self._order is None:
            parts = [(c, np.arange(len(self._blocks(c)), dtype=np.int64)) for c in self.channels]
        else:
            chans, idx = self._order
            chans, idx = chans[self._pos:], idx[self._pos:]
            parts = [(c, idx[chans == c]) for c in self.channels]
        rank = shown + [c for c in self.channels if c not in shown]
        by_channel = dict(parts)
        chan_parts, idx_parts = [], []
        for c in rank:
            idx = by_channel.get(c)
            if idx is None or not len(idx):
                continue
            blocks = self._blocks(c)
            mid = 0.5 * (blocks.start[idx] + blocks.stop[idx])
            chan_parts.append(np.full(len(idx), c, dtype=np.int64))
            idx_parts.append(idx[np.argsort(np.abs(mid - centre), kind="stable")])
        self._order = (np.concatenate(chan_parts), np.concatenate(idx_parts)) if idx_parts else \
            (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
        self._pos = 0

    def _decode(self, channel: int, record: int, block: int, cache: bool, kind: str) -> None:
        started = time.perf_counter()
        with self._step:
            if self._cancelled:
                return
            try:
                self.f.decode_block(record, block, channel, cache=cache)
                self.decoded += 1
            except Exception:                               # noqa: BLE001 - background work never takes the viewer down
                self._failures += 1
                return
        took = time.perf_counter() - started
        perf.count(f"{kind}.block")
        # a pause in proportion to the work: at most `duty` of one core, and always a hand-over of the GIL
        time.sleep(min(MAX_PAUSE_S, max(MIN_PAUSE_S, took * (1.0 - self.duty) / self.duty)))
