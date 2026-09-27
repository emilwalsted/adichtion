"""The video module's stream: which video file belongs to which record.

LabChart's video capture writes one file per record (LabChart block) into a
folder ``<document name> movies`` next to the ``.adicht`` file, named
``Record<N>.wmv`` with ``N`` the 1-based record number, and keeps a small
stream in the document (``ChildDocuments/ADIStdPart<k>``, recognised by the
GUID it starts with) that lists, per record, four float64 seconds on the
video clock and the file name.  See ``docs/FORMAT.md`` §5.
"""
from __future__ import annotations

import os
import struct
from typing import Optional

from .container import AdichtContainer, Entry
from .model import VideoClip
from .serialization import TAG, read_prefixed

VIDEO_GUID = bytes.fromhex("a46425de60224944b4c6b13bc0eaa648")
MOVIES_SUFFIX = " movies"


class VideoStreamError(ValueError):
    pass


def find_video_stream(container: AdichtContainer) -> Optional[Entry]:
    """The stream of the video module, found by its leading GUID (its name is
    just ``ADIStdPart<k>``, the k-th child document)."""
    for e in container.walk():
        if e.is_storage or e.size < 32:
            continue
        head = container.data[container.offset(e.first) : container.offset(e.first) + 16]
        if bytes(head) == VIDEO_GUID:
            return e
    return None


def parse_video_stream(data: bytes) -> list[VideoClip]:
    """Decode the video stream: GUID, magic, int32 version, int32 n, then per
    record ``float64 start, stop, video_start, offset`` and a tagged file name."""
    if data[:16] != VIDEO_GUID:
        raise VideoStreamError("not a video stream (GUID mismatch)")
    magic, version, n = data[16:20], *struct.unpack_from("<ii", data, 20)
    if magic != bytes.fromhex("aaaa1555") or not (0 <= n <= 100000):
        raise VideoStreamError(f"unexpected video stream header (version {version}, {n} entries)")
    clips: list[VideoClip] = []
    o = 28
    for i in range(n):
        a, b, c, d = struct.unpack_from("<4d", data, o)
        o += 32
        if data[o : o + 4] != TAG:
            raise VideoStreamError(f"video entry {i}: file name expected at {o}")
        r = read_prefixed(data, o + 4)
        if r is None:
            raise VideoStreamError(f"video entry {i}: malformed file name at {o}")
        name, o = r
        clips.append(VideoClip(record_index=i, file_name=name, start_s=a, stop_s=b, video_start_s=c, offset_s=d))
    return clips


def read_videos(container: AdichtContainer) -> list[VideoClip]:
    """Video clips of a document, ``[]`` when it has no video stream."""
    e = find_video_stream(container)
    if e is None:
        return []
    return parse_video_stream(container.read_stream(e))


def movies_folder(adicht_path: str | os.PathLike) -> str:
    """The folder LabChart uses for the videos of a document (may not exist)."""
    p = os.fspath(adicht_path)
    stem, _ext = os.path.splitext(p)
    return stem + MOVIES_SUFFIX


def video_path(adicht_path: str | os.PathLike, clip: VideoClip) -> Optional[str]:
    """Full path of a clip's file if it exists next to the document."""
    if not clip.has_video:
        return None
    p = os.path.join(movies_folder(adicht_path), clip.file_name)
    return p if os.path.exists(p) else None
