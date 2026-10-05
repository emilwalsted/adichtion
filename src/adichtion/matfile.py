"""A minimal MATLAB level-5 MAT-file writer, numpy only.

Level 5 (``save -v6``/``-v7``, uncompressed) is what ``scipy.io.loadmat``, MATLAB and Octave all
read, and what RespMech reads: it does not read the HDF5-based v7.3 files.  Only what an
export needs is written: real double matrices, scalar doubles and character matrices.

The layout written for a recording follows LabChart for Windows' "block" export, which is the
``windows`` MATLAB variant of RespMech (it reads ``data_block1``, one row per channel):

* ``data_block<N>``: channels x samples, doubles, in the units of the channels (stored counts
  with ``units=False``); ``N`` counts the exported blocks from 1 (a whole-file export has one
  per LabChart block, an interval of one block exactly one).
* ``titles_block<N>``: the channel titles, one row each; ``units_block<N>``: their units.
* ``samplerate_block<N>``: the table's sampling rate in Hz (the common rate, see
  :func:`adichtion.export.table_plan`); ``record_block<N>``: the 1-based LabChart block the
  data came from.

Writing streams the data in chunks of rows, so the table is never copied whole.  One matrix
element holds at most 4 GiB in this format; a larger table is refused with a ValueError that
says so (export it as CSV or in shorter pieces).
"""
from __future__ import annotations

import os
import struct
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, Optional, Sequence

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from .export import _Progress
    from .file import AdichtFile, Selection

# data types of a data element, and array classes (MATLAB "MAT-File Format", level 5)
_MI_INT8, _MI_INT32, _MI_UINT32, _MI_DOUBLE, _MI_MATRIX, _MI_UTF8 = 1, 5, 6, 9, 14, 16
_MX_CHAR, _MX_DOUBLE = 4, 6

_MAX_ELEMENT = 2**32 - 1


def _pad(n: int) -> int:
    """Padding that brings ``n`` bytes up to a multiple of 8."""
    return (-n) % 8


def _element(fh: BinaryIO, kind: int, payload: bytes) -> None:
    fh.write(struct.pack("<II", kind, len(payload)))
    fh.write(payload)
    fh.write(b"\0" * _pad(len(payload)))


def _matrix_head(fh: BinaryIO, name: str, cls: int, dims: Sequence[int], data_type: int, data_bytes: int) -> None:
    """The tag and the first three sub-elements (flags, dimensions, name) of a matrix, and the
    tag of its real part; the caller writes ``data_bytes`` of data and its padding."""
    nm = name.encode("ascii")
    dim_bytes = 4 * len(dims)
    total = (8 + 8) + (8 + dim_bytes + _pad(dim_bytes)) + (8 + len(nm) + _pad(len(nm))) + (8 + data_bytes + _pad(data_bytes))
    if total > _MAX_ELEMENT:
        raise ValueError(f"{name} is too large for a MATLAB file ({total / 2**30:.1f} GiB; one variable holds at most 4 GiB): "
                         "export a shorter selection, or CSV")
    fh.write(struct.pack("<II", _MI_MATRIX, total))
    _element(fh, _MI_UINT32, struct.pack("<II", cls, 0))
    _element(fh, _MI_INT32, struct.pack(f"<{len(dims)}i", *dims))
    _element(fh, _MI_INT8, nm)
    fh.write(struct.pack("<II", data_type, data_bytes))


def put_scalar(fh: BinaryIO, name: str, value: float) -> None:
    _matrix_head(fh, name, _MX_DOUBLE, (1, 1), _MI_DOUBLE, 8)
    fh.write(struct.pack("<d", float(value)) + b"\0" * _pad(8))


def put_chars(fh: BinaryIO, name: str, rows: Sequence[str]) -> None:
    """A character matrix, one string per row, padded with spaces to the longest (as MATLAB's
    ``char`` does).  Characters outside the Basic Multilingual Plane are replaced by ``?``."""
    rows = [("".join(ch if ord(ch) < 0x10000 else "?" for ch in r)) for r in rows] or [""]
    width = max(len(r) for r in rows)
    padded = [r.ljust(width) for r in rows]
    # MATLAB stores column by column; the characters go as UTF-8 (miUTF8), which scipy and MATLAB
    # both decode (scipy reads miUINT16 characters as UTF-8 and would garble anything but ASCII)
    raw = "".join(padded[i][j] for j in range(width) for i in range(len(rows))).encode("utf-8")
    _matrix_head(fh, name, _MX_CHAR, (len(rows), width), _MI_UTF8, len(raw))
    fh.write(raw + b"\0" * _pad(len(raw)))


def put_table(fh: BinaryIO, name: str, columns: Sequence[np.ndarray], *, job: Optional["_Progress"] = None) -> None:
    """A ``len(columns)`` x ``n`` double matrix, one row per column of the table.  Column-major
    storage of that shape is sample after sample, so it is written a chunk of samples at a time."""
    n = len(columns[0]) if len(columns) else 0
    k = len(columns)
    nbytes = 8 * k * n
    _matrix_head(fh, name, _MX_DOUBLE, (k, n), _MI_DOUBLE, nbytes)
    from . import export          # the chunk size is the exports' (tests make it small)
    chunk = export.CHUNK_ROWS
    for lo in range(0, n, chunk):
        if job is not None:
            job.check()
        hi = min(lo + chunk, n)
        block = np.empty((hi - lo, k), dtype="<f8")
        for j, col in enumerate(columns):
            block[:, j] = col[lo:hi]
        fh.write(block.tobytes())
        if job is not None:
            job.advance(hi - lo)
    fh.write(b"\0" * _pad(nbytes))


def write_header(fh: BinaryIO, text: str = "") -> None:
    """The 128-byte file header: 116 bytes of text, the (empty) subsystem offset, version 0x0100
    and the byte-order mark (``IM``: little endian)."""
    text = (text or "MATLAB 5.0 MAT-file, written by adichtion").encode("ascii", "replace")[:116].ljust(116, b" ")
    fh.write(text + b"\0" * 8 + struct.pack("<H", 0x0100) + b"IM")


def write_mat(f: "AdichtFile", selections: Sequence["Selection"], channels: Sequence[int], path: str | os.PathLike, *,
              units: bool = True, job: Optional["_Progress"] = None) -> int:
    """Write the selections as ``data_block<N>`` matrices (see the module docstring).  Returns
    the number of samples per channel written, summed over the selections."""
    from .export import _partial_file, _table
    channels = list(channels)
    rows = 0
    with _partial_file(path) as tmp, open(tmp, "wb") as fh:
        write_header(fh, f"MATLAB 5.0 MAT-file, written by adichtion {datetime.now():%Y-%m-%d %H:%M:%S}")
        for n, sel in enumerate(selections, start=1):
            rec = f.records[sel.record]
            t, cols, rate, _row = _table(rec, sel, channels, units)
            put_table(fh, f"data_block{n}", cols, job=job)
            put_chars(fh, f"titles_block{n}", [f.channel_info(c).title for c in channels])
            put_chars(fh, f"units_block{n}", [rec.value_label(c) for c in channels])
            put_scalar(fh, f"samplerate_block{n}", rate)
            put_scalar(fh, f"record_block{n}", rec.index + 1)
            rows += len(t)
    return rows
