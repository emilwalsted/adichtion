import numpy as np

from adichtion.blocks import (decode_float32, decode_float32_deltas, decode_int16_planes, decode_subblock,
                              iter_subblocks, plane_bytes, unpack_planes, MAGIC)
from adichtion.synth import encode_float32, encode_int16, pack_planes, subblock


def test_plane_bytes_padding():
    assert plane_bytes(16000) == 2000
    assert plane_bytes(6850) == 860      # 857 rounded up to a multiple of 4
    assert plane_bytes(12850) == 1608
    assert plane_bytes(1) == 4


def test_pack_unpack_planes():
    sym = np.array([0, 1, 2, 3, 255, 20, 7], dtype=np.uint64)
    buf = pack_planes(sym, 8, msb_first=False)
    bits = unpack_planes(buf, 8, len(sym)).astype(np.uint64)
    back = sum(bits[p] << np.uint64(p) for p in range(8))
    assert list(back) == list(sym)


def test_int16_roundtrip_with_wrap():
    rng = np.random.default_rng(1)
    x = np.clip(np.cumsum(rng.integers(-40, 41, 16000)), -32768, 32767)
    x[5000:5010] = [-32768, 32767, -32768, 32767, 0, 32767, -1, 1, -32768, 0]  # wrap-around deltas
    assert np.array_equal(decode_int16_planes(encode_int16(x), 16000), x)


def test_float32_roundtrip_and_rebase():
    rng = np.random.default_rng(2)
    a = (np.cumsum(rng.normal(0, 1, 16000)) + 1000).astype(np.float32)
    b = (a[-1] + np.cumsum(rng.normal(0, 1, 7200))).astype(np.float32)
    da = decode_float32_deltas(encode_float32(a, 0.0), 16000)
    xa = decode_float32(da, 0.0)
    assert np.allclose(xa, a, rtol=1e-6, atol=1e-3)
    db = decode_float32_deltas(encode_float32(b, float(a[-1])), 7200)
    xb = decode_float32(db, float(a[-1]))
    assert np.allclose(xb, b, rtol=1e-6, atol=1e-3)
    # the block is self-contained from sample 31 on: decoding with the wrong prev_last only hurts 0..30
    xb_wrong = decode_float32(db, 0.0)
    assert np.allclose(xb_wrong[31:], b[31:], rtol=1e-6, atol=1e-3)
    assert not np.allclose(xb_wrong[:31], b[:31])
    # and the stored deltas sum to the last value
    assert abs(float(np.sum(db.astype(np.float64))) - float(b[-1])) < 1e-2


def test_subblock_chain_and_flags():
    x16 = np.arange(-100, 100, dtype=np.int64)
    xf = np.linspace(0, 1, 40, dtype=np.float32)
    stream = MAGIC + subblock(x16, False) + subblock(xf, True) + subblock(xf, True, packed=False)
    sbs = list(iter_subblocks(stream))
    assert [(s.n_samples, s.dtype, s.is_packed) for s in sbs] == [(200, "int16", True), (40, "float32", True), (40, "float32", False)]
    bodies = [stream[s.body_offset: s.body_offset + s.body_size] for s in sbs]
    assert np.array_equal(decode_subblock(bodies[0], sbs[0]), x16)
    assert np.allclose(decode_subblock(bodies[1], sbs[1]), xf, atol=1e-6)
    assert np.array_equal(decode_subblock(bodies[2], sbs[2]), xf)
