import numpy as np

from adichtion.blocks import packbits_decode, packbits_encode


def test_known_vectors():
    assert packbits_decode(bytes([0x7F]) + bytes(range(128))) == bytes(range(128))
    assert packbits_decode(bytes([0x81, 0x00])) == bytes(128)
    assert packbits_decode(bytes([0xFE, 0xAB])) == b"\xab" * 3
    # 0x80 is a run of 129 in LabChart's PackBits (Apple's spec makes it a no-op); seen in real files
    assert packbits_decode(bytes([0x80, 0x00, 0x00, 0x41])) == bytes(129) + b"A"


def test_roundtrip_random_and_runs():
    rng = np.random.default_rng(0)
    for n in (0, 1, 2, 3, 200, 5000):
        data = rng.integers(0, 256, n).astype(np.uint8).tobytes()
        assert packbits_decode(packbits_encode(data)) == data
        runs = b"".join(bytes([rng.integers(0, 256)]) * int(rng.integers(1, 400)) for _ in range(n // 50 + 1))
        assert packbits_decode(packbits_encode(runs)) == runs
