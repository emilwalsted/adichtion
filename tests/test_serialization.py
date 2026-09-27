import struct

from adichtion.serialization import TAG, iter_prefixed_strings, iter_tagged_strings, read_prefixed, read_tagged
from adichtion.synth import prefixed, tagged


def test_prefixed_roundtrip():
    blob = prefixed("15,5 inH2O")
    assert read_prefixed(blob, 0) == ("15,5 inH2O", len(blob))


def test_prefixed_rejects_bad_length():
    blob = struct.pack("<ii", 5, -4) + "abcd\x00".encode("utf-16le")
    assert read_prefixed(blob, 0) is None


def test_tagged_and_scan_any_alignment():
    blob = b"\x01" + tagged("abc") + b"zz" + prefixed("def") + TAG + b"\x00"
    assert read_tagged(blob, 1) == ("abc", 1 + len(tagged("abc")))
    assert [s for _o, s in iter_tagged_strings(blob)] == ["abc"]
    assert [s for _o, s in iter_prefixed_strings(blob)] == ["abc", "def"]
