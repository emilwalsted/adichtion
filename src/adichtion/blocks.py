"""Sample data layer: the ``Blocks_0`` stream.

The stream is a 4-byte magic (``55 55 03 aa``) followed by a chain of
*sub-blocks*.  A sub-block holds the samples of one channel for one 16000-tick
block of one record; sub-blocks are stored block-major (block 0: channel 1,
channel 2, ...; block 1: ...).

Sub-block header (16 bytes)::

    int32 n        the sub-block occupies n + 4 bytes in total
    int32 check    0 for packed sub-blocks, a checksum(?) for unpacked ones
    int32 -n       redundant length (self-check)
    int32 flags    0x100 | FLAG_32BIT | FLAG_PACKED | FLAG_UNPACKED

followed by the *body* (``n - 12`` bytes).  For packed sub-blocks the body
starts with ``int32 raw_size`` (= samples * bytes per sample) and continues
with the PackBits-compressed bit planes; for unpacked sub-blocks the body is
the little-endian sample array itself.

Bit-plane coding (see ``docs/FORMAT.md`` for the full description):

* the buffer holds P planes of ``4 * ceil(samples / 32)`` bytes; plane p holds
  bit p of the per-sample symbol, sample i at bit ``i % 8`` of byte ``i // 8``;
* int16 channels: P = 17, symbol = ``2 * |d| + (d < 0)`` where ``d`` is the
  difference to the previous sample (the first sample is stored as-is);
* float32 channels: P = 32, symbol = IEEE-754 bit pattern of a float32
  *difference* to the previous sample, planes stored from bit 31 down to bit 0.
  Sample 31 re-bases the chain: ``d[31]`` also contains the previous block's
  last value, so a block is self-contained from sample 31 on and only the
  first 31 samples need the previous block (see :func:`decode_float32`).

All decoders are pure numpy and stateless; :class:`SubBlockIndex` maps the
sub-blocks of a file to ``(record, block, channel)`` without decoding anything.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, Sequence

import numpy as np

MAGIC = bytes.fromhex("555503aa")
HEADER_SIZE = 16
BLOCK_TICKS = 16000

FLAG_32BIT = 0x1
FLAG_PACKED = 0x4
FLAG_BASE = 0x100
FLAG_UNPACKED = 0x80000000

INT16_PLANES = 17
FLOAT32_PLANES = 32
REBASE_INDEX = 31  # float32 blocks re-base their delta chain at this sample


class BlocksFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SubBlock:
    """Location of one sub-block inside the ``Blocks_0`` stream (nothing decoded)."""

    index: int          # running index in the stream
    offset: int         # offset of the 16-byte header within the stream
    body_offset: int    # offset of the body (after the header)
    body_size: int
    flags: int
    n_samples: int

    @property
    def is_float(self) -> bool:
        return bool(self.flags & FLAG_32BIT)

    @property
    def is_packed(self) -> bool:
        return bool(self.flags & FLAG_PACKED) and not (self.flags & FLAG_UNPACKED)

    @property
    def dtype(self) -> str:
        return "float32" if self.is_float else "int16"

    @property
    def sample_size(self) -> int:
        return 4 if self.is_float else 2


# -- stream walking ------------------------------------------------------------
def iter_subblocks(stream: bytes | memoryview) -> Iterator[SubBlock]:
    """Walk the sub-block chain of a ``Blocks_0`` stream (headers only)."""
    view = memoryview(stream)
    if bytes(view[:4]) != MAGIC:
        raise BlocksFormatError("Blocks stream does not start with 55 55 03 aa")
    pos = 4
    idx = 0
    total = len(view)
    while pos + HEADER_SIZE <= total:
        n, _check, negn, flags = struct.unpack_from("<iiii", view, pos)
        if negn != -n or n < 12:
            raise BlocksFormatError(f"bad sub-block header at stream offset {pos}")
        body_offset = pos + HEADER_SIZE
        body_size = n - 12
        ssize = 4 if flags & FLAG_32BIT else 2
        if flags & FLAG_PACKED and not flags & FLAG_UNPACKED:
            raw, = struct.unpack_from("<i", view, body_offset)
            n_samples = raw // ssize
        else:
            n_samples = body_size // ssize
        yield SubBlock(index=idx, offset=pos, body_offset=body_offset, body_size=body_size,
                       flags=flags, n_samples=n_samples)
        idx += 1
        pos += n + 4


# -- PackBits ------------------------------------------------------------------
def packbits_decode(src: bytes | memoryview) -> bytes:
    """Apple PackBits (TN1023): header ``k``: 0..127 copy ``k+1`` literal bytes,
    129..255 repeat the next byte ``257-k`` times, 128 is a no-op."""
    src = bytes(src)
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        k = src[i]
        i += 1
        if k < 128:
            out += src[i:i + k + 1]
            i += k + 1
        elif k > 128:
            out += src[i:i + 1] * (257 - k)
            i += 1
    return bytes(out)


def packbits_encode(data: bytes) -> bytes:
    """Straightforward PackBits encoder (used by the test-suite writer)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        # run?
        run = 1
        while i + run < n and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 3:
            out.append(257 - run)
            out.append(data[i])
            i += run
            continue
        # literal until the next run of >= 3 or 128 bytes
        j = i
        while j < n and j - i < 128:
            if j + 2 < n and data[j] == data[j + 1] == data[j + 2]:
                break
            j += 1
        out.append(j - i - 1)
        out += data[i:j]
        i = j
    return bytes(out)


