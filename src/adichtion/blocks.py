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
the little-endian sample array itself.  Unpacked sub-blocks do *not* carry
FLAG_32BIT even for float32 channels: their data type is the channel's
(:class:`SubBlockIndex` fixes this up from the Map stream or from the
channel's packed sub-blocks).

Bit-plane coding (see ``docs/FORMAT.md`` for the full description):

* the buffer holds P planes of ``4 * ceil(samples / 32)`` bytes; plane p holds
  bit p of the per-sample symbol, sample i at bit ``i % 8`` of byte ``i // 8``;
* int16 channels: P = 17, symbol = ``2 * |d| + (d < 0)`` where ``d`` is the
  difference to the previous sample (the first sample is stored as-is);
* float32 channels: P = 32, symbol = IEEE-754 bit pattern of a float32
  *difference* to the previous sample (the first sample of the block is
  stored as-is), planes stored from bit 31 down to bit 0, and the samples
  of every 32-sample word in *reverse* order (sample ``32k + j`` sits at bit
  ``31 - j`` of word ``k``).  A float32 block is therefore self-contained.

All decoders are pure numpy and stateless; :class:`SubBlockIndex` maps the
sub-blocks of a file to ``(record, block, channel)`` without decoding anything.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import Iterator, Optional, Sequence

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
WORD_SAMPLES = 32  # samples per 32-bit word of a bit plane


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
    """PackBits as LabChart writes it: header ``k``: 0..127 copy ``k+1`` literal
    bytes, 128..255 repeat the next byte ``257-k`` times.  Unlike Apple's
    TN1023, ``k = 128`` is *not* a no-op but a run of 129 (a signed count of
    -128 -> ``1 - n`` repeats); LabChart emits it, and treating it as a no-op
    loses 129 bytes and corrupts the upper bit planes of the block."""
    src = bytes(src)
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        k = src[i]
        i += 1
        if k < 128:
            out += src[i:i + k + 1]
            i += k + 1
        else:
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


def unpack_planes(buf: bytes, n_planes: int, n_samples: int, word_reversed: bool = False) -> np.ndarray:
    """Return an ``(n_planes, n_samples)`` uint8 array of bits.  Plane order is
    the storage order (the caller decides what plane 0 means).  With
    ``word_reversed`` sample ``32k + j`` is read from bit ``31 - j`` of the
    k-th little-endian 32-bit word (the float32 convention), otherwise from
    bit ``j`` (the int16 convention)."""
    pb = plane_bytes(n_samples)
    if len(buf) % n_planes == 0 and len(buf) // n_planes != pb:
        pb = len(buf) // n_planes  # trust the buffer if the padding rule ever differs
    need = n_planes * pb
    if len(buf) < need:
        buf = buf + bytes(need - len(buf))
    planes = np.frombuffer(buf, dtype=np.uint8, count=need).reshape(n_planes, pb)
    bits = np.unpackbits(planes, axis=1, bitorder="little")
    if word_reversed:
        n_words = bits.shape[1] // WORD_SAMPLES
        bits = bits[:, : n_words * WORD_SAMPLES].reshape(n_planes, n_words, WORD_SAMPLES)[:, :, ::-1].reshape(n_planes, -1)
    return bits[:, :n_samples]


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
    """Return the stored float32 differences of a 32-plane buffer (plane 0 = bit
    31; samples reversed within each 32-bit word).  ``d[0]`` is the first
    sample itself."""
    bits = unpack_planes(buf, FLOAT32_PLANES, n_samples, word_reversed=True).astype(np.uint32)[::-1]
    v = np.zeros(n_samples, dtype=np.uint32)
    for p in range(FLOAT32_PLANES):
        v |= bits[p] << np.uint32(p)
    return v.view(np.float32)


def decode_float32(deltas: np.ndarray, prev_last: float = 0.0) -> np.ndarray:
    """Turn stored float32 differences into sample values: a running sum in
    float32 arithmetic, as LabChart does it (the values are bit-exact to the
    stored block minima/maxima).  ``prev_last`` is accepted for backwards
    compatibility and ignored: a block starts with its first sample stored
    as-is."""
    return np.cumsum(deltas.astype(np.float32), dtype=np.float32)


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

    def __init__(self, subblocks: Sequence[SubBlock], blocks_per_record: Sequence[Sequence[int]],
                 channel_dtypes: Optional[Sequence[str]] = None):
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
        self._fix_unpacked_dtypes(channel_dtypes)
        self._check_sample_counts()

    def _fix_unpacked_dtypes(self, channel_dtypes: Optional[Sequence[str]]) -> None:
        """Unpacked sub-blocks carry no 32-bit flag; give them the data type of
        their channel (from the Map stream, else from the channel's packed
        sub-blocks) so that ``n_samples`` and ``is_float`` are right."""
        for ch in range(self.n_channels):
            if channel_dtypes is not None and ch < len(channel_dtypes):
                is_float = channel_dtypes[ch] == "float32"
            else:
                packed = [sb for r in range(len(self.blocks_per_record))
                          for sb in self.channel_subblocks(r, ch) if sb.is_packed]
                if not packed:
                    continue
                is_float = packed[0].is_float
            if not is_float:
                continue
            for r in range(len(self.blocks_per_record)):
                for b in range(len(self.blocks_per_record[r])):
                    i = self._record_base[r] + b * self.n_channels + ch
                    sb = self.subblocks[i]
                    if not sb.is_packed and not sb.is_float:
                        self.subblocks[i] = replace(sb, flags=sb.flags | FLAG_32BIT, n_samples=sb.body_size // 4)

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
        """``"float32"`` if any sub-block of the channel is 32-bit, else ``"int16"``
        (decided once per channel: the sub-blocks do not change after opening)."""
        memo = self.__dict__.setdefault("_dtype_memo", {})
        if channel not in memo:
            memo[channel] = "int16"
            for r in range(len(self.blocks_per_record)):
                if any(self.get(r, b, channel).is_float for b in range(len(self.blocks_per_record[r]))):
                    memo[channel] = "float32"
                    break
        return memo[channel]

    def samples_per_tick(self, record: int, channel: int) -> float:
        """Sampling rate of the channel relative to the record's tick rate."""
        blocks = self.blocks_per_record[record]
        if not blocks:
            return 1.0
        sb = self.get(record, 0, 0 if self.n_channels == 0 else channel)
        return sb.n_samples / blocks[0]

    def tick_to_sample(self, record: int, channel: int, tick: int) -> int:
        """Number of the channel's samples before ``tick`` (rounded; every channel of
        the files seen so far is sampled at the tick rate)."""
        return int(round(tick * self.samples_per_tick(record, channel)))

    def has_data(self, channel: int) -> bool:
        """Every channel of a ``.adicht`` file stores its samples."""
        return 0 <= channel < self.n_channels
