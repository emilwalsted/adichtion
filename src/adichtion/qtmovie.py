"""The timing of LabChart for Mac's movies (QuickTime ``.mov``).

LabChart for Mac's video capture writes one QuickTime movie per LabChart block,
``<document> movies/LabChartMovieN.mov``, named by the document's ``Cnt1``/``CntD``
chunks (``docs/MAC_FORMAT.md`` §7).  Besides the video (DV) and sound tracks every
movie has a generic track named "Synchronisation track" with one 8-byte sample per
video frame: the frame's capture time as a big-endian ``uint64`` in microseconds on the
classic Mac ``Microseconds()`` clock, the clock of the document's ``mTim`` chunk.  A
frame's time in its LabChart block is therefore ``(stamp - mTim of the block's first
storage block) / 1e6``, and the movie time of the block start follows from any frame.

Only what is needed is read: the top-level atom headers, the ``moov`` atom and the
sync samples, a few hundred kB even for a movie of many GB.  Pure ``struct`` + numpy.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Iterator, Optional, Union

import numpy as np

__all__ = ["MovieError", "MovieTiming", "read_movie_timing"]

MAX_MOOV_BYTES = 256 * 1024 * 1024          # a sanity bound; LabChart's are well below 1 MB
SYNC_SAMPLE_SIZE = 8
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"dinf"}


class MovieError(ValueError):
    pass


@dataclass(frozen=True)
class MovieTiming:
    """Video frame times (movie time, s) and the capture stamp of every frame (µs)."""

    duration_s: float
    frame_times_s: np.ndarray
    stamps_us: np.ndarray

    def offset_s(self, clock0_us: int) -> float:
        """The movie time of the moment ``clock0_us`` on the stamps' clock (a LabChart
        block's start): the median over the frames after the first of ``frame time -
        (stamp - clock0) / 1e6``.  The first frame is left out: the movie shows it from
        time 0, before it was captured."""
        n = len(self.stamps_us)
        if n == 0:
            raise MovieError("the movie has no frames")
        sel = slice(1, n) if n > 1 else slice(0, 1)
        d = self.frame_times_s[sel] - (self.stamps_us[sel] - int(clock0_us)) / 1e6
        return float(np.median(d))


def _atoms(buf: bytes, off: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """(type, body start, atom end) of the atoms in ``buf[off:end]``."""
    while off + 8 <= end:
        size, typ = struct.unpack_from(">I4s", buf, off)
        hdr = 8
        if size == 1:
            if off + 16 > end:
                raise MovieError("truncated atom header")
            size = struct.unpack_from(">Q", buf, off + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            raise MovieError(f"atom {typ!r} at {off} runs past its container")
        yield typ, off + hdr, off + size
        off += size


def _find(buf: bytes, off: int, end: int, path: list[bytes]) -> Optional[tuple[int, int]]:
    for typ, body, stop in _atoms(buf, off, end):
        if typ == path[0]:
            if len(path) == 1:
                return body, stop
            hit = _find(buf, body, stop, path[1:])
            if hit is not None:
                return hit
    return None


def _need(hit: Optional[tuple[int, int]], what: str) -> tuple[int, int]:
    if hit is None:
        raise MovieError(f"no {what} atom")
    return hit


def _track(buf: bytes, body: int, stop: int) -> dict:
    t: dict = {}
    mdia = _need(_find(buf, body, stop, [b"mdia"]), "mdia")
    mdhd = _need(_find(buf, *mdia, [b"mdhd"]), "mdhd")
    if buf[mdhd[0]] == 0:
        t["timescale"], t["duration"] = struct.unpack_from(">II", buf, mdhd[0] + 12)
    else:
        t["timescale"], t["duration"] = struct.unpack_from(">IQ", buf, mdhd[0] + 20)
    hdlr = _need(_find(buf, *mdia, [b"hdlr"]), "hdlr")
    t["handler"] = buf[hdlr[0] + 8:hdlr[0] + 12]
    stbl = _need(_find(buf, *mdia, [b"minf", b"stbl"]), "stbl")
    stts = _need(_find(buf, *stbl, [b"stts"]), "stts")
    n = struct.unpack_from(">I", buf, stts[0] + 4)[0]
    runs = np.frombuffer(buf, dtype=">u4", count=2 * n, offset=stts[0] + 8).reshape(n, 2).astype(np.int64)
    t["durations"] = np.repeat(runs[:, 1], runs[:, 0])
    stsz = _need(_find(buf, *stbl, [b"stsz"]), "stsz")
    t["sample_size"], t["count"] = struct.unpack_from(">II", buf, stsz[0] + 4)
    stsc = _need(_find(buf, *stbl, [b"stsc"]), "stsc")
    n = struct.unpack_from(">I", buf, stsc[0] + 4)[0]
    t["stsc"] = [struct.unpack_from(">III", buf, stsc[0] + 8 + 12 * k) for k in range(n)]
    co = _find(buf, *stbl, [b"stco"])
    if co is not None:
        n = struct.unpack_from(">I", buf, co[0] + 4)[0]
        t["chunks"] = np.frombuffer(buf, dtype=">u4", count=n, offset=co[0] + 8).astype(np.int64)
    else:
        co = _need(_find(buf, *stbl, [b"co64"]), "stco/co64")
        n = struct.unpack_from(">I", buf, co[0] + 4)[0]
        t["chunks"] = np.frombuffer(buf, dtype=">u8", count=n, offset=co[0] + 8).astype(np.int64)
    elst = _find(buf, body, stop, [b"edts", b"elst"])
    t["media_start"] = 0
    if elst is not None and struct.unpack_from(">I", buf, elst[0] + 4)[0] >= 1:
        t["media_start"] = max(0, struct.unpack_from(">i", buf, elst[0] + 12)[0])
    name = _find(buf, body, stop, [b"udta", b"name"])
    t["name"] = buf[name[0]:name[1]].decode("mac_roman", "replace") if name else ""
    return t


def _sample_offsets(t: dict) -> np.ndarray:
    """File offset of every sample of a track with fixed-size samples."""
    chunks = t["chunks"]
    per_chunk = np.zeros(len(chunks), dtype=np.int64)
    runs = t["stsc"] + [(len(chunks) + 1, 0, 0)]
    for (first, per, _desc), (nxt, _p, _d) in zip(runs, runs[1:]):
        per_chunk[first - 1:nxt - 1] = per
    idx = np.repeat(np.arange(len(chunks)), per_chunk)
    within = np.arange(len(idx)) - np.repeat(np.cumsum(per_chunk) - per_chunk, per_chunk)
    return (chunks[idx] + within * t["sample_size"])[:t["count"]]


def _read_moov(fh, size: int) -> bytes:
    off = 0
    while off + 8 <= size:
        fh.seek(off)
        head = fh.read(16)
        if len(head) < 8:
            break
        n, typ = struct.unpack_from(">I4s", head)
        hdr = 8
        if n == 1:
            n = struct.unpack_from(">Q", head, 8)[0]
            hdr = 16
        elif n == 0:
            n = size - off
        if n < hdr:
            raise MovieError(f"bad atom size at {off}")
        if typ == b"moov":
            if n > MAX_MOOV_BYTES:
                raise MovieError(f"moov atom of {n} bytes")
            fh.seek(off)
            data = fh.read(n)
            if len(data) != n:
                raise MovieError("truncated moov atom")
            return data
        off += n
    raise MovieError("no moov atom (not a QuickTime movie, or not downloaded)")


def read_movie_timing(path: Union[str, os.PathLike]) -> MovieTiming:
    """Frame times and sync stamps of a LabChart for Mac movie.  Raises
    :class:`MovieError` for a file that is not such a movie (no synchronisation track,
    or as many stamps as frames missing) and ``OSError`` when it cannot be read."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size < 16:
            raise MovieError("empty file (a cloud placeholder that is not downloaded?)")
        moov = _read_moov(fh, size)
        body = 16 if struct.unpack_from(">I", moov)[0] == 1 else 8
        mvhd = _need(_find(moov, body, len(moov), [b"mvhd"]), "mvhd")
        if moov[mvhd[0]] == 0:
            ts, dur = struct.unpack_from(">II", moov, mvhd[0] + 12)
        else:
            ts, dur = struct.unpack_from(">IQ", moov, mvhd[0] + 20)
        tracks = [_track(moov, b, e) for typ, b, e in _atoms(moov, body, len(moov)) if typ == b"trak"]
        video = next((t for t in tracks if t["handler"] == b"vide"), None)
        sync = next((t for t in tracks if t["handler"] != b"vide" and t["sample_size"] == SYNC_SAMPLE_SIZE
                     and (t["name"].startswith("Synchronisation") or t["handler"] == b"gnrc")), None)
        if video is None:
            raise MovieError("no video track")
        if sync is None:
            raise MovieError("no synchronisation track (not a LabChart for Mac movie?)")
        if sync["count"] != video["count"] or len(video["durations"]) != video["count"]:
            raise MovieError(f"{sync['count']} sync stamps for {video['count']} video frames")
        offsets = _sample_offsets(sync)
        stamps = np.empty(len(offsets), dtype=np.int64)
        # the stamps usually sit together in a small mdat near the end: read runs of them at once
        k = 0
        while k < len(offsets):
            j = k + 1
            while j < len(offsets) and offsets[j] == offsets[j - 1] + SYNC_SAMPLE_SIZE:
                j += 1
            fh.seek(int(offsets[k]))
            raw = fh.read(SYNC_SAMPLE_SIZE * (j - k))
            if len(raw) != SYNC_SAMPLE_SIZE * (j - k):
                raise MovieError("sync samples outside the file")
            stamps[k:j] = np.frombuffer(raw, dtype=">u8").astype(np.int64)
            k = j
    starts = np.concatenate([[0], np.cumsum(video["durations"])[:-1]]) - video["media_start"]
    return MovieTiming(duration_s=dur / ts if ts else 0.0,
                       frame_times_s=starts / float(video["timescale"]), stamps_us=stamps)
