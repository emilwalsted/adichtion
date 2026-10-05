"""The file's time axis (Qt-free)."""
import pytest

from adichtion import AdichtFile
from adichtion.ui.timeline import Timeline


def test_blocks_abut_and_positions_resolve(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        tl = Timeline(f)
        assert [b.number for b in tl.blocks] == [1, 2]
        assert tl.blocks[0].start_s == 0.0 and tl.blocks[1].start_s == pytest.approx(40.0)
        assert tl.duration_s == pytest.approx(45.0)
        assert tl.boundaries == [pytest.approx(40.0)]
        # a time inside block 2 maps to that block's own tick
        pos = tl.locate(42.5)
        assert pos.block.number == 2 and pos.tick == 2500 and pos.block_time_s == pytest.approx(2.5)
        assert (pos.clock - f.records[1].start_local).total_seconds() == pytest.approx(2.5)
        # clamped at both ends
        assert tl.locate(-3).tick == 0 and tl.locate(-3).block.number == 1
        assert tl.locate(1e9).block.number == 2 and tl.locate(1e9).tick == f.records[1].ticks - 1
        assert tl.file_time(1, 2500) == pytest.approx(42.5)
        assert tl.describe(42.5).startswith("Block 2 · 2.500 s · ")


def test_selections_split_at_block_boundaries(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        tl = Timeline(f)
        sels = tl.selections(39.0, 41.0)
        assert [(s.record, s.start, s.stop) for s in sels] == [(0, 39000, 40000), (1, 0, 1000)]
        assert tl.selections(10.0, 12.0) == tl.selections(12.0, 10.0)
        assert [(s.record, s.start, s.stop) for s in tl.selections(10.0, 12.0)] == [(0, 10000, 12000)]
        assert tl.selections(44.0, 50.0)[0].stop == f.records[1].ticks


def test_comments_carry_file_times(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        tl = Timeline(f)
        times = [round(c.file_time_s, 3) for c in tl.comments]
        assert times == [0.01, 16.005, 39.995, 42.5]
        assert tl.comments[-1].block.number == 2 and tl.comments[-1].comment.text == "short"


def test_a_comments_interval_runs_to_just_before_the_next_comment(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        tl = Timeline(f)
        # one tick (1 ms) short of the next comment; the last comment runs to the end of the file
        assert tl.comment_interval(0) == (pytest.approx(0.01), pytest.approx(16.004))
        assert tl.comment_interval(1) == (pytest.approx(16.005), pytest.approx(39.994))
        assert tl.comment_interval(2) == (pytest.approx(39.995), pytest.approx(42.499))
        assert tl.comment_interval(3) == (pytest.approx(42.5), pytest.approx(tl.duration_s))
        # a block's interval is its own span
        assert tl.block_interval(0) == (0.0, pytest.approx(40.0))
        assert tl.block_interval(1) == (pytest.approx(40.0), pytest.approx(45.0))
