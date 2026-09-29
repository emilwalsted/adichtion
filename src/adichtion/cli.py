"""Command line interface: ``adichtion <command> ...`` (``adichtion`` alone starts the app).

Commands
--------
info      records, channels, comments of a file
tree      the container's storages and streams
comments  comment list (all records or one)
export    write a selection of a record as LabChart-style text or CSV
verify    compare decoded samples with a LabChart text export (the oracle)
check     decode every block and compare with the min/max LabChart stored
videos    the video clips recorded with the records
infer     recover the definitions of calculated channels from the data
macsettings  channel settings (calculated-channel definitions) of a LabChart 7 for Mac document or settings file
strings   dump the tagged UTF-16 strings of the Map stream (debugging)
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import numpy as np

from . import __version__
from .chartdata import tagged_strings
from .container import AdichtContainer
from .export import export_selections, read_labchart_text, upsampling_notice, whole_file
from .file import AdichtFile, Selection


def _open(path: str) -> AdichtFile:
    try:
        return AdichtFile(path)
    except Exception as exc:  # noqa: BLE001 - report and exit
        sys.exit(f"error: {exc}")


def cmd_info(a: argparse.Namespace) -> None:
    with _open(a.file) as f:
        if f.kind == "mac":
            n_missing = sum(1 for c in range(f.n_channels) if not f.has_data(c))
            print(f"{a.file}: {f.format_label}, {len(f.records)} records, {f.n_channels} channels"
                  + (f" ({n_missing} calculated channels not stored: LabChart for Mac computed them when drawing)" if n_missing else "")
                  + (f", {len(f.movie_files)} movies ({sum(1 for v in f.videos if v.stamped)} found and timed; "
                     f"see `adichtion videos`)" if f.movie_files else ""))
        else:
            print(f"{a.file}: format v{f.format_version}, {len(f.records)} records, {f.n_channels} channels, "
                  f"Chart Data {'.'.join(map(str, f.chart.format_version))}, Map parsed by {f.parse_mode}"
                  + (f", {sum(1 for v in f.videos if v.has_video)} videos in '{os.path.basename(f.movies_folder)}'" if f.videos else ""))
        print("channels (units of record 1):")
        rec0 = f.records[0] if f.records else None
        for ci in f.channels:
            if not f.has_data(ci.index):
                d = f.channel_definition(ci.index)
                print(f"  {ci.index + 1:3d} {ci.title:20} not stored" + (f"  (calculated: {d})" if d else "  (no data)"))
                continue
            rate = rec0.sample_rate_hz(ci.index) if rec0 else float("nan")
            u = ci.units
            conv = ""
            if u is not None:
                conv = (f"{u.slope:.6g} * V + {u.offset:.6g} -> {u.units or '?'}" if u.enabled else "off")
                if u.calibration[0]:
                    conv += f"  [{u.calibration[0]} -> {u.calibration[2]}, {u.calibration[1]} -> {u.calibration[3]}]"
            rng = f"{ci.range_volts:g} V" if ci.range_volts is not None else ("-" if ci.dtype == "float32" else "?")
            lsb = f"{u.lsb:.4g}" if u is not None and u.has_lsb else "?"
            label = rec0.value_label(ci.index) if rec0 else ""
            print(f"  {ci.index + 1:3d} {ci.title:20} {ci.dtype:8} {rate:8g} Hz  range {rng:>7}  lsb {lsb:>9}  values in {label or '(stored units)':10}  conversion {conv}")
        print("records:")
        for r in f.records:
            vid = ""
            if r.video is not None:
                vid = f"  video {r.video.file_name or '-'}" + (f" (offset {r.video.offset_s:.3f} s)" if r.video.has_video else "")
            print(f"  {r.index + 1:3d} {r.start_local:%Y-%m-%d %H:%M:%S}  {r.duration_s:9.2f} s  {r.ticks:>9} ticks @ {r.tick_rate_hz:g} Hz  "
                  f"{len(r.block_ticks)} blocks  {len(r.comments)} comments{vid}")


def cmd_videos(a: argparse.Namespace) -> None:
    with _open(a.file) as f:
        if f.kind == "mac":
            if not f.movie_files:
                print("this document names no movie files")
                return
            print(f"movies folder: {f.movies_folder}" + ("" if os.path.isdir(f.movies_folder) else "  (not found)"))
            for v in f.videos:
                path = os.path.join(f.movies_folder, v.file_name)
                if v.stamped:
                    state = f"record start at movie time {v.offset_s:.3f} s (from the frame stamps)"
                elif os.path.exists(path):
                    state = "cannot be read (not downloaded, or not a LabChart movie)"
                else:
                    state = "not found"
                print(f"  record {v.record_index + 1:3d}  {v.file_name}  {state}")
            return
        if not f.videos:
            print("no video stream in this document")
            return
        print(f"movies folder: {f.movies_folder}" + ("" if os.path.isdir(f.movies_folder) else "  (not found)"))
        for v in f.videos:
            rec = f.records[v.record_index] if v.record_index < len(f.records) else None
            path = f.records[v.record_index].video_file if rec else None
            print(f"  record {v.record_index + 1:3d}  {v.file_name or '-':16}  record start at video time {v.offset_s:8.3f} s  "
                  f"video clock {v.start_s:10.3f} .. {v.stop_s:10.3f} s  file {'present' if path else ('missing' if v.has_video else '-')}")


def cmd_check(a: argparse.Namespace) -> None:
    """Decode every block and compare with the min/max LabChart stored in the Map stream."""
    with _open(a.file) as f:
        if f.parse_mode != "sequential":
            sys.exit("the Map stream was not parsed by the full grammar; nothing to check against")
        walk = f.walk_subblocks()
        if [(s.offset, s.body_size) for s in walk] != [(s.offset, s.body_size) for s in f.index.subblocks]:
            sys.exit("MISMATCH: the block index of the Map stream and the Blocks_0 chain disagree")
        n = bad = unchecked = 0
        for rec in f.records:
            for ch in range(f.n_channels):
                if not f.has_data(ch):
                    continue
                # a Mac document's index holds the envelope of the signal before decimation:
                # a decimated channel's samples lie inside it (MAC_FORMAT.md §3)
                inside = f.kind == "mac" and f.index.decimation(rec.index, ch) > 1
                for b, blk in enumerate(f.channel_record(rec.index, ch).blocks):
                    x = f.decode_block(rec.index, b, ch)
                    if not len(x):
                        continue
                    if np.isnan(blk.min) or np.isnan(blk.max):
                        unchecked += 1
                        continue
                    mn, mx = float(x.min()), float(x.max())
                    tol = 0.0 if x.dtype == np.int32 else 1e-6 * max(1.0, abs(mn), abs(mx))
                    n += 1
                    wrong = (mn < blk.min - tol or mx > blk.max + tol) if inside else \
                        (abs(mn - blk.min) > tol or abs(mx - blk.max) > tol)
                    if wrong:
                        bad += 1
                        print(f"record {rec.index + 1} channel {ch + 1} block {b + 1}: decoded {mn:g}..{mx:g}, stored {blk.min:g}..{blk.max:g}")
        print(f"{n} blocks checked, {bad} mismatches" + (f", {unchecked} blocks without a stored min/max" if unchecked else ""))
        print("VERDICT:", "match" if bad == 0 else "MISMATCH")
        sys.exit(0 if bad == 0 else 1)


def cmd_tree(a: argparse.Namespace) -> None:
    from .macdoc import is_mac_document
    if is_mac_document(a.file):
        from .macchart import MacResFile
        m = MacResFile(a.file)
        print(f"{a.file}: ADInst Data Res File, {len(m.chunks)} chunks, table of contents at {m.toc_offset}")
        for c in sorted(m.chunks, key=lambda c: c.offset):
            print(f"  {c.type!r:8} id {c.id:<6} offset {c.offset:>12,} length {c.length:>12,}")
        return
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
        channels = _parse_channels(a.channels, f.n_channels)
        missing = [c + 1 for c in channels if not f.has_data(c)]
        if a.channels and missing:
            sys.exit(f"error: channel(s) {', '.join(map(str, missing))} are not stored in this file "
                     "(calculated channels of a LabChart for Mac document)")
        channels = [c for c in channels if f.has_data(c)]
        if a.range_volts:
            for item in a.range_volts:
                ch, v = item.split("=")
                f.set_range_volts(int(ch) - 1, float(v))
        fmt = a.format or {"csv": "csv", "xlsx": "xlsx"}.get(os.path.splitext(a.output)[1].lstrip(".").lower(), "txt")
        if a.all_records:
            selections = whole_file(f)
            what = f"all {len(selections)} records"
        else:
            if not 1 <= a.record <= len(f.records):
                sys.exit(f"error: record {a.record} does not exist (file has {len(f.records)})")
            rec = f.records[a.record - 1]
            if a.between:
                sel = rec.selection_between(a.between[0], a.between[1])
                if sel is None:
                    sys.exit(f"error: no comment starting with {a.between[0]!r} in record {a.record}")
            else:
                start = 0.0 if a.start is None else a.start
                stop = rec.duration_s if a.stop is None else a.stop
                sel = rec.selection(start, stop)
            selections = [sel]
            what = f"{sel.start / rec.tick_rate_hz:.3f}-{sel.stop / rec.tick_rate_hz:.3f} s of record {a.record}"
        try:
            n = export_selections(f, selections, channels, a.output, fmt=fmt, header=not a.no_header,
                                  decimal=a.decimal, units=not a.raw, comments=not a.no_comments)
            note = upsampling_notice(f, selections, channels)
        except (ValueError, RuntimeError) as exc:
            sys.exit(f"error: {exc}")
        print(f"wrote {n} rows x {len(channels)} channels ({what}) to {a.output}")
        if note:
            print(f"note: {note}")


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
    from .macdoc import is_mac_document
    if is_mac_document(a.file):
        sys.exit("a LabChart for Mac document has no Map stream; `adichtion tree` lists its chunks")
    with AdichtContainer(a.file) as c:
        mp = c.read_stream(c.find_stream("Map"))
        for off, s in tagged_strings(mp):
            print(f"{off:#08x}  {s[:120]!r}")


def cmd_infer(a: argparse.Namespace) -> None:
    """Test the standard LabChart calculations against the calculated channels of one record."""
    from .infer import infer_record

    with _open(a.file) as f:
        if not 1 <= a.record <= len(f.records):
            sys.exit(f"error: record {a.record} does not exist (file has {len(f.records)})")
        rec = f.records[a.record - 1]
        channels = _parse_channels(a.channels, f.n_channels) if a.channels else None
        n = min(rec.ticks, a.samples)
        print(f"record {a.record}: {rec.ticks} ticks @ {rec.tick_rate_hz:g} Hz, analysing the first {n} "
              f"({n / rec.tick_rate_hz:.1f} s); channel numbers are 1-based")
        results = infer_record(rec, channels, length=a.samples, only_float=not a.all)
        if not results and f.kind == "mac":
            print("a LabChart for Mac document stores the definitions of its calculated channels, not their "
                  "data: see `adichtion macsettings` (or `adichtion info`)")
        elif not results:
            print("no calculated (float32) channels in this file; use --all or --channels to test raw channels")
        for c, hyps in results.items():
            print(f"Ch{c + 1} {f.channel_titles[c]!r} ({f.index.channel_dtype(c)}, {rec.value_label(c) or 'stored units'}):")
            if not hyps:
                print("    no hypothesis matched (not a standard calculation of the other channels, or its inputs are not in the file)")
            for h in hyps[: a.top]:
                print(f"    {h}")


def cmd_macsettings(a: argparse.Namespace) -> None:
    """Channel settings of a LabChart 7 for Mac document or settings file."""
    from .macchart import KEY_N_CHANNELS, MacChartError, MacResFile, channel_settings, document_properties

    try:
        f = MacResFile(a.file)
        settings = channel_settings(f)
    except (OSError, MacChartError) as exc:
        sys.exit(f"error: {exc}")
    kind = "document (with data)" if f.is_document else "settings file"
    n_doc = document_properties(f).get(KEY_N_CHANNELS)
    print(f"{a.file}: LabChart for Mac {kind}, {len(f.chunks)} chunks" + (f", {n_doc} channels" if n_doc else ""))
    hidden = 0
    for ch, s in settings.items():
        if n_doc and ch > n_doc and not a.verbose:     # settings left over from channels the document no longer has
            hidden += 1
            continue
        print(f"  {ch:3d} {s.title!r:24} {s.describe()}")
        if a.verbose:
            for key, value in sorted(s.properties.items()):
                print(f"        {key:#06x} = {value!r}")
    if hidden:
        print(f"  ({hidden} more channel settings beyond channel {n_doc}, left over from earlier setups; --verbose shows them)")


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="adichtion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"adichtion {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="records, channels and comment counts"); s.add_argument("file"); s.set_defaults(fn=cmd_info)
    s = sub.add_parser("tree", help="container storages and streams"); s.add_argument("file"); s.set_defaults(fn=cmd_tree)
    s = sub.add_parser("comments", help="list comments"); s.add_argument("file"); s.add_argument("--record", type=int); s.set_defaults(fn=cmd_comments)
    s = sub.add_parser("strings", help="dump tagged strings of the Map stream"); s.add_argument("file"); s.set_defaults(fn=cmd_strings)
    s = sub.add_parser("videos", help="video clips of the records (LabChart video capture)"); s.add_argument("file"); s.set_defaults(fn=cmd_videos)
    s = sub.add_parser("check", help="decode every block and compare with the min/max stored in the file"); s.add_argument("file"); s.set_defaults(fn=cmd_check)

    s = sub.add_parser("export", help="export a selection of one record")
    s.add_argument("file"); s.add_argument("-o", "--output", required=True)
    s.add_argument("--record", type=int, default=1, help="1-based record number (default 1)")
    s.add_argument("--channels", help="1-based channel list, e.g. 1,2,5-7 (default all)")
    s.add_argument("--start", type=float, help="start time in seconds from record start")
    s.add_argument("--stop", type=float, help="stop time in seconds from record start")
    s.add_argument("--between", nargs=2, metavar=("START_TEXT", "STOP_TEXT"), help="select from a comment to the next matching comment")
    s.add_argument("--format", choices=("txt", "csv", "xlsx"), help="default: from the output file's extension (txt otherwise)")
    s.add_argument("--all-records", action="store_true", help="export every record (LabChart block) of the file")
    s.add_argument("--decimal", default=".", help="decimal separator (e.g. ',' for Danish Excel)")
    s.add_argument("--no-header", action="store_true"); s.add_argument("--no-comments", action="store_true")
    s.add_argument("--raw", action="store_true", help="write stored values (ADC counts) instead of converted units")
    s.add_argument("--range-volts", action="append", metavar="CH=VOLTS", help="override the input range of a channel, e.g. 7=10 (rarely needed)")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("verify", help="compare decoded samples with a LabChart text export of the same record")
    s.add_argument("file"); s.add_argument("export"); s.add_argument("--record", type=int, default=1)
    s.add_argument("--channels", help="channels matching the export's value columns (default all)")
    s.add_argument("--raw", action="store_true", help="compare stored values instead of converted units")
    s.add_argument("--fit", action="store_true", help="allow an unknown linear scaling per channel")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("infer", help="recover the definitions of calculated channels from the data of one record")
    s.add_argument("file"); s.add_argument("--record", type=int, default=1, help="1-based record number (default 1)")
    s.add_argument("--channels", help="1-based channels to explain (default: every float32 channel)")
    s.add_argument("--samples", type=int, default=200_000, help="ticks analysed from the record start (default 200000)")
    s.add_argument("--top", type=int, default=3, help="hypotheses listed per channel (default 3)")
    s.add_argument("--all", action="store_true", help="also test the raw (int16) channels")
    s.set_defaults(fn=cmd_infer)

    s = sub.add_parser("macsettings", help="channel settings of a LabChart 7 for Mac document or settings file")
    s.add_argument("file"); s.add_argument("-v", "--verbose", action="store_true", help="every property of every channel")
    s.set_defaults(fn=cmd_macsettings)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":  # pragma: no cover
    main()
