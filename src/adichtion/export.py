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
* ``"tsv"``: a plain tab-separated table with one header line, the layout RespMech's ``.txt``
  loader reads (the LabChart header lines of ``"txt"`` are not a table); same ``block`` column
  as CSV with several records.
* ``"xlsx"``: an Excel workbook (``openpyxl``), one sheet per record.
* ``"mat"``: a MATLAB level-5 file (:mod:`adichtion.matfile`), one ``data_block<N>`` matrix with
  the channels as rows per exported block, the layout of RespMech's ``windows`` MATLAB variant.

RespMech reads ``csv``, ``tsv``, ``xlsx`` and ``mat`` exports as they are (not ``txt``, whose
LabChart header lines are not a table).  CSV with a decimal comma is ``;``-separated, as
RespMech and European Excel expect: a comma both as decimal and as separator cannot be read by
anything.

Values are what :meth:`adichtion.Record.read` returns (units when possible),
formatted with ``%.7g`` like LabChart's 7 significant digits.  Time is
seconds from the record start.

A table holds its channels at their common sampling rate.  When the channels
chosen have different rates (the slow inputs of a LabChart for Mac document),
the table has a row per tick and the slower channels are up-sampled as
LabChart's own export does (:meth:`adichtion.Record.read_ticks`);
:func:`upsampling_notice` says so in a sentence for the user.

Text and CSV are formatted a block of rows at a time (one ``%``-format over the whole
block, not one call per value) and Excel sheets are streamed as XML; both give exactly the
bytes (text) and cell values (Excel) the value-by-value writers gave.  An export writes to
``<name>.part`` next to the target and renames it when it is complete, so a cancelled or
failed export leaves no half-written file (and an existing file of that name is untouched).
:func:`export_selections` takes ``progress(rows_done, rows_total)`` and ``stale()`` (return
True to cancel: :class:`ExportCancelled` is raised) for the viewer's progress dialog.
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import re
import zipfile
from bisect import bisect_left
from typing import Callable, Iterable, Iterator, Optional, Sequence, TextIO

import numpy as np

from .file import AdichtFile, Record, Selection

#: Rows an Excel worksheet can hold (2007+ format); the xlsx writer refuses more.
XLSX_MAX_ROWS = 1_048_576

#: Rows formatted (and checked for a cancel request) at a time.
CHUNK_ROWS = 50_000


class ExportCancelled(Exception):
    """The export was cancelled (``stale()`` returned True); nothing was written to the target."""


def _cell(text: str, sep: str, repl: str = " ") -> str:
    """Free text (a channel title, a comment) as one field: the separator would split the row
    and a line break or tab would break it, so each becomes ``repl`` (a space; a comment in a
    comma-separated file gets a semicolon, as it always has)."""
    return text.replace(sep, repl).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _excel_datetime(dt) -> float:
    """Excel serial date (days since 1899-12-30) of a datetime."""
    from datetime import datetime, timezone
    epoch = datetime(1899, 12, 30, tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) - epoch).total_seconds() / 86400.0


#: The formats written as one delimited line per row.
_DELIMITED = ("txt", "csv", "tsv")
#: Every format :func:`export_selections` knows.
FORMATS = ("txt", "csv", "tsv", "xlsx", "mat")


def _separator(fmt: str, decimal: str) -> str:
    """The field separator of a delimited format: a tab for the text flavours, a comma for CSV
    (a semicolon when the decimal is a comma, so the two never collide)."""
    if fmt in ("txt", "tsv"):
        return "\t"
    return ";" if decimal == "," else ","


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
    return max(0, record.tick_to_sample(stop, channels[0]) - record.tick_to_sample(start, channels[0]))


def _comment_rows(record: Record, selection: Selection, row_of_tick) -> dict[int, list[str]]:
    """Comments inside the selection, keyed by the row they fall on (the first row at or
    after the comment)."""
    by_row: dict[int, list[str]] = {}
    for cm in record.comments:
        if selection.start <= cm.tick < selection.stop:
            by_row.setdefault(row_of_tick(cm.tick), []).append(cm.text)
    return by_row


class _Progress:
    """The rows written so far across all tables of one export, its cancel check and its callback."""

    def __init__(self, total: int, stale: Optional[Callable[[], bool]] = None,
                 progress: Optional[Callable[[int, int], None]] = None):
        self.total, self.done = total, 0
        self._stale, self._progress = stale, progress

    def check(self) -> None:
        if self._stale is not None and self._stale():
            raise ExportCancelled("export cancelled")

    def advance(self, rows: int) -> None:
        self.done += rows
        if self._progress is not None:
            self._progress(self.done, self.total)


