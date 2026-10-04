"""Zero-copy access to the Blocks stream: a memoryview over the mmap instead of a copy."""
import gc
import mmap
import os

import numpy as np
import pytest

import adichtion.file as file_mod
from adichtion import AdichtContainer, AdichtFile
from adichtion.synth import write_synthetic

from conftest import make_document
from test_macdoc import mac  # noqa: F401  (the synthetic Mac document fixture)


@pytest.fixture(scope="module")
def fragmented(tmp_path_factory):
    channels, records = make_document()
    path = tmp_path_factory.mktemp("frag") / "fragmented.adicht"
    write_synthetic(path, channels, records, fragment=True)
    return str(path)


def _all_samples(f):
    out = []
    for r in range(len(f.records)):
        for ch in range(f.n_channels):
            for b in range(len(f.records[r].block_ticks)):
                out.append(np.array(f.decode_block(r, b, ch)))
    return out


def _same(a, b):
    assert len(a) == len(b) and all(np.array_equal(x, y) for x, y in zip(a, b))


def test_zero_copy_is_a_view_over_the_mmap_and_decodes_identically(synthetic):
    with AdichtFile(synthetic["path"], zero_copy=False) as copy:
        assert isinstance(copy._blocks, bytes)
        expected = _all_samples(copy)
    with AdichtFile(synthetic["path"], zero_copy=True) as f:
        assert isinstance(f._blocks, memoryview) and isinstance(f._container.data, mmap.mmap)
        assert f.zero_copy is True
        _same(_all_samples(f), expected)
        assert [c.text for c in f.records[0].comments] == ["start", "15,5 inH2O", "stop"]
        assert f.walk_subblocks() == f.index.subblocks            # the scan sees the same chain


def test_repeated_open_and_close_never_hits_buffererror(synthetic):
    for _ in range(40):
        f = AdichtFile(synthetic["path"], zero_copy=True)
        f.decode_block(0, 0, 0)
        f.decode_block(0, 1, 2)
        f.close()
        assert f._container.data.closed


def test_with_block_and_walk_leave_nothing_exported(synthetic):
    f = AdichtFile(synthetic["path"], zero_copy=True)
    try:
        f.walk_subblocks()
        f.block_table(0)
    finally:
        f.close()
    assert f._container.data.closed


def test_a_fragmented_chain_falls_back_to_a_copy(fragmented, synthetic):
    with AdichtFile(synthetic["path"], zero_copy=False) as copy:
        expected = _all_samples(copy)
    with AdichtFile(fragmented, zero_copy=True) as f:
        assert isinstance(f._blocks, bytes) and f.zero_copy is False
        _same(_all_samples(f), expected)
    with AdichtContainer(fragmented) as c:
        entry = c.find_stream("Blocks_0")
        assert list(c.chain(entry.first, entry.last)) != list(range(entry.first, entry.last + 1))
        assert c.stream_view(entry) is None
        assert len(c.read_stream(entry)) == entry.size


def test_stream_view_equals_read_stream(synthetic):
    with AdichtContainer(synthetic["path"]) as c:
        for e in c.streams().values():
            v = c.stream_view(e)
            if v is not None:
                assert bytes(v) == c.read_stream(e)
                v.release()
        assert c.stream_view(c.find_stream("Blocks_0")) is not None
        assert c.stream_view(c.find_stream("Blocks_0")).nbytes == c.find_stream("Blocks_0").size


def test_auto_mode_uses_the_size_threshold(synthetic, monkeypatch):
    size = os.path.getsize(synthetic["path"])
    monkeypatch.setattr(file_mod, "ZERO_COPY_MIN_BYTES", size + 1)
    monkeypatch.delenv("ADICHTION_ZERO_COPY", raising=False)
    with AdichtFile(synthetic["path"]) as f:
        assert isinstance(f._blocks, bytes) and not f.zero_copy
    monkeypatch.setattr(file_mod, "ZERO_COPY_MIN_BYTES", 1)
    with AdichtFile(synthetic["path"]) as f:
        assert isinstance(f._blocks, memoryview) and f.zero_copy


def test_environment_override(synthetic, monkeypatch):
    monkeypatch.setattr(file_mod, "ZERO_COPY_MIN_BYTES", 1)
    monkeypatch.setenv("ADICHTION_ZERO_COPY", "0")
    with AdichtFile(synthetic["path"]) as f:
        assert isinstance(f._blocks, bytes)
    monkeypatch.setattr(file_mod, "ZERO_COPY_MIN_BYTES", 10 ** 12)
    monkeypatch.setenv("ADICHTION_ZERO_COPY", "1")
    with AdichtFile(synthetic["path"]) as f:
        assert isinstance(f._blocks, memoryview)
    monkeypatch.setenv("ADICHTION_ZERO_COPY", "auto")
    with AdichtFile(synthetic["path"]) as f:
        assert isinstance(f._blocks, bytes)
    # an explicit argument beats the environment
    monkeypatch.setenv("ADICHTION_ZERO_COPY", "0")
    with AdichtFile(synthetic["path"], zero_copy=True) as f:
        assert isinstance(f._blocks, memoryview)


def test_a_failed_open_closes_the_mapping(synthetic, monkeypatch):
    seen = []

    class Spy(AdichtContainer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen.append(self)

    def boom(*a, **k):
        raise ValueError("index is bad")

    monkeypatch.setattr(file_mod, "AdichtContainer", Spy)
    monkeypatch.setattr(file_mod, "SubBlockIndex", boom)
    with pytest.raises(ValueError, match="index is bad"):
        AdichtFile(synthetic["path"], zero_copy=True)
    assert seen and seen[0].data.closed


def test_a_failure_while_walking_leaves_no_view_behind(synthetic, monkeypatch):
    """The traceback of the failure keeps the frames alive; closing must still work."""
    seen = []

    class Spy(AdichtContainer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen.append(self)

    monkeypatch.setattr(file_mod, "AdichtContainer", Spy)
    monkeypatch.setattr(file_mod, "_subblocks_from_map",
                        lambda blocks, chart: file_mod.iter_subblocks(bytes(blocks)[8:]))
    with pytest.raises(Exception):
        AdichtFile(synthetic["path"], zero_copy=True)
    gc.collect()
    assert seen and seen[0].data.closed


def test_close_with_a_stray_view_does_not_raise_and_use_after_close_is_an_error(synthetic):
    f = AdichtFile(synthetic["path"], zero_copy=True)
    stray = f._body(f.index.subblocks[0])
    f.close()                                   # a view that outlives close() must not make close() fail
    del stray
    gc.collect()
    with pytest.raises(ValueError):
        f.decode_block(0, 1, 1)                 # never a crash on a closed mapping
    f.close()                                   # and closing twice is harmless


def test_mac_documents_are_read_into_memory_whatever_is_asked(mac):
    with AdichtFile(mac["path"], zero_copy=True) as f:
        assert f.kind == "mac" and f.zero_copy is False and isinstance(f._blocks, bytes)
        assert len(f.decode_block(0, 0, 0)) > 0
