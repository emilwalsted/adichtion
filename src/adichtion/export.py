"""Export selections of one or more records to text, CSV or Excel.

Three flavours:

* ``"txt"``: LabChart-style tab-separated text with the familiar header lines
  (``Interval=``, ``ExcelDateTime=``, ``ChannelTitle=``, ``Range=``,
  ``UnitName=``) and comments in a trailing ``#* text`` column – the format
  LabChart itself writes with *File > Export*, so downstream tools (e.g.
  RespMech) can read either.  Several records go into one file with the header
  lines repeated at every record start, as LabChart does for several blocks.
* ``"csv"``: plain comma-separated values with a one-line header; with several
  records a leading ``block`` column says which one a row belongs to.
* ``"xlsx"``: an Excel workbook (``openpyxl``), one sheet per record.

Values are what :meth:`adichtion.Record.read` returns (units when possible),
formatted with ``%.7g`` like LabChart's 7 significant digits.  Time is
seconds from the record start.

A table holds its channels at their common sampling rate.  When the channels
chosen have different rates (the slow inputs of a LabChart for Mac document),
the table has a row per tick and the slower channels are up-sampled as
LabChart's own export does (:meth:`adichtion.Record.read_ticks`);
:func:`upsampling_notice` says so in a sentence for the user.
"""
from __future__ import annotations

import io
import os
from typing import Iterable, Optional, Sequence, TextIO

import numpy as np

from .file import AdichtFile, Record, Selection

#: Rows an Excel worksheet can hold (2007+ format); the xlsx writer refuses more.
XLSX_MAX_ROWS = 1_048_576


def _excel_datetime(dt) -> float:
    """Excel serial date (days since 1899-12-30) of a datetime."""
    from datetime import datetime, timezone
    epoch = datetime(1899, 12, 30, tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) - epoch).total_seconds() / 86400.0


def _fmt(v: float, decimal: str) -> str:
    s = f"{v:.7g}"
    return s.replace(".", decimal) if decimal != "." else s


def table_plan(record: Record, channels: Sequence[int]) -> tuple[float, dict[int, float]]:
    """The sampling rate of one table of ``channels`` from ``record``, and the channels it
    up-samples (channel -> its own rate).  Channels that share a rate keep it; with several
    rates the table has a row per tick and the slower channels are up-sampled the way
    LabChart's own export does (:meth:`Record.read_ticks`)."""
    missing = [c + 1 for c in channels if not record.file.has_data(c)]
    if missing:
        raise ValueError(f"channel(s) {', '.join(map(str, missing))} are not stored in this file "
                         "(calculated channels of a LabChart for Mac document)")
    rates = {c: record.sample_rate_hz(c) for c in channels}
    if len(set(rates.values())) <= 1:
        return next(iter(rates.values())), {}
    return record.tick_rate_hz, {c: r for c, r in rates.items() if r != record.tick_rate_hz}


def upsampled_channels(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int]) -> dict[int, float]:
    """The channels an export of ``selections`` up-samples (channel -> its own rate)."""
    out: dict[int, float] = {}
    for sel in selections:
        out.update(table_plan(f.records[sel.record], channels)[1])
    return out


def _channel_list(chs: Sequence[int]) -> str:
    """1-based channel numbers with runs joined: ``7-10, 12``."""
    parts, run = [], []
    for c in sorted(chs):
        if run and c == run[-1] + 1:
            run.append(c)
            continue
        if run:
            parts.append(f"{run[0]}-{run[-1]}" if len(run) > 2 else ", ".join(map(str, run)))
        run = [c]
    if run:
        parts.append(f"{run[0]}-{run[-1]}" if len(run) > 2 else ", ".join(map(str, run)))
    return ", ".join(parts)


def upsampling_notice(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int]) -> Optional[str]:
    """What an export of ``selections`` up-samples, in a sentence for the user; None when
    every table keeps its channels' own rate."""
    up = upsampled_channels(f, selections, channels)
    if not up:
        return None
    targets = sorted({f.records[s.record].tick_rate_hz for s in selections})
    by_rate: dict[float, list[int]] = {}
    for c, r in up.items():
        by_rate.setdefault(r, []).append(c + 1)
    what = "; ".join(f"channel{'s' if len(chs) > 1 else ''} {_channel_list(chs)} from {r:g} Hz"
                     for r, chs in sorted(by_rate.items(), reverse=True))
    return (f"Up-sampled to {' and '.join(f'{t:g}' for t in targets)} Hz so that all channels share one table: {what} "
            "(linear interpolation between the stored samples, rounded to whole ADC counts, "
            "as LabChart's own export does).")