@contextlib.contextmanager
def _partial_file(path: str | os.PathLike) -> Iterator[str]:
    """The path to write an export to: ``<path>.part``, renamed over ``path`` when the block
    finishes and removed when it raises (a cancelled or failed export leaves no half-written file
    and does not touch an existing one).  A target that exists but is not a regular file (a
    device, a pipe) is written to directly."""
    real = os.path.realpath(os.fspath(path))
    if os.path.exists(real) and not os.path.isfile(real):
        yield real
        return
    tmp = real + ".part"
    try:
        yield tmp
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _strings(values: np.ndarray, spec: str, repeats: bool = True) -> np.ndarray:
    """``spec % v`` for every value, as an object array of ``str``.  The values of a recording are
    ADC counts times a scale, so a block of rows holds far fewer distinct values than rows: with
    ``repeats`` each distinct bit pattern (not each distinct value: ``-0.0`` keeps its sign) is
    formatted once.  The time column never repeats and passes ``repeats=False``."""
    values = np.ascontiguousarray(values, dtype=np.float64)
    text = np.empty(len(values), dtype=object)
    if repeats:
        uniq, inverse = np.unique(values.view(np.int64), return_inverse=True)
        if 2 * len(uniq) <= len(values):
            text = np.empty(len(uniq), dtype=object)
            text[:] = list(map(spec.__mod__, uniq.view(np.float64).tolist()))
            return text[inverse.reshape(-1)]
    text[:] = list(map(spec.__mod__, values.tolist()))
    return text


def _text_rows(t: np.ndarray, cols: Sequence[np.ndarray], lo: int, hi: int, prefix: str, sep: str,
               decimal: str) -> str:
    """Rows ``[lo, hi)`` of a table as text, every value as ``%.7g``: the values are formatted
    column by column (:func:`_strings`) and the rows put together by one ``%`` over the whole
    block, which is what makes this several times faster than a call per value (and the same
    characters)."""
    k = 1 + len(cols)
    cells = np.empty((hi - lo, k), dtype=object)
    cells[:, 0] = _strings(t[lo:hi], "%.7g", repeats=False)
    for j, col in enumerate(cols):
        cells[:, j + 1] = _strings(col[lo:hi], "%.7g")
    row = prefix + sep.join(["%s"] * k) + "\n"
    text = (row * (hi - lo)) % tuple(cells.ravel().tolist())
    return text if decimal == "." else text.replace(".", decimal)


def write_text(record: Record, selection: Selection, channels: Sequence[int], out: TextIO, *,
               fmt: str = "txt", header: bool = True, decimal: str = ".", units: bool = True,
               comments: bool = True, block_column: bool = False, job: Optional[_Progress] = None) -> int:
    """Write the selection to ``out``.  Returns the number of data rows written.
    ``block_column`` (CSV only) prefixes every row with the 1-based record number.
    (``job`` is :func:`export_selections`' progress and cancel plumbing.)"""
    f = record.file
    channels = list(channels)
    if not channels:
        raise ValueError("no channels selected")
    ch_infos = [f.channel_info(c) for c in channels]
    t, cols, rate, row_of_tick = _table(record, selection, channels, units)
    n = len(t)
    sep = _separator(fmt, decimal)
    if fmt == "txt" and header:
        out.write(f"Interval=\t{_fmt(1.0 / rate, decimal)} s\n")
        out.write(f"ExcelDateTime=\t{_excel_datetime(record.start_utc):.10g}\t{record.start_local:%Y-%m-%d %H:%M:%S.%f}\n")
        out.write("TimeFormat=\tStartOfBlock\n")
        out.write("DateFormat=\tY-M-D\n")
        out.write("ChannelTitle=\t" + "\t".join(ci.title for ci in ch_infos) + "\n")
        out.write("Range=\t" + "\t".join(f"{_fmt(ci.range_volts, decimal)} V" if ci.range_volts else "" for ci in ch_infos) + "\n")
        out.write("UnitName=\t" + "\t".join(record.value_label(c) for c in channels) + "\n")
    elif fmt in ("csv", "tsv") and header:
        out.write(("block" + sep if block_column else "") + "time_s" + sep
                  + sep.join(_cell(f"{ci.title} [{record.value_label(c)}]", sep) for ci, c in zip(ch_infos, channels))
                  + (sep + "comment" if comments else "") + "\n")
    by_row = _comment_rows(record, selection, row_of_tick) if comments else {}
    commented = sorted(by_row)
    prefix = f"{record.index + 1}{sep}" if (block_column and fmt != "txt") else ""
    lo = 0
    while lo < n:
        if job is not None:
            job.check()
        hi = min(lo + CHUNK_ROWS, n)
        pos = lo
        for r in commented[bisect_left(commented, lo):bisect_left(commented, hi)]:
            if r > pos:
                out.write(_text_rows(t, cols, pos, r, prefix, sep, decimal))
            text = " | ".join(by_row[r])
            out.write(_text_rows(t, cols, r, r + 1, prefix, sep, decimal)[:-1] + sep
                      + ("#* " + _cell(text, sep) if fmt == "txt" else _cell(text, sep, ";" if sep == "," else " ")) + "\n")
            pos = r + 1
        if pos < hi:
            out.write(_text_rows(t, cols, pos, hi, prefix, sep, decimal))
        if job is not None:
            job.advance(hi - lo)
        lo = hi
    return n


