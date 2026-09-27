"""Command line interface: ``python -m adichtion`` / ``adichtion``.

Commands
--------
info      records, channels, comments of a file
tree      the container's storages and streams
comments  comment list (all records or one)
export    write a selection of a record as LabChart-style text or CSV
verify    compare decoded samples with a LabChart text export (the oracle)
strings   dump the tagged UTF-16 strings of the Map stream (debugging)
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import numpy as np

from . import __version__
from .chartdata import tagged_strings
from .container import AdichtContainer
from .export import export_file, read_labchart_text
from .file import AdichtFile, Selection


def _open(path: str) -> AdichtFile:
    try:
        return AdichtFile(path)
    except Exception as exc:  # noqa: BLE001 - report and exit
        sys.exit(f"error: {exc}")


def cmd_info(a: argparse.Namespace) -> None:
    with _open(a.file) as f:
        print(f"{a.file}: format v{f.format_version}, {len(f.records)} records, {f.n_channels} channels, "
              f"Chart Data {'.'.join(map(str, f.chart.format_version))}")
        print("channels:")
        for ci in f.channels:
            rate = f.records[0].sample_rate_hz(ci.index) if f.records else float("nan")
            units = ci.units.units if ci.units and ci.units.units else ("V" if ci.range_volts and ci.dtype == "int16" else "")
            print(f"  {ci.index + 1:3d} {ci.title:20} {ci.dtype:8} {rate:8g} Hz  range {ci.range_volts if ci.range_volts is not None else '?':>6} V  units {units}")
        print("records:")
        for r in f.records:
            print(f"  {r.index + 1:3d} {r.start_local:%Y-%m-%d %H:%M:%S}  {r.duration_s:9.2f} s  {r.ticks:>9} ticks @ {r.tick_rate_hz:g} Hz  "
                  f"{len(r.block_ticks)} blocks  {len(r.comments)} comments")


def cmd_tree(a: argparse.Namespace) -> None:
    with AdichtContainer(a.file) as c:
        print(f"{a.file}: format v{c.version}, cluster {c.cluster_size} B, {c.n_clusters} clusters, cluster map at {c.map_first}")
        for e in c.walk():
            kind = "storage" if e.is_storage else "stream "
            print(f"  {kind} {e.path:50} first={e.first:<8} last={e.last:<8} size={e.size:>12,}")


def cmd_comments(a: argparse.Namespace) -> None:
    with _open(a.file) as f:
        recs = f.records if a.record is None else [f.records[a.record - 1]]
        titles = f.channel_titles
        for r in recs:
            for cm in r.comments:
                ch = "all" if cm.channel < 0 else (titles[cm.channel] if cm.channel < len(titles) else f"ch{cm.channel + 1}")
                print(f"record {r.index + 1:3d}  #{cm.id:<4} {cm.tick / r.tick_rate_hz:10.3f} s  {r.start_local.timestamp() + cm.tick / r.tick_rate_hz:.3f}  [{ch}]  {cm.text}")


def _parse_channels(spec: Optional[str], n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo) - 1, int(hi)))
        else:
            out.append(int(part) - 1)
    for c in out:
        if not 0 <= c < n:
            sys.exit(f"error: channel {c + 1} does not exist (file has {n})")
    return out


def cmd_export(a: argparse.Namespace) -> None:
    with _open(a.file) as f:
        rec = f.records[a.record - 1]
        channels = _parse_channels(a.channels, f.n_channels)
        if a.between:
            sel = rec.selection_between(a.between[0], a.between[1])
            if sel is None:
                sys.exit(f"error: no comment starting with {a.between[0]!r} in record {a.record}")
        else:
            start = 0.0 if a.start is None else a.start
            stop = rec.duration_s if a.stop is None else a.stop
            sel = rec.selection(start, stop)
        if a.range_volts:
            for item in a.range_volts:
                ch, v = item.split("=")
                f.set_range_volts(int(ch) - 1, float(v))
        n = export_file(rec, sel, channels, a.output, fmt=a.format, header=not a.no_header,
                        decimal=a.decimal, units=not a.raw, comments=not a.no_comments)
        print(f"wrote {n} rows x {len(channels)} channels ({sel.start / rec.tick_rate_hz:.3f}-{sel.stop / rec.tick_rate_hz:.3f} s of record {a.record}) to {a.output}")


def cmd_verify(a: argparse.Namespace) -> None:
    """Compare a LabChart text export with the decoded data of the same record."""
    header, t, vals, _comments = read_labchart_text(a.export)
    with _open(a.file) as f:
        rec = f.records[a.record - 1]
        channels = _parse_channels(a.channels, f.n_channels)
        if len(channels) != vals.shape[1]:
            sys.exit(f"error: export has {vals.shape[1]} value columns but {len(channels)} channels were selected")
        rate = rec.sample_rate_hz(channels[0])
        start = int(round(t[0] * rate))
        stop = start + len(t)
        ok = True
        for col, ch in enumerate(channels):
            x = rec.read(ch, int(round(start / rec.samples_per_tick(ch))), int(round(stop / rec.samples_per_tick(ch))), units=not a.raw)
            y = vals[:, col]
            m = min(len(x), len(y))
            x, y = x[:m], y[:m]
            if a.fit:
                A = np.vstack([x, np.ones_like(x)]).T
                (k, b), *_ = np.linalg.lstsq(A, y, rcond=None)
                res = np.abs(y - (k * x + b)).max()
                print(f"channel {ch + 1:3d} {f.channel_titles[ch]:20}: y = {k:.9g} * x + {b:.6g}, max residual {res:.3g} over {m} samples")
                ok &= res < 1e-3 * max(1.0, np.abs(y).max())
            else:
                err = np.abs(y - x)
                print(f"channel {ch + 1:3d} {f.channel_titles[ch]:20}: max |diff| {err.max():.3g} over {m} samples (export decimals {header.get('Interval', '?')})")
                ok &= err.max() < 1e-4 * max(1.0, np.abs(y).max())
        print("VERDICT:", "match" if ok else "MISMATCH")
        sys.exit(0 if ok else 1)


def cmd_strings(a: argparse.Namespace) -> None:
    with AdichtContainer(a.file) as c:
        mp = c.read_stream(c.find_stream("Map"))
        for off, s in tagged_strings(mp):
            print(f"{off:#08x}  {s[:120]!r}")


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="adichtion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"adichtion {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="records, channels and comment counts"); s.add_argument("file"); s.set_defaults(fn=cmd_info)
    s = sub.add_parser("tree", help="container storages and streams"); s.add_argument("file"); s.set_defaults(fn=cmd_tree)
    s = sub.add_parser("comments", help="list comments"); s.add_argument("file"); s.add_argument("--record", type=int); s.set_defaults(fn=cmd_comments)
    s = sub.add_parser("strings", help="dump tagged strings of the Map stream"); s.add_argument("file"); s.set_defaults(fn=cmd_strings)

    s = sub.add_parser("export", help="export a selection of one record")
    s.add_argument("file"); s.add_argument("-o", "--output", required=True)
    s.add_argument("--record", type=int, default=1, help="1-based record number (default 1)")
    s.add_argument("--channels", help="1-based channel list, e.g. 1,2,5-7 (default all)")
    s.add_argument("--start", type=float, help="start time in seconds from record start")
    s.add_argument("--stop", type=float, help="stop time in seconds from record start")
    s.add_argument("--between", nargs=2, metavar=("START_TEXT", "STOP_TEXT"), help="select from a comment to the next matching comment")
    s.add_argument("--format", choices=("txt", "csv"), default="txt")
    s.add_argument("--decimal", default=".", help="decimal separator (e.g. ',' for Danish Excel)")
    s.add_argument("--no-header", action="store_true"); s.add_argument("--no-comments", action="store_true")
    s.add_argument("--raw", action="store_true", help="write stored values (ADC counts) instead of converted units")
    s.add_argument("--range-volts", action="append", metavar="CH=VOLTS", help="input range of a channel for v26 files, e.g. 7=10")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("verify", help="compare decoded samples with a LabChart text export of the same record")
    s.add_argument("file"); s.add_argument("export"); s.add_argument("--record", type=int, default=1)
    s.add_argument("--channels", help="channels matching the export's value columns (default all)")
    s.add_argument("--raw", action="store_true", help="compare stored values instead of converted units")
    s.add_argument("--fit", action="store_true", help="allow an unknown linear scaling per channel")
    s.set_defaults(fn=cmd_verify)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":  # pragma: no cover
    main()
