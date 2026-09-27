"""Export selections of a record to text/CSV.

Two flavours:

* ``"txt"``: LabChart-style tab-separated text with the familiar header lines
  (``Interval=``, ``ExcelDateTime=``, ``ChannelTitle=``, ``Range=``,
  ``UnitName=``) and comments in a trailing ``#* text`` column – the format
  LabChart itself writes with *File > Export*, so downstream tools (e.g.
  RespMech) can read either.
* ``"csv"``: plain comma-separated values with a one-line header.

Values are what :meth:`adichtion.Record.read` returns (units when possible),
formatted with ``%.7g`` like LabChart's 7 significant digits.
"""
from __future__ import annotations

import io
import os
from typing import Iterable, Optional, Sequence, TextIO

import numpy as np

from .file import Record, Selection


def _excel_datetime(dt) -> float:
    """Excel serial date (days since 1899-12-30) of a datetime."""
    from datetime import datetime, timezone
    epoch = datetime(1899, 12, 30, tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) - epoch).total_seconds() / 86400.0


def _fmt(v: float, decimal: str) -> str:
    s = f"{v:.7g}"
    return s.replace(".", decimal) if decimal != "." else s


def write_text(record: Record, selection: Selection, channels: Sequence[int], out: TextIO, *,
               fmt: str = "txt", header: bool = True, decimal: str = ".", units: bool = True,
               comments: bool = True) -> int:
    """Write the selection to ``out``.  Returns the number of data rows written."""
    f = record.file
    ch_infos = [f.channel_info(c) for c in channels]
    rates = {record.sample_rate_hz(c) for c in channels}
    if len(rates) != 1:
        raise ValueError("channels with different sampling rates cannot share one table; export them separately")
    rate = rates.pop()
    t = record.time(selection.start, selection.stop, channels[0])
    cols = [record.read(c, selection.start, selection.stop, units=units) for c in channels]
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
        out.write("time_s," + ",".join(f"{ci.title} [{record.value_label(c)}]".replace(",", " ") for ci, c in zip(ch_infos, channels)) + "\n")
    # comments inside the selection, keyed by sample row
    by_row: dict[int, list[str]] = {}
    if comments:
        for cm in record.comments:
            if selection.start <= cm.tick < selection.stop:
                row = record.tick_to_sample(cm.tick - selection.start, channels[0])
                by_row.setdefault(row, []).append(cm.text)
    buf = io.StringIO()
    for i in range(n):
        parts = [_fmt(t[i], decimal)] + [_fmt(float(col[i]), decimal) for col in cols]
        if i in by_row:
            parts.append(("#* " if fmt == "txt" else "") + " | ".join(by_row[i]))
        buf.write(sep.join(parts) + "\n")
        if i % 50000 == 49999:
            out.write(buf.getvalue())
            buf = io.StringIO()
    out.write(buf.getvalue())
    return n


def export_file(record: Record, selection: Selection, channels: Sequence[int], path: str | os.PathLike, **kw) -> int:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        return write_text(record, selection, channels, fh, **kw)


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