# -- bit planes ----------------------------------------------------------------
def plane_bytes(n_samples: int) -> int:
    return 4 * ((n_samples + 31) // 32)


def unpack_planes(buf: bytes, n_planes: int, n_samples: int) -> np.ndarray:
    """Return an ``(n_planes, n_samples)`` uint8 array of bits.  Plane order is
    the storage order (the caller decides what plane 0 means)."""
    pb = plane_bytes(n_samples)
    if len(buf) % n_planes == 0 and len(buf) // n_planes != pb:
        pb = len(buf) // n_planes  # trust the buffer if the padding rule ever differs
    need = n_planes * pb
    if len(buf) < need:
        buf = buf + bytes(need - len(buf))
    planes = np.frombuffer(buf, dtype=np.uint8, count=need).reshape(n_planes, pb)
    return np.unpackbits(planes, axis=1, bitorder="little")[:, :n_samples]


def decode_int16_planes(buf: bytes, n_samples: int) -> np.ndarray:
    """Decode a 17-plane sign-magnitude delta buffer to int16 counts (as int32)."""
    bits = unpack_planes(buf, INT16_PLANES, n_samples).astype(np.int64)
    m = np.zeros(n_samples, dtype=np.int64)
    for p in range(INT16_PLANES):
        m |= bits[p] << p
    d = np.where(m & 1, -(m >> 1), m >> 1)
    x = (np.cumsum(d) + 32768) % 65536 - 32768  # int16 arithmetic wraps
    return x.astype(np.int32)


def decode_float32_deltas(buf: bytes, n_samples: int) -> np.ndarray:
    """Return the stored float32 differences of a 32-plane buffer (plane 0 = bit 31)."""
    bits = unpack_planes(buf, FLOAT32_PLANES, n_samples).astype(np.uint32)[::-1]
    v = np.zeros(n_samples, dtype=np.uint32)
    for p in range(FLOAT32_PLANES):
        v |= bits[p] << np.uint32(p)
    return v.view(np.float32)


def decode_float32(deltas: np.ndarray, prev_last: float = 0.0) -> np.ndarray:
    """Turn stored float32 differences into sample values.

    ``prev_last`` is the last value of the previous block of the same record
    (0 for the first block).  The chain re-bases at sample :data:`REBASE_INDEX`:
    the stored ``d[31]`` includes ``prev_last``, so ``cumsum`` from zero is
    correct from sample 31 on and only samples 0..30 need ``prev_last`` added.
    """
    x = np.cumsum(deltas.astype(np.float64))
    if len(x) > REBASE_INDEX:
        x[:REBASE_INDEX] += prev_last
    else:
        x += prev_last
    return x.astype(np.float32)


# -- sub-block decoding ----------------------------------------------------------
def decode_subblock(body: bytes | memoryview, sb: SubBlock, prev_last: float = 0.0) -> np.ndarray:
    """Decode one sub-block.  Returns int32 counts (int16 channels) or float32."""
    body = bytes(body)
    if not sb.is_packed:
        arr = np.frombuffer(body, dtype="<f4" if sb.is_float else "<i2", count=sb.n_samples)
        return arr.astype(np.float32) if sb.is_float else arr.astype(np.int32)
    buf = packbits_decode(body[4:])
    if sb.is_float:
        return decode_float32(decode_float32_deltas(buf, sb.n_samples), prev_last)
    return decode_int16_planes(buf, sb.n_samples)


# -- mapping sub-blocks to records/blocks/channels --------------------------------
class SubBlockIndex:
    """Maps ``(record, block, channel)`` to sub-blocks.

    ``blocks_per_record`` gives the tick count of every block of every record
    (from the Map stream).  The channel count is derived: the number of
    sub-blocks must be ``n_channels * total_blocks``.
    """

    def __init__(self, subblocks: Sequence[SubBlock], blocks_per_record: Sequence[Sequence[int]]):
        self.subblocks = list(subblocks)
        self.blocks_per_record = [list(b) for b in blocks_per_record]
        total_blocks = sum(len(b) for b in self.blocks_per_record)
        if total_blocks == 0 or len(self.subblocks) % total_blocks:
            raise BlocksFormatError(
                f"{len(self.subblocks)} sub-blocks cannot be divided over {total_blocks} blocks")
        self.n_channels = len(self.subblocks) // total_blocks
        # first sub-block index of every record
        self._record_base: list[int] = []
        base = 0
        for blocks in self.blocks_per_record:
            self._record_base.append(base)
            base += len(blocks) * self.n_channels
        self._check_sample_counts()

    def _check_sample_counts(self) -> None:
        for r, blocks in enumerate(self.blocks_per_record):
            for b, ticks in enumerate(blocks):
                for ch in range(self.n_channels):
                    sb = self.get(r, b, ch)
                    if sb.n_samples % ticks and ticks % sb.n_samples:
                        raise BlocksFormatError(
                            f"record {r} block {b} channel {ch}: {sb.n_samples} samples for {ticks} ticks")

    def get(self, record: int, block: int, channel: int) -> SubBlock:
        return self.subblocks[self._record_base[record] + block * self.n_channels + channel]

    def channel_subblocks(self, record: int, channel: int) -> list[SubBlock]:
        return [self.get(record, b, channel) for b in range(len(self.blocks_per_record[record]))]

    def channel_dtype(self, channel: int) -> str:
        """``"float32"`` if any sub-block of the channel is 32-bit, else ``"int16"``."""
        for r in range(len(self.blocks_per_record)):
            for b in range(len(self.blocks_per_record[r])):
                if self.get(r, b, channel).is_float:
                    return "float32"
        return "int16"

    def samples_per_tick(self, record: int, channel: int) -> float:
        """Sampling rate of the channel relative to the record's tick rate."""
        blocks = self.blocks_per_record[record]
        if not blocks:
            return 1.0
        sb = self.get(record, 0, 0 if self.n_channels == 0 else channel)
        return sb.n_samples / blocks[0]
