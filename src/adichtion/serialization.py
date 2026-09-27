"""Primitive serialization conventions used inside LabChart streams.

* A *prefixed string* is ``int32 n, int32 -n`` followed by ``n`` UTF-16LE code
  units, the last of which is the terminating NUL.
* A *tagged string* is the same preceded by the int32 tag ``0x80000001``.
* Lists are ``int32 n, int32 -n`` followed by ``n`` items.

The redundant ``-n`` makes every length field self-checking, which is what
makes scanning a stream for structures feasible.
"""
from __future__ import annotations

import struct
from typing import Iterator, Optional

TAG = b"\x01\x00\x00\x80"  # 0x80000001 precedes a serialized string


def read_prefixed(blob: bytes, i: int, max_len: int = 1 << 20) -> Optional[tuple[str, int]]:
    """Read a prefixed UTF-16 string at offset ``i``.

    Returns ``(text_without_nul, end_offset)`` or ``None`` when the bytes at
    ``i`` are not a well-formed prefixed string.
    """
    if i < 0 or i + 8 > len(blob):
        return None
    n, neg = struct.unpack_from("<ii", blob, i)
    if not (0 < n <= max_len and neg == -n and i + 8 + 2 * n <= len(blob)):
        return None
    try:
        s = blob[i + 8 : i + 8 + 2 * n].decode("utf-16le")
    except UnicodeDecodeError:
        return None
    if not s.endswith("\x00"):
        return None
    body = s[:-1]
    if any(not (ch.isprintable() or ch in "\t\n\r") for ch in body):
        return None
    return body, i + 8 + 2 * n


def read_tagged(blob: bytes, i: int) -> Optional[tuple[str, int]]:
    """Read a tagged prefixed string (``TAG`` + prefixed string) at offset ``i``."""
    if blob[i : i + 4] != TAG:
        return None
    return read_prefixed(blob, i + 4)


def iter_prefixed_strings(blob: bytes, min_len: int = 1) -> Iterator[tuple[int, str]]:
    """Yield ``(offset, text)`` for every prefixed string, at any byte alignment."""
    i = 0
    n = len(blob)
    while i + 8 <= n:
        r = read_prefixed(blob, i)
        if r is not None and len(r[0]) >= min_len:
            yield i, r[0]
            i = r[1]
        else:
            i += 1


def iter_tagged_strings(blob: bytes) -> Iterator[tuple[int, str]]:
    """Yield ``(offset_of_tag, text)`` for every tagged string in ``blob``."""
    i = blob.find(TAG)
    while i != -1:
        r = read_prefixed(blob, i + 4)
        if r is not None:
            yield i, r[0]
            i = blob.find(TAG, r[1])
        else:
            i = blob.find(TAG, i + 1)
