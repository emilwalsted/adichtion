"""``adichtion bench FILE``: what the viewer's operations cost on one file, without the GUI.

Measures, on the file's own data: opening, the whole-file y range from the block index,
the curve passes the plots run (:func:`adichtion.ui.lod.curve_for_range`, coarse and
detailed, cold and warm, for the whole file, 10 minutes, 60 seconds and 5 seconds),
the decoding of single blocks, cursor look-ups, and optionally the export of a
selection.  Qt-free; the result is a plain dict (``--json``) and a short table.

**Only numbers are reported.**  The report carries the file size, counts of blocks,
channels and records, sampling rates and timings, never the file name, titles, comment
texts, dates or anything else that could identify a recording or a subject
(``tests/test_perf.py`` pins it).

Cold means adichtion's own caches (decoded blocks, filled envelopes) were cleared first, not
the operating system's file cache; warm is the same call again.  Times are wall-clock milliseconds of one run on the machine the
command runs on, which is the point: the numbers are Emil's own Mac with a real file.
"""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Callable, Optional

import numpy as np

from .file import AdichtFile

#: ``(label, seconds)``; ``None`` seconds is the whole file.  Spans longer than the file are clamped.
VIEWS = (("full", None), ("10min", 600.0), ("60s", 60.0), ("5s", 5.0))
DEFAULT_WIDTH = 1200               # pixel buckets per curve, about a channel plot's width


def _ms(fn: Callable[[], Any]) -> tuple[float, Any]:
    t = time.perf_counter()
    out = fn()
    return (time.perf_counter() - t) * 1000.0, out


def _round(x: float) -> float:
    return round(x, 3)


