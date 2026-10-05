"""Video clips for exported intervals (adichtion.videoclip): which piece of which file, and the clip itself.

The source videos are made here: frame numbers written as a row of squares (as tests/ui/test_video.py does), so
a clip's first frame and length can be read back exactly; ffmpeg turns them into the WMV the LabChart for Windows
writes.  Skipped without OpenCV or an ffmpeg program."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from adichtion import videoclip
from adichtion.file import AdichtFile, Selection
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic
from adichtion.video import movies_folder

cv2 = pytest.importorskip("cv2")
FFMPEG = videoclip.find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg program")

FPS = 25
SECONDS = 8
BITS = 9                      # 2**9 > 25 * 8 frames
W, H = 160, 120
#           file name        start    stop   video start  offset into the file of the block's first tick
OFFSETS = (0.50, 0.20)


def marker_video(path: str, seconds: int = SECONDS) -> None:
    """A WMV (WMV2, lossy) whose frame number is a row of white squares; the intermediate MJPG file is removed."""
    tmp = path + ".avi"
    w = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    assert w.isOpened()
    for i in range(FPS * seconds):
        frame = np.full((H, W, 3), 40, np.uint8)
        for k in range(BITS):
            if i >> k & 1:
                frame[50:70, 6 + 16 * k: 18 + 16 * k] = 255
        w.write(frame)
    w.release()
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", tmp, "-c:v", "wmv2", "-q:v", "2", "-g", str(FPS),
                    path], check=True)
    os.remove(tmp)


def frame_number(frame) -> int:
    n = 0
    for k in range(BITS):
        if frame[60, 12 + 16 * k, 0] > 128:
            n |= 1 << k
    return n


def read_clip(path: str):
    """(number of the first frame, frames, fps) of a clip, by decoding it."""
    cap = cv2.VideoCapture(path)
    assert cap.isOpened()
    first, n = None, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if first is None:
            first = frame_number(fr)
        n += 1
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return first, n, fps


@pytest.fixture(scope="module")
def two_blocks(tmp_path_factory):
    """A document of two 5 s blocks, each with a clip (Record1.wmv, Record2.wmv); returns its path."""
    folder = tmp_path_factory.mktemp("clips")
    ch = [SynthChannel("Flow", "int16", None, 0.2)]
    recs = []
    for i, off in enumerate(OFFSETS):
        r = SynthRecord(5000, videos=(f"Record{i + 1}.wmv", 100.0, 105.0, 100.0 - off, off))
        r.data = {0: np.zeros(5000, np.int64)}
        recs.append(r)
    path = str(folder / "two.adicht")
    write_synthetic(path, ch, recs, video=True)
    os.makedirs(movies_folder(path))
    for i in range(2):
        marker_video(os.path.join(movies_folder(path), f"Record{i + 1}.wmv"))
    return path


@pytest.fixture
def doc(two_blocks):
    f = AdichtFile(two_blocks)
    yield f
    f.close()


def test_a_selection_maps_to_its_place_in_the_file(doc):
    segs, missing = videoclip.video_segments(doc, [Selection(0, 1000, 3000)])
    assert missing == [] and len(segs) == 1
    s = segs[0]
    assert s.record == 0 and s.path.endswith("Record1.wmv")
    assert s.start_s == pytest.approx(0.50 + 1.0) and s.duration_s == pytest.approx(2.0)


def test_an_interval_that_begins_before_the_clip_is_cut_at_the_start_of_the_file(doc, monkeypatch):
    clip = doc.videos[0]
    monkeypatch.setattr(type(clip), "file_time", lambda self, t: t - 0.3 if self is clip else t, raising=False)
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 1000)])
    assert segs[0].start_s == 0.0 and segs[0].duration_s == pytest.approx(0.7)


def test_blocks_without_video_are_reported_not_skipped_silently(two_blocks, tmp_path):
    path = str(tmp_path / "copy.adicht")
    shutil.copy(two_blocks, path)
    shutil.copytree(movies_folder(two_blocks), movies_folder(path))
    os.remove(os.path.join(movies_folder(path), "Record2.wmv"))
    with AdichtFile(path) as f:
        segs, missing = videoclip.video_segments(f, [Selection(0, 0, 1000), Selection(1, 0, 1000)])
        assert [x.record for x in segs] == [0] and missing == [2]
        assert "no video for block 2" in videoclip.segment_note("h264", segs, missing)
        assert videoclip.has_video(f, [Selection(1, 0, 10)]) is False


def test_has_video_looks_for_a_file(doc):
    assert videoclip.has_video(doc, [Selection(0, 0, 10)])


def test_the_extension_follows_the_mode(doc):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 1000)])
    assert videoclip.clip_extension("original", segs) == ".wmv"
    assert videoclip.clip_extension("h264", segs) == ".mp4"


def test_an_h264_clip_is_frame_accurate_and_plays_as_h264(doc, tmp_path):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 1000, 3000)])         # 2 s from 1.5 s into the file
    seen = []
    pending = videoclip.write_clip(segs, "h264", tmp_path / "interval.mp4", progress=seen.append)
    assert os.path.exists(pending.part) and not os.path.exists(pending.final)
    out = pending.commit()
    assert out == os.path.realpath(tmp_path / "interval.mp4") and os.path.exists(out) and not os.path.exists(pending.part)
    first, n, fps = read_clip(out)
    assert abs(first - round(1.5 * FPS)) <= 1                                     # frame 37 or 38 of the source
    assert abs(n - 2 * FPS) <= 2 and fps == pytest.approx(FPS, abs=0.5)
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", out], capture_output=True, text=True).stderr
    assert "Video: h264" in probe and "yuv420p" in probe
    assert seen and seen[-1] == 1.0 and all(0.0 <= p <= 1.0 for p in seen)


def test_an_original_clip_keeps_the_container_and_starts_at_a_key_frame_before_the_interval(doc, tmp_path):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 1000, 3000)])
    out = videoclip.write_clip(segs, "original", tmp_path / "interval.wmv").commit()
    assert out.endswith(".wmv")
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", out], capture_output=True, text=True).stderr
    assert "Video: wmv2" in probe                                                  # copied, not re-encoded
    first, n, _fps = read_clip(out)
    # a stream copy starts on a key frame it can find (here: the index of an ASF file has one entry a second, and
    # ffmpeg lands before it), never later than the interval, and runs to the interval's end
    assert first <= round(1.5 * FPS) - 1
    assert round(1.5 * FPS) - first <= 2 * FPS
    assert first + n >= round(3.5 * FPS) - 2


def test_several_blocks_become_one_h264_clip(doc, tmp_path):
    sels = [Selection(0, 0, 2000), Selection(1, 1000, 4000)]                       # 2 s + 3 s
    segs, missing = videoclip.video_segments(doc, sels)
    assert missing == [] and [s.record for s in segs] == [0, 1]
    out = videoclip.write_clip(segs, "h264", tmp_path / "joined.mp4").commit()
    first, n, _fps = read_clip(out)
    assert abs(first - round(0.5 * FPS)) <= 1 and abs(n - 5 * FPS) <= 3
    assert "joined into one clip" in videoclip.segment_note("h264", segs)


def test_several_blocks_become_one_original_clip(doc, tmp_path):
    sels = [Selection(0, 0, 2000), Selection(1, 1000, 4000)]
    segs, _ = videoclip.video_segments(doc, sels)
    out = videoclip.write_clip(segs, "original", tmp_path / "joined.wmv").commit()
    _first, n, _fps = read_clip(out)
    assert n >= 5 * FPS - 3


def test_a_cancelled_clip_leaves_nothing_behind(doc, tmp_path):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 4000)])
    with pytest.raises(videoclip.VideoExportCancelled):
        videoclip.write_clip(segs, "h264", tmp_path / "x.mp4", stale=lambda: True)
    assert os.listdir(tmp_path) == []


def test_a_discarded_clip_is_removed_and_the_old_file_is_untouched(doc, tmp_path):
    target = tmp_path / "x.mp4"
    target.write_bytes(b"old")
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 1000)])
    pending = videoclip.write_clip(segs, "h264", target)
    pending.discard()
    pending.discard()
    assert sorted(os.listdir(tmp_path)) == ["x.mp4"] and target.read_bytes() == b"old"


def test_a_failing_ffmpeg_gives_a_message_and_no_part_file(doc, tmp_path):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 1000)])
    bad = [videoclip.Segment(0, str(tmp_path / "not-a-video.wmv"), 0.0, 1.0)]
    (tmp_path / "not-a-video.wmv").write_bytes(b"nothing")
    with pytest.raises(videoclip.VideoExportError, match="ffmpeg could not write the clip"):
        videoclip.write_clip(bad, "h264", tmp_path / "out" / "x.mp4")
    assert segs and not (tmp_path / "out").exists()


def test_no_ffmpeg_is_explained(doc, tmp_path, monkeypatch):
    segs, _ = videoclip.video_segments(doc, [Selection(0, 0, 1000)])
    monkeypatch.setattr(videoclip, "find_ffmpeg", lambda: None)
    with pytest.raises(videoclip.VideoExportError, match="imageio-ffmpeg"):
        videoclip.write_clip(segs, "h264", tmp_path / "x.mp4")
    with pytest.raises(videoclip.VideoExportError, match="could not be started"):
        videoclip.write_clip(segs, "h264", tmp_path / "x.mp4", ffmpeg=str(tmp_path / "missing-ffmpeg"))


def test_pieces_in_different_containers_cannot_be_copied_together(tmp_path):
    segs = [videoclip.Segment(0, "a.wmv", 0.0, 1.0), videoclip.Segment(1, "b.mov", 0.0, 1.0)]
    with pytest.raises(videoclip.VideoExportError, match="different file types"):
        videoclip.build_command("ffmpeg", segs, "original", "out.wmv", str(tmp_path))
    assert videoclip.clip_extension("original", segs) == ".wmv"


def test_nothing_to_cut_is_an_error(tmp_path):
    with pytest.raises(videoclip.VideoExportError, match="no video"):
        videoclip.write_clip([], "h264", tmp_path / "x.mp4")
    with pytest.raises(ValueError):
        videoclip.write_clip([videoclip.Segment(0, "a.wmv", 0.0, 1.0)], "nonsense", tmp_path / "x.mp4")
