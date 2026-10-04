"""``adichtion bench FILE --pan``: drive the real viewer window through a pan and zoom scenario and report what the
GUI thread spent on it (the ``ADICHTION_PERF`` events, see :mod:`adichtion.perf` and :mod:`adichtion.ui.perfmon`).

The scenario is what a hand on the wheel does: ``steps`` sideways pan steps of a twentieth of the view, then
``steps // 2`` zoom steps, in and out, each followed by a pause of ``pause_ms`` of event-loop time (never a tight
``processEvents`` loop: it would starve the worker thread and inflate every time, see ``tests/ui/helpers.py``).  The
steps go through the same code as the mouse (``ChannelViewBox.pan_fraction`` and ``scaleBy`` inside the input
counters), not through synthesised mouse events.  A window is shown for the few seconds it takes; headless, set
``QT_QPA_PLATFORM=offscreen`` (then the numbers are the software renderer's and only good for before/after comparisons).

This module imports no Qt until :func:`run_pan` is called, so ``adichtion.bench`` stays Qt-free.  **Only numbers are
reported**: counts, milliseconds and the file's size and duration, never a name, title, comment or date.
"""
from __future__ import annotations

import io
import os
import time
from typing import Any

from .. import perf

DEFAULT_STEPS = 50
DEFAULT_PAUSE_MS = 40
DEFAULT_VIEW_S = 600.0                  # the view the scenario starts from: 10 minutes (a quarter of the file when that is shorter)


def _platform() -> str:
    """The Qt platform plugin, from a fixed list (the environment variable is free text)."""
    p = os.environ.get("QT_QPA_PLATFORM", "").split(":")[0].lower()
    return p if p in ("offscreen", "cocoa", "windows", "xcb", "wayland", "minimal") else "native"


def _open_quietly(w, path: str) -> None:
    """Open the file; a failure raises instead of showing the window's modal error box (it would hang a headless run)."""
    from ..file import AdichtFile

    AdichtFile(path).close()                                # raises on a bad file, with no dialog
    if not w.open_file_sync(path):
        raise ValueError("the window could not open the file")


def _pump(app, seconds: float) -> None:
    """Event-loop time with sleeps in between (a worker's results arrive as queued signals)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def _wait(app, cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


def run_pan(path: str, steps: int = DEFAULT_STEPS, pause_ms: int = DEFAULT_PAUSE_MS,
            view_s: float = DEFAULT_VIEW_S, width: int = 1600, height: int = 1000) -> dict[str, Any]:
    """Open ``path`` in a real :class:`MainWindow`, run the scenario and return the report (see the module docstring)."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    from . import theme
    from .channel_stack import _input_span

    saved = perf.snapshot()                                  # the caller's own ADICHTION_PERF setup, put back at the end
    w = None
    try:
        perf.configure(True, stream=io.StringIO())          # events on, nothing printed; the table below is the report
        from .app import _application_class                 # after configure: the class times events only when perf is on
        had_app = QApplication.instance() is not None       # an existing application has no notify hook: no ev.* rows then
        app = QApplication.instance() or _application_class()(["adichtion-bench"])
        theme.apply_theme(app)
        from .main_window import MainWindow

        w = MainWindow()
        w.resize(width, height)
        w.show()
        _pump(app, 0.2)
        _open_quietly(w, path)
        stack = w.stack
        if stack.timeline is None or not stack.visible_channels:
            raise ValueError("the file has no channel with data to show")
        duration = stack.timeline.duration_s
        span = min(view_s, duration / 4.0)                # a view with room to pan: at most a quarter of a short file
        t0 = max(0.0, duration / 2.0 - span / 2.0)
        stack.set_range(t0, t0 + span)
        _wait(app, lambda: all(stack.plots[c].level for c in stack.visible_channels), 60.0)
        _pump(app, 0.5)                                    # the first detail passes arrive and are drawn

        perf.configure(True, stream=io.StringIO())          # from here on: only the scenario is counted
        master = stack._master()
        vb = master.vb
        pause = max(0.0, pause_ms / 1000.0)
        direction = 1.0
        wall = time.perf_counter()
        for _ in range(max(1, steps)):
            x0, x1 = stack.view_range()
            if direction > 0 and x1 >= duration - 1e-6:
                direction = -1.0
            elif direction < 0 and x0 <= 1e-6:
                direction = 1.0
            vb.pan_fraction(0.05 * direction)
            _pump(app, pause)
        pan_wall = time.perf_counter() - wall
        zoom_steps = max(0, steps // 2)
        wall = time.perf_counter()
        for i in range(zoom_steps):
            x0, x1 = stack.view_range()
            factor = 0.8 if i % 2 == 0 else 1.25
            with _input_span("wheel"):
                vb.scaleBy(x=factor, center=pg.Point((x0 + x1) / 2.0, 0.0))
            _pump(app, pause)
        zoom_wall = time.perf_counter() - wall
        _pump(app, 0.5)                                    # let the last detail passes land
        events, counts = perf.summary(), perf.counts()
        report = {
            "scenario": {"pan_steps": max(1, steps), "zoom_steps": zoom_steps, "pause_ms": int(pause_ms),
                         "start_view_s": round(span, 3), "window_px": [int(width), int(height)],
                         "channels_shown": len(stack.visible_channels),
                         "pan_wall_ms_per_step": round(pan_wall * 1000.0 / max(1, steps), 3),
                         "zoom_wall_ms_per_step": round(zoom_wall * 1000.0 / max(1, zoom_steps), 3) if zoom_steps else 0.0,
                         "platform": _platform(), "event_types_timed": int(not had_app)},
            "events": events,
            "counts": counts,
        }
        return report
    finally:
        if w is not None:
            w.close()
            w.deleteLater()
            app.processEvents()
        perf.restore(saved)


def format_pan(r: dict[str, Any]) -> str:
    """The pan report as a table: the steps the GUI thread took, the heaviest first, then the counts."""
    sc = r["scenario"]
    lines = [f"pan        {sc['pan_steps']} pan + {sc['zoom_steps']} zoom steps, {sc['pause_ms']} ms apart, from a "
             f"{sc['start_view_s']:.0f} s view, {sc['channels_shown']} channels, window {sc['window_px'][0]}x{sc['window_px'][1]} ({sc['platform']})",
             f"           wall per step: pan {sc['pan_wall_ms_per_step']:.1f} ms, zoom {sc['zoom_wall_ms_per_step']:.1f} ms "
             f"(includes the {sc['pause_ms']} ms pause)",
             f"  {'event':<34}{'count':>7} {'total ms':>10} {'mean':>8} {'max':>8}"]
    rows = sorted(r["events"].items(), key=lambda kv: -kv[1]["total_ms"])
    for name, s in rows[:40]:
        lines.append(f"  {name:<34}{s['count']:>7d} {s['total_ms']:>10.1f} {s['mean_ms']:>8.2f} {s['max_ms']:>8.2f}")
    if len(rows) > 40:
        lines.append(f"  ... {len(rows) - 40} more events in the JSON report")
    if r["counts"]:
        lines.append("  counts: " + ", ".join(f"{k} {v}" for k, v in r["counts"].items()))
    return "\n".join(lines)