def run_bench(path: str, width: int = DEFAULT_WIDTH, export_seconds: Optional[float] = None,
              decode_blocks: int = 40, cursor_lookups: int = 200) -> dict:
    """Run every measurement and return the report (see the module docstring)."""
    from .export import export_selections
    from .ui import lod
    from .ui.timeline import Timeline

    size = os.path.getsize(path)
    t_open, f = _ms(lambda: AdichtFile(path))
    try:
        timeline = Timeline(f)
        channels = [c for c in range(f.n_channels) if f.has_data(c)]
        total_blocks = sum(len(r.block_ticks) for r in f.records)
        report: dict[str, Any] = {
            "file": {"bytes": size, "kind": f.kind, "records": len(f.records), "channels": f.n_channels,
                     "channels_with_data": len(channels), "duration_s": _round(timeline.duration_s),
                     "storage_blocks": total_blocks,
                     "tick_rates_hz": sorted({float(r.tick_rate_hz) for r in f.records}),
                     "sample_rates_hz": sorted({float(f.records[0].sample_rate_hz(c)) for c in channels}) if f.records else [],
                     "comments": sum(len(r.comments) for r in f.records)},
            "open_ms": _round(t_open),
        }
        if not channels or not f.records:
            report.update(global_range_ms=0.0, curves=[], decode={}, cursor={}, export=None)
            return report

        for c in channels:                                  # one throw-away pass: imports and numpy warm-up are not the file's cost
            lod.curve_for_range(f, timeline, c, 0.0, min(1.0, timeline.duration_s), 64, coarse=True)
        f.clear_cache()
        ms, _ = _ms(lambda: [lod.global_range(f, c) for c in channels])
        report["global_range_ms"] = _round(ms)
        report["global_range_ms_per_channel"] = _round(ms / len(channels))

        # the curve passes: all channels, as a render pass draws them
        curves = []
        seen: set[float] = set()
        mid = timeline.duration_s / 2.0
        for label, seconds in VIEWS:
            span = timeline.duration_s if seconds is None else min(seconds, timeline.duration_s)
            if round(span, 6) in seen:
                continue                                    # a file shorter than this view: already measured
            seen.add(round(span, 6))
            t0 = max(0.0, mid - span / 2.0)
            t1 = min(timeline.duration_s, t0 + span)
            row: dict[str, Any] = {"view": label, "span_s": _round(t1 - t0)}
            for mode, coarse in (("coarse", True), ("detail", False)):
                f.clear_cache()
                cold, _ = _ms(lambda: [lod.curve_for_range(f, timeline, c, t0, t1, width, coarse=coarse) for c in channels])
                _hits, decoded = f.cache_stats()
                warm, out = _ms(lambda: [lod.curve_for_range(f, timeline, c, t0, t1, width, coarse=coarse) for c in channels])
                row[f"{mode}_cold_ms"] = _round(cold)
                row[f"{mode}_warm_ms"] = _round(warm)
                row[f"{mode}_decoded_blocks_cold"] = decoded
                row[f"{mode}_points"] = int(sum(len(o[0]) for o in out))
                row[f"{mode}_level"] = sorted({o[2] for o in out})[0] if out else ""
            curves.append(row)
        report["curves"] = curves
        report["curve_width_px"] = width

        # decoding single blocks of the first channel with data, cold
        f.clear_cache()
        ch = channels[0]
        keys = [(r.index, b) for r in f.records for b in range(len(r.block_ticks))]
        step = max(1, len(keys) // max(1, decode_blocks))
        picked = keys[::step][:decode_blocks]
        t = time.perf_counter()
        n_samples = 0
        for ri, bi in picked:
            n_samples += len(f.decode_block(ri, bi, ch))
        el = (time.perf_counter() - t) * 1000.0
        report["decode"] = {"channel_rate_hz": float(f.records[0].sample_rate_hz(ch)), "blocks": len(picked), "total_ms": _round(el),
                            "ms_per_block": _round(el / max(1, len(picked))),
                            "samples_per_block": int(n_samples / max(1, len(picked)))}

        # cursor look-ups: the value of every channel at a time, as the plots show it
        rng = np.random.default_rng(1)
        times = rng.uniform(0.0, timeline.duration_s, cursor_lookups)
        f.clear_cache()
        cold, _ = _ms(lambda: [lod.value_at(f, timeline, c, float(t)) for t in times for c in channels])
        warm, _ = _ms(lambda: [lod.value_at(f, timeline, c, float(t)) for t in times for c in channels])
        report["cursor"] = {"lookups": cursor_lookups, "channels": len(channels),
                            "cold_ms_per_lookup": _round(cold / cursor_lookups),
                            "warm_ms_per_lookup": _round(warm / cursor_lookups)}

        report["export"] = None
        if export_seconds:
            rec = max(f.records, key=lambda r: r.ticks)
            span = min(float(export_seconds), rec.duration_s)
            start = max(0.0, rec.duration_s / 2.0 - span / 2.0)
            sel = rec.selection(start, start + span)
            f.clear_cache()
            with tempfile.TemporaryDirectory(prefix="adichtion-bench-") as tmp:
                out_path = os.path.join(tmp, "export.txt")
                ms, rows = _ms(lambda: export_selections(f, [sel], channels, out_path, fmt="txt"))
                out_bytes = os.path.getsize(out_path)
            report["export"] = {"seconds": _round(span), "rows": int(rows), "channels": len(channels),
                                "ms": _round(ms), "bytes": out_bytes}
        report["cache_after"] = dict(zip(("hits", "misses"), f.cache_stats()))
        return report
    finally:
        f.close()


def format_table(r: dict) -> str:
    """The report as a short text table."""
    fi = r["file"]
    lines = [f"file       {fi['bytes'] / 1e6:.1f} MB, {fi['kind']}, {fi['records']} records, {fi['storage_blocks']} storage blocks, "
             f"{fi['channels_with_data']}/{fi['channels']} channels with data, {fi['duration_s']:.1f} s, "
             f"{fi['comments']} comments",
             f"           tick {', '.join(f'{x:g}' for x in fi['tick_rates_hz'])} Hz, channel rates {', '.join(f'{x:g}' for x in fi['sample_rates_hz'])} Hz",
             f"open       {r['open_ms']:.1f} ms"]
    if not r.get("curves"):
        return "\n".join(lines + ["(no channel with data: nothing more to measure)"])
    lines.append(f"y range    {r['global_range_ms']:.1f} ms for all channels from the block index "
                 f"({r['global_range_ms_per_channel']:.2f} ms per channel)")
    lines.append(f"curves     all channels, {r['curve_width_px']} px, ms: cold = caches cleared, warm = second call")
    lines.append(f"  {'view':<7}{'span s':>9} | {'coarse cold':>11} {'warm':>8} | {'detail cold':>11} {'warm':>8} {'blocks':>7} {'points':>9} {'level':>9}")
    for c in r["curves"]:
        lines.append(f"  {c['view']:<7}{c['span_s']:>9.1f} | {c['coarse_cold_ms']:>11.1f} {c['coarse_warm_ms']:>8.1f} | "
                     f"{c['detail_cold_ms']:>11.1f} {c['detail_warm_ms']:>8.1f} {c['detail_decoded_blocks_cold']:>7d} "
                     f"{c['detail_points']:>9d} {c['detail_level']:>9}")
    d = r["decode"]
    lines.append(f"decode     {d['ms_per_block']:.2f} ms per block ({d['blocks']} blocks, {d['samples_per_block']} samples each), cold")
    c = r["cursor"]
    lines.append(f"cursor     {c['cold_ms_per_lookup']:.2f} ms per look-up of {c['channels']} channels cold, {c['warm_ms_per_lookup']:.3f} ms warm")
    e = r.get("export")
    if e:
        lines.append(f"export     {e['seconds']:.0f} s x {e['channels']} channels: {e['rows']} rows, {e['bytes'] / 1e6:.1f} MB in {e['ms']:.0f} ms")
    if r.get("pan"):
        from .ui.panbench import format_pan             # Qt-free until run_pan is called
        lines.append(format_pan(r["pan"]))
    return "\n".join(lines)
