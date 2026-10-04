"""Built-in timing for the viewer (``ADICHTION_PERF=1``).  Qt-free, standard library only.

Set ``ADICHTION_PERF=1`` and the app writes a short timeline to stderr: one line per
measured step (opening the file, each stage of installing it, every coarse pass, every
detail pass in the worker thread, slow frames, GUI stalls) and a summary when the
window closes.  ``ADICHTION_PERF_FILE=path`` also writes the same events as JSON lines
(append mode), so a run can be sent in for analysis.  Without the variable nothing is
timed: :func:`span` returns one shared do-nothing context manager and no timers exist.

**Only numbers leave the process.**  An event is a code-literal name plus numeric
fields (milliseconds, counts, rates); :func:`event` refuses anything else.  File names,
comment texts, dates and subject identifiers can therefore never end up in a log, by
construction and not by care (``tests/test_perf.py`` pins it).

The same module carries the aggregates (count, total, max per event name) that the
summary prints and that ``adichtion bench`` reuses for its tables.
"""
from __future__ import annotations

import json
import numbers
import os
import sys
import threading
import time
from typing import Any, Optional, TextIO

ENV_VAR = "ADICHTION_PERF"
ENV_FILE = "ADICHTION_PERF_FILE"
ENV_EVENTS = "ADICHTION_PERF_EVENTS"

_TRUE = ("1", "true", "yes", "on")
_LEVELS = ("samples", "envelope", "coarse")        # the one non-numeric field allowed: a code-literal LOD level

_lock = threading.Lock()
_io_lock = threading.Lock()                        # serialises the output lines (separate from the aggregates' lock)
_enabled = False
_stream: Optional[TextIO] = None
_file: Optional[TextIO] = None
_t0 = time.perf_counter()
_stats: dict[str, list[float]] = {}                # name -> [count, total_ms, max_ms]
_counts: dict[str, int] = {}                       # name -> how many times (:func:`count`)


def enabled() -> bool:
    return _enabled


def configure(on: bool, path: Optional[str] = None, stream: Optional[TextIO] = None) -> None:
    """Switch the timeline on or off (tests; the app takes the environment at start-up).
    ``stream`` replaces stderr, ``path`` is the optional JSON-lines file."""
    global _enabled, _stream, _file, _t0
    with _lock:
        if _file is not None:
            try:
                _file.close()
            except OSError:
                pass
        _file = None
        _enabled = bool(on)
        _stream = stream
        _stats.clear()
        _counts.clear()
        _t0 = time.perf_counter()
        if on and path:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
                _file = os.fdopen(fd, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                print(f"perf: cannot write {ENV_FILE}: {exc.strerror}", file=sys.stderr)


def configure_from_env(environ=None) -> bool:
    env = os.environ if environ is None else environ
    on = env.get(ENV_VAR, "").strip().lower() in _TRUE
    configure(on, env.get(ENV_FILE) or None)
    return on


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, bool):
            out[k] = int(v)
        elif isinstance(v, numbers.Integral):          # numpy integers too
            out[k] = int(v)
        elif isinstance(v, numbers.Real):
            f = float(v)
            out[k] = round(f, 3) if f == f and f not in (float("inf"), float("-inf")) else None   # JSON has no NaN
        elif k == "level" and v in _LEVELS:
            out[k] = v
        else:
            raise ValueError(f"perf field {k!r} must be a number (got {type(v).__name__}): "
                             "timings carry no names, texts or dates")
    return out


def _emit(name: str, ms: Optional[float], fields: dict[str, Any], quiet: bool) -> None:
    now = time.perf_counter() - _t0
    with _lock:
        if ms is not None:
            s = _stats.setdefault(name, [0, 0.0, 0.0])
            s[0] += 1
            s[1] += ms
            s[2] = max(s[2], ms)
    if quiet:
        return
    parts = [f"{k}={v}" for k, v in fields.items()]
    line = f"perf {now:9.3f}s  {name:<22}" + (f" {ms:9.2f} ms" if ms is not None else " " * 13) + ("  " + " ".join(parts) if parts else "")
    rec: dict[str, Any] = {"t": round(now, 4), "event": name}
    if ms is not None:
        rec["ms"] = round(ms, 3)
    rec.update(fields)
    # the I/O is outside the stats lock (a blocked stderr must not freeze the GUI thread's quiet events), and a
    # failing sink (closed pipe, full disk) switches timing off rather than raising out of the code being measured
    with _io_lock:
        try:
            print(line, file=_stream or sys.stderr, flush=True)
            if _file is not None:
                _file.write(json.dumps(rec) + "\n")
        except (OSError, ValueError):
            _disable_after_io_error()