def _table(record: Record, selection: Selection, channels: Sequence[int], units: bool):
    """``(time, columns, rate, row_of_tick)`` of one table (:func:`table_plan`)."""
    rate, up = table_plan(record, channels)
    start, stop = max(0, selection.start), min(selection.stop, record.ticks)
    if up:
        t = np.arange(start, stop) / record.tick_rate_hz
        cols = [record.read_ticks(c, start, stop, units=units) for c in channels]
        return t, cols, rate, (lambda tick: tick - start)
    c0 = channels[0]
    t = record.time(start, stop, c0)
    cols = [record.read(c, start, stop, units=units) for c in channels]
    s0 = record.tick_to_sample(start, c0)
    return t, cols, rate, (lambda tick: record.tick_to_sample(tick, c0) - s0)


def table_rows(record: Record, selection: Selection, channels: Sequence[int]) -> int:
    """Rows the table of ``channels`` for ``selection`` has (:func:`table_plan`)."""
    _rate, up = table_plan(record, channels)
    start, stop = max(0, selection.start), min(selection.stop, record.ticks)
    if up:
        return max(0, stop - start)
    return record.tick_to_sample(stop, channels[0]) - record.tick_to_sample(start, channels[0])


def _comment_rows(record: Record, selection: Selection, row_of_tick) -> dict[int, list[str]]:
    """Comments inside the selection, keyed by the row they fall on (the first row at or
    after the comment)."""
    by_row: dict[int, list[str]] = {}
    for cm in record.comments:
        if selection.start <= cm.tick < selection.stop:
            by_row.setdefault(row_of_tick(cm.tick), []).append(cm.text)
    return by_row


def write_text(record: Record, selection: Selection, channels: Sequence[int], out: TextIO, *,
               fmt: str = "txt", header: bool = True, decimal: str = ".", units: bool = True,
               comments: bool = True, block_column: bool = False) -> int:
    """Write the selection to ``out``.  Returns the number of data rows written.
    ``block_column`` (CSV only) prefixes every row with the 1-based record number."""
    f = record.file
    channels = list(channels)
    if not channels:
        raise ValueError("no channels selected")
    ch_infos = [f.channel_info(c) for c in channels]
    t, cols, rate, row_of_tick = _table(record, selection, channels, units)
    n = len(t)
    sep = "\t" if fmt == "txt" else ","
    if fmt == "txt" and header:
        out.write(f"Interval=\t{_fmt(1.0 / rate, decimal)} s\n")
        out.write(f"ExcelDateTime=\t{_excel_datetime(record.start_utc):.10g}\t{record.start_local:%Y-%m-%d %H:%M:%S.%f}\n")
        out.write("TimeFormat=\tStartOfBlock\n")
        out.write("DateFormat=\tY-M-D\n")
        out.write("ChannelTitle=\t" + "\t".join(ci.title for ci in ch_infos) + "\n")
        out.write("Range=\t" + "\t".join(f"{_fmt(ci.range_volts, decimal)} V" if ci.range_volts else "" for ci in ch_infos) + "\n")
        out.write("UnitName=\t" + "\t".join(record.value_label(c) for c in channels) + "\n")
    elif fmt == "csv" and header:
        out.write(("block," if block_column else "") + "time_s,"
                  + ",".join(f"{ci.title} [{record.value_label(c)}]".replace(",", " ") for ci, c in zip(ch_infos, channels))
                  + (",comment" if comments else "") + "\n")
    by_row = _comment_rows(record, selection, row_of_tick) if comments else {}
    prefix = f"{record.index + 1}," if (block_column and fmt == "csv") else ""
    buf = io.StringIO()
    for i in range(n):
        parts = [_fmt(t[i], decimal)] + [_fmt(float(col[i]), decimal) for col in cols]
        if i in by_row:
            text = " | ".join(by_row[i])
            parts.append("#* " + text if fmt == "txt" else text.replace(",", ";"))
        buf.write(prefix + sep.join(parts) + "\n")
        if i % 50000 == 49999:
            out.write(buf.getvalue())
            buf = io.StringIO()
    out.write(buf.getvalue())
    return n


def export_file(record: Record, selection: Selection, channels: Sequence[int], path: str | os.PathLike, **kw) -> int:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        return write_text(record, selection, channels, fh, **kw)


def whole_file(f: AdichtFile) -> list[Selection]:
    """One selection per record covering everything."""
    return [Selection(r.index, 0, r.ticks) for r in f.records]


