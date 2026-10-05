"""Cut the video that belongs to an exported interval into a clip file.

The data export of an interval can be followed by a clip of the video LabChart recorded with it
(:mod:`adichtion.video`, :mod:`adichtion.qtmovie`).  Two ways to write it (:data:`MODES`):

``"original"``
    The clip keeps the file's own container and codec and is cut without re-encoding (``ffmpeg -c copy``):
    nothing is lost, and it is fast.  A stream copy can only start on a key frame, so the clip may begin a
    little before the interval (up to the file's key-frame distance); :func:`segment_note` says so.
``"h264"``
    The clip is re-encoded to H.264 in an MP4 file (yuv420p, ``-movflags +faststart``), which every
    platform's player reads.  Frame accurate.  Audio, when the file has any, is kept for one block and
    left out when several blocks are joined (their audio would have to be joined with the same care).

An interval over several blocks gives one clip: the blocks' pieces joined in order, with the time between
blocks left out (the video of the gap does not exist either).  The pieces must then share a size and a
container (a re-encoded clip is scaled to the first piece's size; a copied clip needs equal streams, and
ffmpeg refuses otherwise, which is reported).

The work is done by an ``ffmpeg`` program (:func:`find_ffmpeg`: ``ADICHTION_FFMPEG``, the one bundled with the
``imageio-ffmpeg`` package, or one on ``PATH``); the Qt-free layer here only builds the command line and
watches it.  The clip is written under ``<name>.part<ext>`` and renamed when complete, like a data export
(:class:`PendingClip` keeps the rename in the caller's hands, so a data export that follows can fail
without a clip being left behind).
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .file import AdichtFile, Selection

MODES = ("original", "h264")
H264_EXTENSION = ".mp4"
H264_CRF = 20                       # visually lossless for a camera clip, a quarter of the size of crf 12


class VideoExportError(RuntimeError):
    """The clip could not be made (no ffmpeg, mixed containers, ffmpeg's own refusal); the message is for the user."""


class VideoExportCancelled(Exception):
    """``stale()`` turned true while the clip was being written; nothing is left behind."""


@dataclass(frozen=True)
class Segment:
    """One piece of one video file: ``duration_s`` from ``start_s`` (seconds into the file)."""

    record: int            # 0-based record the piece belongs to
    path: str
    start_s: float
    duration_s: float


def find_ffmpeg() -> Optional[str]:
    """The ffmpeg program to use, or ``None``: ``ADICHTION_FFMPEG`` (a path), the copy bundled with the
    ``imageio-ffmpeg`` package, then ``ffmpeg`` on ``PATH``."""
    env = os.environ.get("ADICHTION_FFMPEG")
    if env and os.path.isfile(env):
        return env
    try:
        import imageio_ffmpeg                        # noqa: PLC0415 - optional
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:                                 # noqa: BLE001 - not installed, or no binary for this platform
        pass
    return shutil.which("ffmpeg")


def video_segments(f: AdichtFile, selections: Sequence[Selection]) -> tuple[list[Segment], list[int]]:
    """The pieces of video that cover ``selections`` (in the order given), and the 1-based numbers of the blocks
    that have none (no clip, or its file is not there).  A piece's position is ``VideoClip.file_time`` of the
    selection's first and last tick, clipped to the start of the file."""
    segments: list[Segment] = []
    missing: list[int] = []
    for sel in selections:
        rec = f.records[sel.record]
        path, clip = rec.video_file, rec.video
        path = os.path.abspath(path) if path is not None else None      # a concat list reads stems relative to itself
        if path is None or clip is None:
            missing.append(sel.record + 1)
            continue
        rate = rec.tick_rate_hz
        t0, t1 = sel.start / rate, sel.stop / rate
        start = clip.file_time(t0)
        dur = t1 - t0
        if start < 0:                                 # the interval begins before the clip does
            dur += start
            start = 0.0
        if dur <= 0:
            missing.append(sel.record + 1)
            continue
        segments.append(Segment(sel.record, path, start, dur))
    return segments, missing


def has_video(f: AdichtFile, selections: Sequence[Selection]) -> bool:
    """Is there a video file for at least one of the selections?  Stops at the first one found (a file-system
    lookup per block is what costs), so asking it for a whole file is cheap when the first block has a clip."""
    for sel in selections:
        if 0 <= sel.record < len(f.records) and f.records[sel.record].video_file is not None:
            return True
    return False


def clip_extension(mode: str, segments: Sequence[Segment]) -> str:
    """The file extension (with the dot) of the clip: the source's own for ``original``, ``.mp4`` for ``h264``."""
    if mode == "h264" or not segments:
        return H264_EXTENSION
    return os.path.splitext(segments[0].path)[1].lower() or H264_EXTENSION


def segment_note(mode: str, segments: Sequence[Segment], missing: Sequence[int] = ()) -> str:
    """A sentence for the export dialog and the status bar about what the clip will (not) contain."""
    parts = []
    if mode == "original":
        parts.append("The clip is cut without re-encoding, so it can begin a little before the interval (at the "
                     "video's previous key frame)")
    if len(segments) > 1:
        parts.append(f"the {len(segments)} blocks' video is joined into one clip, the time between blocks left out")
    if missing:
        parts.append("no video for block" + ("s " if len(missing) > 1 else " ") + ", ".join(str(m) for m in missing))
    return "; ".join(parts)


def _hidden_window() -> dict:
    """``subprocess`` arguments that keep a console window from flashing up on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


_SIZE = re.compile(r"Video:.*?[ ,](\d{2,5})x(\d{2,5})")
_RATE = re.compile(r"([\d.]+) fps")


def _probe_text(ffmpeg: str, path: str) -> str:
    """What ``ffmpeg -i`` prints about a file (it exits non-zero for want of an output, which is fine)."""
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                           errors="replace", timeout=60, **_hidden_window())
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoExportError(f"ffmpeg could not be started ({getattr(exc, 'strerror', None) or exc}).") from exc
    return p.stderr


def probe_video(ffmpeg: str, path: str) -> tuple[int, int, float]:
    """``(width, height, fps)`` of a file's first video stream, read from what ``ffmpeg -i`` prints."""
    text = _probe_text(ffmpeg, path)
    m = _SIZE.search(text)
    if m is None:
        raise VideoExportError(f"{os.path.basename(path)} has no video stream ffmpeg can read.")
    r = _RATE.search(text[m.start():])
    return int(m.group(1)), int(m.group(2)), float(r.group(1)) if r else 25.0


_TIMES = re.compile(r"Duration: (\d+):(\d+):([\d.]+)(?:, start: (-?[\d.]+))?")


def probe_times(ffmpeg: str, path: str) -> tuple[float, float]:
    """``(start_s, duration_s)`` of a file as the container states them (start is the first timestamp: a stream
    copy's ``-ss`` is relative to it, a concat list's ``inpoint`` is absolute)."""
    m = _TIMES.search(_probe_text(ffmpeg, path))
    if m is None:
        raise VideoExportError(f"{os.path.basename(path)} has no duration ffmpeg can read.")
    return float(m.group(4) or 0.0), int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def fit_segments(ffmpeg: str, segments: Sequence[Segment]) -> tuple[list[Segment], list[float]]:
    """The pieces cut to what each file really holds (an interval can run past the end of the video), pieces
    with nothing left dropped, and each kept file's start time."""
    kept, starts = [], []
    for s in segments:
        start, length = probe_times(ffmpeg, s.path)
        dur = min(s.duration_s, length - s.start_s)
        if dur > 0.02:
            kept.append(Segment(s.record, s.path, s.start_s, dur))
            starts.append(start)
    return kept, starts


def build_command(ffmpeg: str, segments: Sequence[Segment], mode: str, out: str, workdir: str,
                  size: Optional[tuple[int, int, float]] = None,
                  file_starts: Optional[Sequence[float]] = None) -> list[str]:
    """The ffmpeg command line for ``segments`` (non-empty) written to ``out``.  ``workdir`` holds the list file a
    stream-copied join needs; ``file_starts`` the start time of each piece's file (default 0); ``size`` is ``(width, height, fps)`` of the first piece (needed to join re-encoded pieces)."""
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if mode == "original":
        if len({os.path.splitext(s.path)[1].lower() for s in segments}) > 1:
            raise VideoExportError("The video of these blocks is in different file types, so it cannot be joined "
                                   "without re-encoding: choose H.264, or export one block at a time.")
        if len(segments) == 1:
            s = segments[0]
            return base + ["-ss", f"{s.start_s:.6f}", "-i", s.path, "-t", f"{s.duration_s:.6f}", "-map", "0:v", "-map", "0:a?",
                           "-c", "copy", "-avoid_negative_ts", "make_zero", out]
        listing = os.path.join(workdir, "pieces.txt")
        with open(listing, "w", encoding="utf-8") as fh:
            for i, s in enumerate(segments):
                t0 = s.start_s + (file_starts[i] if file_starts else 0.0)
                fh.write("file '" + os.path.abspath(s.path).replace("'", "'\\''") + "'\n")
                fh.write(f"inpoint {t0:.6f}\noutpoint {t0 + s.duration_s:.6f}\n")
        return base + ["-f", "concat", "-safe", "0", "-i", listing, "-map", "0:v", "-map", "0:a?", "-c", "copy",
                       "-avoid_negative_ts", "make_zero", out]
    if mode != "h264":
        raise ValueError(f"unknown video mode {mode!r}")
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", str(H264_CRF), "-pix_fmt", "yuv420p",
           "-movflags", "+faststart"]
    if len(segments) == 1:
        s = segments[0]
        return base + ["-ss", f"{s.start_s:.6f}", "-i", s.path, "-t", f"{s.duration_s:.6f}",
                       "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
                       *enc, "-c:a", "aac", out]
    if size is None:
        raise ValueError("joining re-encoded pieces needs the first piece's size")
    w, h, fps = size
    w, h = w - w % 2, h - h % 2
    cmd = list(base)
    for s in segments:
        cmd += ["-ss", f"{s.start_s:.6f}", "-t", f"{s.duration_s:.6f}", "-i", s.path]
    chains = [f"[{i}:v:0]scale={w}:{h},setsar=1,fps={fps:.6f},format=yuv420p[v{i}]" for i in range(len(segments))]
    joined = "".join(f"[v{i}]" for i in range(len(segments)))
    graph = ";".join(chains) + f";{joined}concat=n={len(segments)}:v=1:a=0[v]"
    return cmd + ["-filter_complex", graph, "-map", "[v]", *enc, out]