def _disable_after_io_error() -> None:
    global _enabled
    _enabled = False


def event(name: str, ms: Optional[float] = None, quiet: bool = False, **fields) -> None:
    """Record one event (no-op unless enabled).  ``quiet`` only aggregates (nothing printed, nothing in the
    JSON-lines file), for events too frequent to print one by one (frames, ticks of the stall monitor);
    their count, total and maximum are in the summary."""
    if not _enabled:
        return
    _emit(name, ms, _clean(fields), quiet)


def count(name: str, n: int = 1) -> int:
    """Count one occurrence of ``name`` (no-op unless enabled; nothing is printed per call, the totals are in the
    summary).  For things that matter by how often they happen, not by how long they take: repaints, range signals.
    Returns the new total (0 when timing is off), so a caller can tell how many happened between two points."""
    if not _enabled:
        return 0
    with _lock:
        total = _counts[name] = _counts.get(name, 0) + int(n)
    return total


def counts() -> dict[str, int]:
    """``{name: total}`` of every :func:`count` so far."""
    with _lock:
        return dict(sorted(_counts.items()))


class _Span:
    __slots__ = ("name", "fields", "_t", "_slow_ms")

    def __init__(self, name: str, fields: dict[str, Any], slow_ms: Optional[float] = None):
        self.name, self.fields, self._slow_ms = name, fields, slow_ms

    def set(self, **fields) -> None:
        self.fields.update(fields)

    def __enter__(self) -> "_Span":
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        ms = (time.perf_counter() - self._t) * 1000.0
        # slow_ms None: an ordinary span, printed every time; a number: a frequent one, only aggregated unless it took that long
        event(self.name, ms, quiet=self._slow_ms is not None and ms < self._slow_ms, **self.fields)


class _NullSpan:
    """Shared by every span while timing is off: costs one function call, allocates nothing."""
    __slots__ = ()

    def set(self, **fields) -> None:
        pass

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *exc) -> None:
        pass


_NULL = _NullSpan()


def span(name: str, **fields):
    """``with perf.span("render.coarse", channels=4) as sp: ...`` times the block;
    ``sp.set(blocks=3)`` adds numeric fields learnt along the way."""
    if not _enabled:
        return _NULL
    return _Span(name, dict(fields))


def quiet_span(name: str, slow_ms: float = 10.0, **fields):
    """Like :func:`span`, for steps that run many times per second (a pan or zoom step, one axis, one repaint): every
    call is aggregated (count, total, maximum in the summary) but only those of ``slow_ms`` or more are printed."""
    if not _enabled:
        return _NULL
    return _Span(name, dict(fields), slow_ms)


def events_enabled(environ=None) -> bool:
    """Whether the viewer also times every top-level Qt event of the GUI thread by type
    (``ADICHTION_PERF_EVENTS=0`` switches it off; it is on whenever ``ADICHTION_PERF`` is)."""
    env = os.environ if environ is None else environ
    return env.get(ENV_EVENTS, "1").strip().lower() not in ("0", "false", "no", "off")


def summary() -> dict[str, dict[str, float]]:
    """``{name: {"count", "total_ms", "mean_ms", "max_ms"}}`` of everything recorded so far."""
    with _lock:
        return {k: {"count": int(v[0]), "total_ms": round(v[1], 3), "mean_ms": round(v[1] / v[0], 3) if v[0] else 0.0,
                    "max_ms": round(v[2], 3)} for k, v in sorted(_stats.items())}


def print_summary(cache: Optional[tuple[int, int]] = None) -> None:
    """The summary table at the end of a run; ``cache`` is the file's ``(hits, misses)``
    (a miss is one decoded block)."""
    if not _enabled:
        return
    out = _stream or sys.stderr
    rows = summary()
    try:
        _print_summary(out, rows, cache)
    except (OSError, ValueError):
        _disable_after_io_error()


def _print_summary(out, rows, cache) -> None:
    if cache is not None:
        hits, misses = cache
        event("cache", None, hits=hits, decoded_blocks=misses,
              hit_rate=round(hits / (hits + misses), 3) if hits + misses else 0.0)
    print("perf summary (ms)          count      total       mean        max", file=out)
    for name, s in rows.items():
        print(f"  {name:<22} {s['count']:7d} {s['total_ms']:10.1f} {s['mean_ms']:10.2f} {s['max_ms']:10.2f}", file=out, flush=True)
    tallies = counts()
    if tallies:
        print("perf counts", file=out)
        for name, n in tallies.items():
            print(f"  {name:<22} {n:7d}", file=out, flush=True)


configure_from_env()