def export_selections(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int],
                      path: str | os.PathLike, fmt: str = "txt", **kw) -> int:
    """Export several selections (of one or more records) into one file.

    ``fmt`` is ``"txt"``, ``"csv"`` or ``"xlsx"``; the keyword arguments are those of
    :func:`write_text` (``header``, ``decimal``, ``units``, ``comments``).  Returns
    the number of data rows written.  Text and CSV exports of several records repeat
    the header per record (txt) or carry a ``block`` column (csv), as described in
    the module docstring.
    """
    selections = [s for s in selections if s.stop > s.start]
    if not selections:
        raise ValueError("nothing to export: the selection is empty")
    if fmt == "xlsx":
        return write_xlsx(f, selections, channels, path, **kw)
    if fmt not in ("txt", "csv"):
        raise ValueError(f"unknown export format {fmt!r}")
    header = kw.pop("header", True)
    rows = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i, sel in enumerate(selections):
            rec = f.records[sel.record]
            rows += write_text(rec, sel, channels, fh, fmt=fmt,
                               header=header and (fmt == "txt" or i == 0),
                               block_column=len(selections) > 1, **kw)
    return rows


def write_xlsx(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int], path: str | os.PathLike, *,
               header: bool = True, decimal: str = ".", units: bool = True, comments: bool = True) -> int:
    """Excel workbook with one sheet per selection ("Block 3", or "Block 3 (2)" for a
    second selection of the same record).  Numbers are written as numbers (``decimal``
    is ignored: Excel applies its own locale).  Refuses a selection longer than a
    worksheet (:data:`XLSX_MAX_ROWS`)."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:                           # pragma: no cover - depends on the environment
        raise RuntimeError("Excel export needs the openpyxl package (pip install openpyxl)") from exc
    channels = list(channels)
    if not channels:
        raise ValueError("no channels selected")
    for sel in selections:
        rec = f.records[sel.record]
        n = table_rows(rec, sel, channels)
        if n + 1 > XLSX_MAX_ROWS:
            raise ValueError(f"block {sel.record + 1}: {n} rows do not fit an Excel sheet "
                             f"({XLSX_MAX_ROWS - 1} data rows); export a shorter selection or use text/CSV")
    wb = Workbook(write_only=True)
    rows = 0
    seen: dict[int, int] = {}
    for sel in selections:
        rec = f.records[sel.record]
        t, cols, rate, row_of_tick = _table(rec, sel, channels, units)
        k = seen.get(sel.record, 0) + 1
        seen[sel.record] = k
        ws = wb.create_sheet(f"Block {sel.record + 1}" + (f" ({k})" if k > 1 else ""))
        if header:
            ws.append(["Block", sel.record + 1, "start", rec.start_local.strftime("%Y-%m-%d %H:%M:%S"),
                       "selection start (s)", sel.start / rec.tick_rate_hz, "interval (s)", 1.0 / rate])
            ws.append(["time_s"] + [f"{f.channel_info(c).title} [{rec.value_label(c)}]" for c in channels]
                      + (["comment"] if comments else []))
        by_row = _comment_rows(rec, sel, row_of_tick) if comments else {}
        for i in range(len(t)):
            row = [float(t[i])] + [float(col[i]) for col in cols]
            if comments:
                row.append(" | ".join(by_row[i]) if i in by_row else None)
            ws.append(row)
        rows += len(t)
    wb.save(os.fspath(path))
    return rows


def read_labchart_text(path: str | os.PathLike) -> tuple[dict[str, str], np.ndarray, np.ndarray, list[tuple[int, str]]]:
    """Read a LabChart text export (with or without header).

    Returns ``(header, time, values, comments)`` where ``values`` is a 2-D array
    (rows x channels) and ``comments`` is a list of ``(row, text)``.  Decimal
    commas are accepted.
    """
    header: dict[str, str] = {}
    times: list[float] = []
    rows: list[list[float]] = []
    comments: list[tuple[int, str]] = []
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if "=" in line.split("\t", 1)[0]:
                key, _, val = line.partition("=")
                header[key.strip()] = val.strip("\t ").strip()
                continue
            parts = line.split("\t")
            vals: list[float] = []
            for p in parts:
                if p.startswith("#"):
                    comments.append((len(rows), p.lstrip("#* ").strip()))
                    break
                try:
                    vals.append(float(p.replace(",", ".")))
                except ValueError:
                    break
            if not vals:
                continue
            times.append(vals[0])
            rows.append(vals[1:])
    width = max((len(r) for r in rows), default=0)
    arr = np.full((len(rows), width), np.nan)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    return header, np.asarray(times), arr, comments