class PendingClip:
    """A finished clip still under its temporary name: :meth:`commit` renames it to its final name (over any
    file there), :meth:`discard` removes it.  Both are safe to call more than once."""

    def __init__(self, part: str, final: str):
        self.part, self.final = part, final

    def commit(self) -> str:
        if os.path.exists(self.part):
            os.replace(self.part, self.final)
        return self.final

    def discard(self) -> None:
        with contextlib.suppress(OSError):
            os.remove(self.part)


def part_name(final: str) -> str:
    """``dir/name.mp4`` -> ``dir/name.part.mp4`` (the extension stays: ffmpeg picks the container from it)."""
    stem, ext = os.path.splitext(final)
    return f"{stem}.part{ext}"


def write_clip(segments: Sequence[Segment], mode: str, path: str | os.PathLike, *,
               stale: Optional[Callable[[], bool]] = None,
               progress: Optional[Callable[[float], None]] = None,
               ffmpeg: Optional[str] = None) -> PendingClip:
    """Write the clip for ``segments`` as ``path`` (extension: :func:`clip_extension`), under a temporary name.

    ``progress(fraction)`` is called as ffmpeg reports how far it has got; ``stale()`` returning True stops ffmpeg and
    raises :class:`VideoExportCancelled`.  The returned :class:`PendingClip` renames the file when the caller is
    ready (after the data export, say); any error removes the temporary file before it propagates."""
    segments = list(segments)
    if not segments:
        raise VideoExportError("There is no video for this interval.")
    if mode not in MODES:
        raise ValueError(f"unknown video mode {mode!r}")
    exe = ffmpeg or find_ffmpeg()
    if exe is None:
        raise VideoExportError("The video clip needs the ffmpeg program, which was not found. The imageio-ffmpeg "
                               "package brings one (pip install imageio-ffmpeg); an ffmpeg on the PATH works too.")
    final = os.path.realpath(os.fspath(path))
    if any(os.path.realpath(s.path) == final for s in segments):
        raise VideoExportError("The clip would replace the original video file: choose another file name.")
    part = part_name(final)
    segments, starts = fit_segments(exe, segments)
    if not segments:
        raise VideoExportError("The video has nothing in this interval (it ends before it starts).")
    total = sum(s.duration_s for s in segments)
    tail: deque[str] = deque(maxlen=12)
    seen = [0.0]
    with tempfile.TemporaryDirectory(prefix="adichtion-clip-") as work:
        size = probe_video(exe, segments[0].path) if mode == "h264" and len(segments) > 1 else None
        cmd = build_command(exe, segments, mode, part, work, size, starts)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                    **_hidden_window())
        except OSError as exc:
            raise VideoExportError(f"ffmpeg could not be started ({exc.strerror or exc}).") from exc

        def drain() -> None:                          # ffmpeg's messages, read so that the pipe never fills
            for line in proc.stderr:                  # type: ignore[union-attr]
                if line.strip():
                    tail.append(line.strip())

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            for line in proc.stdout:                  # type: ignore[union-attr]
                if stale is not None and stale():
                    proc.terminate()
                    raise VideoExportCancelled()
                key, _, value = line.strip().partition("=")
                if progress is not None and key == "out_time_us" and value.isdigit() and total > 0:
                    seen[0] = max(seen[0], int(value) / 1e6)
                    progress(min(1.0, int(value) / 1e6 / total))
                elif key == "out_time_us" and value.isdigit():
                    seen[0] = max(seen[0], int(value) / 1e6)
            code = proc.wait()
            reader.join(5)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            with contextlib.suppress(OSError):
                os.remove(part)
            raise
    if code == 0 and os.path.exists(part) and seen[0] <= 0.0:
        with contextlib.suppress(OSError):
            os.remove(part)
        raise VideoExportError("ffmpeg wrote no video for this interval.")
    if code != 0 or not os.path.exists(part):
        with contextlib.suppress(OSError):
            os.remove(part)
        detail = " ".join(tail) or f"exit code {code}"
        raise VideoExportError(f"ffmpeg could not write the clip: {detail}")
    if progress is not None:
        progress(1.0)
    return PendingClip(part, final)
