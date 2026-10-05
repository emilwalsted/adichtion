"""``adichtion export --video`` writes the clip of the interval beside the data file."""
import os

import pytest

from adichtion import __main__ as entry
from adichtion import videoclip
from test_videoclip import two_blocks, read_clip   # noqa: F401  (fixture and helper; tests/ is on the path)

needs_ffmpeg = pytest.mark.skipif(videoclip.find_ffmpeg() is None, reason="no ffmpeg program")


def run(*args):
    return entry.main(["adichtion", *args])


@needs_ffmpeg
def test_video_h264_writes_data_and_an_mp4_clip(two_blocks, tmp_path, capsys):
    out = tmp_path / "interval.csv"
    assert run("export", two_blocks, "-o", str(out), "--record", "1", "--start", "1", "--stop", "3", "--video", "h264") == 0
    assert out.exists() and (tmp_path / "interval.mp4").exists()
    assert not list(tmp_path.glob("*.part*"))
    assert "video clip" in capsys.readouterr().out
    first, n, _fps = read_clip(str(tmp_path / "interval.mp4"))
    assert abs(first - round(1.5 * 25)) <= 1 and abs(n - 2 * 25) <= 2      # 2 s from 1.5 s into the file


@needs_ffmpeg
def test_video_original_keeps_the_source_container_and_says_so(two_blocks, tmp_path, capsys):
    out = tmp_path / "interval.txt"
    assert run("export", two_blocks, "-o", str(out), "--start", "1", "--stop", "3", "--video", "original") == 0
    assert (tmp_path / "interval.wmv").exists()
    assert "without re-encoding" in capsys.readouterr().out


@needs_ffmpeg
def test_all_records_join_into_one_clip(two_blocks, tmp_path):
    out = tmp_path / "all.csv"
    assert run("export", two_blocks, "-o", str(out), "--all-records", "--video", "h264") == 0
    _f, one, _fps = read_clip(str(tmp_path / "all.mp4"))
    assert run("export", two_blocks, "-o", str(tmp_path / "b1.csv"), "--record", "1", "--video", "h264") == 0
    _f, single, _fps = read_clip(str(tmp_path / "b1.mp4"))
    assert abs(one - 2 * single) <= 6                                # both blocks, not just the first


def test_without_the_option_no_clip_is_written(two_blocks, tmp_path):
    out = tmp_path / "plain.csv"
    assert run("export", two_blocks, "-o", str(out), "--start", "1", "--stop", "2") == 0
    assert [p.name for p in tmp_path.iterdir()] == ["plain.csv"]


def test_a_file_without_video_is_an_error_and_writes_nothing(synthetic, tmp_path):
    out = tmp_path / "x.csv"
    with pytest.raises(SystemExit) as e:
        run("export", synthetic["path"], "-o", str(out), "--video", "h264")
    assert "no video" in str(e.value)
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_a_failing_data_export_leaves_no_clip_behind(two_blocks, tmp_path, monkeypatch):
    import adichtion.cli as cli

    def boom(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(cli, "export_selections", boom)
    out = tmp_path / "bad.csv"
    with pytest.raises(SystemExit):
        run("export", two_blocks, "-o", str(out), "--start", "1", "--stop", "2", "--video", "h264")
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_data_and_clip_may_not_share_a_name(two_blocks, tmp_path):
    out = tmp_path / "same.mp4"
    with pytest.raises(SystemExit) as e:
        run("export", two_blocks, "-o", str(out), "--format", "csv", "--start", "1", "--stop", "2", "--video", "h264")
    assert "same name" in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_an_unknown_mode_is_refused_by_the_parser(two_blocks, tmp_path):
    with pytest.raises(SystemExit) as e:
        run("export", two_blocks, "-o", str(tmp_path / "a.csv"), "--video", "bogus")
    assert e.value.code == 2


def test_without_ffmpeg_the_error_is_explained_and_nothing_is_written(two_blocks, tmp_path, monkeypatch):
    monkeypatch.setattr(videoclip, "find_ffmpeg", lambda: None)
    with pytest.raises(SystemExit) as e:
        run("export", two_blocks, "-o", str(tmp_path / "n.csv"), "--start", "1", "--stop", "2", "--video", "h264")
    assert "ffmpeg" in str(e.value)
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
def test_an_interrupt_during_the_data_export_leaves_no_clip(two_blocks, tmp_path, monkeypatch):
    import adichtion.cli as cli

    def stop(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "export_selections", stop)
    with pytest.raises(KeyboardInterrupt):
        run("export", two_blocks, "-o", str(tmp_path / "k.csv"), "--start", "1", "--stop", "2", "--video", "h264")
    assert list(tmp_path.iterdir()) == []