def export_file(record: Record, selection: Selection, channels: Sequence[int], path: str | os.PathLike, **kw) -> int:
    with _partial_file(path) as tmp, open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        return write_text(record, selection, channels, fh, **kw)


def whole_file(f: AdichtFile) -> list[Selection]:
    """One selection per record covering everything."""
    return [Selection(r.index, 0, r.ticks) for r in f.records]


def export_selections(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int],
                      path: str | os.PathLike, fmt: str = "txt", *,
                      stale: Optional[Callable[[], bool]] = None,
                      progress: Optional[Callable[[int, int], None]] = None, **kw) -> int:
    """Export several selections (of one or more records) into one file.

    ``fmt`` is one of :data:`FORMATS`; the keyword arguments are those of
    :func:`write_text` (``header``, ``decimal``, ``units``, ``comments``).  Returns
    the number of data rows written.  Text and CSV exports of several records repeat
    the header per record (txt) or carry a ``block`` column (csv), as described in
    the module docstring.

    ``progress(rows_done, rows_total)`` is called after every block of rows, and
    ``stale()`` returning True cancels the export: :class:`ExportCancelled` is raised and
    the target is left as it was (see the module docstring).
    """
    selections = [s for s in selections if s.stop > s.start]
    if not selections:
        raise ValueError("nothing to export: the selection is empty")
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format {fmt!r}")
    channels = list(channels)
    if not channels:
        raise ValueError("no channels selected")
    job = _Progress(sum(table_rows(f.records[s.record], s, channels) for s in selections), stale, progress)
    if fmt == "xlsx":
        return write_xlsx(f, selections, channels, path, job=job, **kw)
    if fmt == "mat":
        from .matfile import write_mat
        return write_mat(f, selections, channels, path, units=kw.get("units", True), job=job)
    header = kw.pop("header", True)
    rows = 0
    with _partial_file(path) as tmp, open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for i, sel in enumerate(selections):
            rec = f.records[sel.record]
            rows += write_text(rec, sel, channels, fh, fmt=fmt,
                               header=header and (fmt == "txt" or i == 0),
                               block_column=len(selections) > 1, job=job, **kw)
    return rows


def get_column_letter(n: int) -> str:
    """Excel column letters of the 1-based column ``n`` (1 -> A, 27 -> AA)."""
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_XML_ESCAPED_LOOKALIKE = re.compile(r"_(x[0-9A-Fa-f]{4}_)")


def _xml_text(s: str) -> str:
    """Text for a ``<t>`` element: markup escaped, characters XML cannot carry written as Excel's
    own ``_xHHHH_`` escape (which Excel turns back into the character), a literal ``_xHHHH_`` protected."""
    s = _XML_ESCAPED_LOOKALIKE.sub(r"_x005F_\1", s)
    s = _XML_ILLEGAL.sub(lambda m: f"_x{ord(m.group()):04X}_", s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xlsx_cell(ref: str, v) -> str:
    """One cell as openpyxl writes it: a number as ``t="n"`` with ``%.16g`` (NaN and infinity an empty ``<v />``),
    text as an inline string, ``None`` as no cell."""
    if v is None:
        return ""
    if isinstance(v, str):
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(v)}</t></is></c>'
    v = float(v)
    return f'<c r="{ref}" t="n">' + ("<v>%.16g</v>" % v if math.isfinite(v) else "<v />") + "</c>"


def _xlsx_row(number: int, values: Sequence) -> str:
    return (f'<row r="{number}">' + "".join(_xlsx_cell(f"{get_column_letter(j + 1)}{number}", v)
                                            for j, v in enumerate(values)) + "</row>")


