"""LabChart for Mac movies: the frame times and the capture stamps of the synchronisation
track, read from synthetic QuickTime files with LabChart's layout (MAC_FORMAT.md §7)."""
import numpy as np
import pytest

from adichtion.qtmovie import MovieError, read_movie_timing
from adichtion.synth import write_labchart_movie

CLOCK0 = 4_824_211_405          # µs: a block's start on the Microseconds() clock (as in mTim)


def _movie(offset_s=0.003405, first_lag_us=19_928, n=200, seed=5):
    """Irregular frame durations (1/600 s) and stamps that put every frame after the first
    ``offset_s`` earlier in the block than in the movie, as LabChart's movies do."""
    rng = np.random.default_rng(seed)
    durations = rng.integers(1, 120, n)
    durations[0] = 62
    movie_t = np.concatenate([[0], np.cumsum(durations)[:-1]]) / 600.0
    stamps = CLOCK0 + np.round((movie_t - offset_s) * 1e6).astype(np.int64)
    stamps[0] = CLOCK0 + first_lag_us                          # the first frame: captured after the start
    return durations, stamps, movie_t


@pytest.mark.parametrize("per_chunk,co64", [(3, False), (1, True), (7, False)])
def test_frame_times_stamps_and_offset(tmp_path, per_chunk, co64):
    durations, stamps, movie_t = _movie()
    p = tmp_path / "LabChartMovie1.mov"
    write_labchart_movie(p, durations, stamps, per_chunk=per_chunk, co64=co64)
    tm = read_movie_timing(p)
    np.testing.assert_allclose(tm.frame_times_s, movie_t)
    np.testing.assert_array_equal(tm.stamps_us, stamps)
    assert tm.duration_s == pytest.approx(durations.sum() / 600.0)
    assert tm.offset_s(CLOCK0) == pytest.approx(0.003405, abs=1e-6)    # the first frame does not count
    assert tm.offset_s(CLOCK0 + 1_000_000) == pytest.approx(1.003405, abs=1e-6)


def test_what_is_not_a_labchart_movie(tmp_path):
    durations, stamps, _t = _movie(n=10)
    empty = tmp_path / "placeholder.mov"
    empty.write_bytes(b"")                                     # a cloud placeholder that is not downloaded
    with pytest.raises(MovieError, match="empty"):
        read_movie_timing(empty)
    other = tmp_path / "notes.mov"
    other.write_bytes(b"\x00\x00\x00\x10junkjunkjunkjunk" * 4)
    with pytest.raises(MovieError, match="no moov"):
        read_movie_timing(other)
    plain = tmp_path / "plain.mov"
    write_labchart_movie(plain, durations, stamps, sync_track=False)
    with pytest.raises(MovieError, match="synchronisation track"):
        read_movie_timing(plain)
    short = tmp_path / "short.mov"
    write_labchart_movie(short, durations, stamps)
    data = bytearray(short.read_bytes())
    k = data.rfind(b"stsz")                                    # the sync track's stsz: claim one stamp fewer
    data[k + 12:k + 16] = (9).to_bytes(4, "big")
    short.write_bytes(bytes(data))
    with pytest.raises(MovieError, match="9 sync stamps for 10 video frames"):
        read_movie_timing(short)
    with pytest.raises(OSError):
        read_movie_timing(tmp_path / "missing.mov")