def _xlsx_numbers(block: np.ndarray, first_row: int) -> str:
    """The rows of ``block`` (all finite) as sheet rows from ``first_row``: one ``%`` over the block."""
    n, k = block.shape
    rows = np.arange(first_row, first_row + n)
    args = np.empty((n, 1 + 2 * k), dtype=object)
    args[:, 0] = rows
    for j in range(k):
        args[:, 1 + 2 * j] = rows
        args[:, 2 + 2 * j] = _strings(block[:, j], "%.16g")
    row = '<row r="%d">' + "".join(f'<c r="{get_column_letter(j + 1)}%d" t="n"><v>%s</v></c>' for j in range(k)) + "</row>"
    return (row * n) % tuple(args.ravel().tolist())


def _xlsx_info(rec: Record, sel: Selection, rate: float, n_channels: int, comments: bool):
    """``(first column, labels, values)`` of the cells that describe a sheet: block, start, where
    the selection starts and the sampling interval.  They sit to the right of the table, one
    empty column after it, in the header row and the first data row, so the sheet itself stays a
    plain table whose first row is the column titles (what RespMech's Excel loader reads)."""
    first_col = 1 + n_channels + (1 if comments else 0) + 2
    labels = ["Block", "start", "selection start (s)", "interval (s)"]
    values = [sel.record + 1, rec.start_local.strftime("%Y-%m-%d %H:%M:%S"), sel.start / rec.tick_rate_hz, 1.0 / rate]
    return first_col, labels, values


def _xlsx_header_values(f: AdichtFile, rec: Record, channels: Sequence[int], comments: bool, info) -> list:
    """The cells of the header row, ``None`` for a gap: the column titles, then the info labels."""
    row: list = ["time_s"] + [f"{f.channel_info(c).title} [{rec.value_label(c)}]" for c in channels] + (["comment"] if comments else [])
    if info is not None:
        row += [None] * (info[0] - 1 - len(row)) + list(info[1])
    return row


def _xlsx_sheet_rows(f: AdichtFile, rec: Record, sel: Selection, channels: Sequence[int], write: Callable[[str], object], *,
                     header: bool, units: bool, comments: bool, job: Optional[_Progress]) -> int:
    """The ``<row>`` elements of the sheet of one selection, through ``write``; returns its data rows."""
    t, cols, rate, row_of_tick = _table(rec, sel, channels, units)
    n = len(t)
    first = 1
    info = _xlsx_info(rec, sel, rate, len(channels), comments) if header else None
    if header:
        write(_xlsx_row(1, _xlsx_header_values(f, rec, channels, comments, info)))
        first = 2
    by_row = _comment_rows(rec, sel, row_of_tick) if comments else {}
    comment_col = get_column_letter(len(channels) + 2)
    for lo in range(0, n, CHUNK_ROWS):
        if job is not None:
            job.check()
        hi = min(lo + CHUNK_ROWS, n)
        block = np.empty((hi - lo, 1 + len(cols)))
        block[:, 0] = t[lo:hi]
        for j, col in enumerate(cols):
            block[:, j + 1] = col[lo:hi]
        # cells after the table's own columns: a comment, and (on the first data row) the info cells
        extra: dict[int, str] = {r: _xlsx_cell(f"{comment_col}{first + r}", " | ".join(by_row[r])) for r in by_row if lo <= r < hi}
        if info is not None and lo == 0:
            extra[0] = extra.get(0, "") + "".join(_xlsx_cell(f"{get_column_letter(info[0] + j)}{first}", v) for j, v in enumerate(info[2]))
        if np.isfinite(block).all():
            text = _xlsx_numbers(block, first + lo)
            if extra:
                rows = text.split("</row>")
                for r, cells in extra.items():
                    rows[r - lo] += cells
                text = "</row>".join(rows)
            write(text)
        else:
            vals = block.tolist()
            write("".join(_xlsx_row(first + lo + i, vals[i])[:-len("</row>")] + extra.get(lo + i, "") + "</row>"
                          for i in range(hi - lo)))
        if job is not None:
            job.advance(hi - lo)
    return n


def _xlsx_streamed(f: AdichtFile, sheets: Sequence[tuple[str, Record, Selection]], channels: Sequence[int],
                   path: str, *, header: bool, units: bool, comments: bool, job: Optional[_Progress]) -> bool:
    """Write the workbook with the sheet data streamed as XML instead of cell by cell through
    openpyxl (a dozen times faster).  The package around the sheets (content types, workbook,
    styles, relationships) is what openpyxl itself writes for an empty workbook of these sheets, and
    each sheet keeps openpyxl's envelope around its ``<sheetData>``; only the rows are ours.
    Returns False, having written nothing, when openpyxl's layout is not the one this relies on."""
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    for name, _rec, _sel in sheets:
        wb.create_sheet(name)
    skeleton = io.BytesIO()
    wb.save(skeleton)
    zin = zipfile.ZipFile(skeleton)
    envelopes: dict[str, tuple[str, str]] = {}
    for i in range(len(sheets)):
        part = f"xl/worksheets/sheet{i + 1}.xml"
        try:
            xml = zin.read(part).decode("utf-8")
        except KeyError:
            return False
        halves = re.split(r"<sheetData\s*/>|<sheetData>\s*</sheetData>", xml)
        if len(halves) != 2:
            return False
        envelopes[part] = (halves[0], halves[1])
    est_bytes = sum(table_rows(rec, sel, channels) for _n, rec, sel in sheets) * (len(channels) + 2) * 60
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zout:
        for item in zin.infolist():
            if item.filename not in envelopes:
                zout.writestr(item, zin.read(item.filename))
                continue
            head, tail = envelopes[item.filename]
            _name, rec, sel = sheets[int(item.filename[len("xl/worksheets/sheet"):-4]) - 1]
            zi = zipfile.ZipInfo(item.filename, item.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with zout.open(zi, "w", force_zip64=est_bytes > (1 << 30)) as w:
                def put(text: str, w=w) -> None:
                    w.write(text.encode("utf-8"))
                put(head + "<sheetData>")
                _xlsx_sheet_rows(f, rec, sel, channels, put, header=header, units=units, comments=comments, job=job)
                put("</sheetData>" + tail)
    return True


def write_xlsx(f: AdichtFile, selections: Sequence[Selection], channels: Sequence[int], path: str | os.PathLike, *,
               header: bool = True, decimal: str = ".", units: bool = True, comments: bool = True,
               job: Optional[_Progress] = None, fast: bool = True) -> int:
    """Excel workbook with one sheet per selection ("Block 3", or "Block 3 (2)" for a
    second selection of the same record).  Numbers are written as numbers (``decimal``
    is ignored: Excel applies its own locale).  Refuses a selection longer than a
    worksheet (:data:`XLSX_MAX_ROWS`).  ``fast=False`` writes cell by cell through openpyxl
    (what this function did before the streamed writer; the tests compare the two)."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:                           # pragma: no cover - depends on the environment
        raise RuntimeError("Excel export needs the openpyxl package (pip install openpyxl)") from exc
    channels = list(channels)
    if not channels:
        raise ValueError("no channels selected")
    sheets: list[tuple[str, Record, Selection]] = []
    seen: dict[int, int] = {}
    rows = 0
    for sel in selections:
        rec = f.records[sel.record]
        n = table_rows(rec, sel, channels)
        if n + 1 > XLSX_MAX_ROWS:
            raise ValueError(f"block {sel.record + 1}: {n} rows do not fit an Excel sheet "
                             f"({XLSX_MAX_ROWS - 1} data rows); export a shorter selection or use text/CSV")
        k = seen.get(sel.record, 0) + 1
        seen[sel.record] = k
        sheets.append((f"Block {sel.record + 1}" + (f" ({k})" if k > 1 else ""), rec, sel))
        rows += n
    if job is None:
        job = _Progress(rows)
    with _partial_file(path) as tmp:
        if fast and _xlsx_streamed(f, sheets, channels, tmp, header=header, units=units, comments=comments, job=job):
            return rows
        wb = Workbook(write_only=True)
        for name, rec, sel in sheets:
            t, cols, rate, row_of_tick = _table(rec, sel, channels, units)
            ws = wb.create_sheet(name)
            info = _xlsx_info(rec, sel, rate, len(channels), comments) if header else None
            if header:
                ws.append(_xlsx_header_values(f, rec, channels, comments, info))
            by_row = _comment_rows(rec, sel, row_of_tick) if comments else {}
            for i in range(len(t)):
                if i % CHUNK_ROWS == 0:
                    job.check()
                row = [float(t[i])] + [float(col[i]) for col in cols]
                if comments:
                    row.append(" | ".join(by_row[i]) if i in by_row else None)
                if info is not None and i == 0:
                    row += [None] * (info[0] - 1 - len(row)) + list(info[2])
                ws.append(row)
                if i % CHUNK_ROWS == CHUNK_ROWS - 1 or i == len(t) - 1:
                    job.advance(i % CHUNK_ROWS + 1)
        wb.save(tmp)
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
