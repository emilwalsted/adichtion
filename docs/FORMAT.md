# The LabChart `.adicht` file format (reverse engineered)

This document describes the ADInstruments LabChart 8 data file format as far
as it has been reverse engineered for `adichtion`. Everything here was derived
from the files themselves (see *Evidence* at the end); nothing comes from
ADInstruments documentation or from disassembling their software.

Verified on:

| file | LabChart | format version | cluster | tick rate | channels |
|---|---|---|---|---|---|
| recordings from 2023 (PowerLab 16/35, spirometry + mouth pressure) | 8.1.16 | v29 | 4096 B | 1000 Hz | 3 × int16 |
| recordings from 2016 (diaphragm EMG, oesophageal/gastric pressure) | 8.1.x | v26 | 1024 B | 4000 Hz | 10 × int16 + 11 × float32 |

All integers are little-endian. "int32/int64" are signed unless noted.

## 1. Container: "ADInstruments Compound File"

A structured-storage container in the spirit of Microsoft OLE/CFBF, but ADI's
own: fixed-size clusters, a doubly linked cluster map and a tree of storages
holding named streams.

### 1.1 Header (512 bytes)

| offset | type | content |
|---|---|---|
| 0x000 | UTF-16LE | `"ADInstruments Compound File"`, zero padded to 0x80 |
| 0x080 | int32 | format version (26 for the 2016 files, 29 for 2023) |
| 0x084 | int32 | header size = 512 |
| 0x088 | int32 | allocation chunk in bytes = 131072 |
| 0x08C | int32 | 0x7FFFFFFF |
| 0x090 | 8 bytes | 0 |
| 0x098 | int32 | log2(cluster size): 10 → 1024 B, 12 → 4096 B |
| 0x09C | int64 | cluster capacity of the map (a multiple of the entries per map page) |
| 0x0A4 | int64 | −capacity |
| 0x0AC | int64 | first cluster of the cluster-map pages |
| 0x0B8 | entry | root directory entry (160 bytes, §1.4), name `ADIRootStorage` |

### 1.2 Cluster addressing

Cluster `c` starts at file offset `512 + (c − 2) × cluster_size`: the first
data cluster after the header is **cluster 2**; clusters 0 and 1 are virtual.
The physical file usually ends short of a whole 128 KiB allocation chunk.

### 1.3 Cluster map

Pages of `cluster_size / 8` entries, each entry `(int32 next, int32 prev)`,
`0` = none. Page `k` describes clusters `k × entries_per_page …`. The pages
are contiguous from the cluster named in header field 0x0AC to the end of the
file. Entry 0 is special (`next` = first free cluster, `prev` = INT32_MIN);
entries beyond the physical file are pre-initialised as a plain chain.

A stream is the chain `first → next → … → last`; all streams seen so far are
contiguous, so readers can slice directly.

### 1.4 Directory entries and tables

A directory table is `int32 count, int32 −count` followed by `count` entries
of 160 bytes:

| offset | type | content |
|---|---|---|
| 0x00 | UTF-16LE | name, 72 bytes, zero padded |
| 0x48 | int64 | first cluster |
| 0x50 | int64 | size in bytes |
| 0x58 | int64 | last cluster |
| 0x60 | 40 bytes | zero |
| 0x88 | int32 | flags (0) |
| 0x8C | int32 | type: 1 = storage, 2 = stream (0 seen for empty streams) |
| 0x90 | 16 bytes | zero |

The root entry's `first` is the cluster holding the root table (its `size` is
the table's byte size). A storage entry's `first` cluster holds its own table.
Tables have always fitted in one cluster.

Typical v29 tree:

```
Settings/                 (storage)
  ChannelSettings, MacroStorage, MenuSettings, DisplaySettings, SettingsRegistrar, ZOrderStream
  ChildDocuments/         DeviceManagerStg_0/{DeviceManagerStm_0, ChartTrigSettings},
                          PowerLabStg/{PLSettings, PLDescr}, ScopeView, Scripts, ADIStdPart0..2
Version                   ("LabChart 8.1.16")
ChildDocuments/           ScopeView, Scripts, ADIStdPart2..4, DataPadStorage/Spreadsheet*Stream (when a Data Pad exists)
Map                       the "Chart Data" document: records, blocks, comments, titles, units, XML setups (§3)
Blocks_0                  the sample data (§4)
```

The v26 files have only `blocks_0`, `IncMap` (empty) and `Map`: the settings
storage is absent, i.e. they hold data, titles, units and comments only (the
same content as ADI's "Simple Data File" `.adidat`).

## 2. Serialisation conventions inside streams

* A **prefixed string** is `int32 n, int32 −n` followed by `n` UTF-16LE code
  units, the last being the terminating NUL.
* A **tagged string** is the tag `0x80000001` (bytes `01 00 00 80`) followed by
  a prefixed string.
* Lists are `int32 n, int32 −n` followed by `n` items.

The redundant `−n` makes every length field self-checking, which is what makes
scanning a stream for structures feasible. Strings are not aligned.

## 3. The `Map` stream ("ADInstruments Chart Data")

Header: magic `aa aa 14 55` (v26) / `aa aa 15 55` (v29), `int32 2`, 8 zero
bytes, `uint16 25`, `"ADInstruments Chart Data\0"` (Latin-1), three int16
version numbers (3.5.21 / 3.7.3), padding.

### 3.1 Records

LabChart calls them *blocks*; the ADI SDK and this library call them
**records** (a continuous recording; a new one starts every time sampling is
restarted). Each record header:

| type | content |
|---|---|
| int64 | tick rate in Hz (the highest sampling rate of the record) |
| float64 | start time, seconds since 1970-01-01 UTC |
| float64 | UTC − local time in seconds (−3600 for CET) |
| 12 bytes | zero |
| int32 | counter (cumulative comment count seen so far, roughly) |
| int64 | ticks in the record |
| tagged string | empty |
| block list | §3.2 |

The record headers are found by scanning for this pattern; they are not at
fixed offsets.

### 3.2 Block list and comments

Records are stored in **blocks of 16000 ticks** (the last one shorter). The
block list is `int32 n, int32 −n`, then per block a 16-byte entry

`int32 3, int32 ticks, int32 flags (0x80000000 on the first block), int16 0, int16 n_comments`

followed by `n_comments` comment records:

`int32 2, int32 0, int32 comment id, int16 channel (−1 = all channels, else 0-based), int16 tick within the block, tagged text`

The absolute tick of a comment is the block's start tick plus the 16-bit tick.
Comment ids are global to the file and may have gaps (deleted comments).

### 3.3 Channel titles

`int32 −1, int32 N, int32 −N`, then `N` tagged strings: the titles of
channels 1…N, N being the number of channels the hardware offers (16 or 32);
unused channels carry the default `"Channel k"`.

### 3.4 Units conversion records

One per channel (identity records for unconverted channels), in channel
order, in v29 files:

| type | content |
|---|---|
| tagged string | base units, `"V"` |
| float64 | slope (units per volt) |
| float64 | offset (units) |
| int32 × 4 | 1, 1, 0x10000, 0 |
| tagged string | target units, e.g. `"cmH2O"` (may be empty even when converted) |
| tagged string × 4 | the two-point calibration as displayed: base value 1, base value 2, units value 1, units value 2 (locale decimals, e.g. `"4,5455 V"`) |

`value = slope × volts + offset`, and slope/offset are exactly the two-point
calibration LabChart shows. In v26 files there are fewer records than channels
and their association with channels is not yet known (see §6).

### 3.5 Channel setup XML (v29 only)

Per channel a tagged string holding an XML fragment
`<_ChannelSetup value-code="k"> … </_ChannelSetup>` with, among others,
`<PhysicalGainIndex …>10 V</PhysicalGainIndex>` (the input range),
`<SpeedIndex …>1000 /s</SpeedIndex>` (sampling rate) and
`<Device>PowerLab 16/35-NNNN</Device>`.

### 3.6 Other content

Also present but not needed by the reader: 44-byte per-block index records in
v26 (tag `0x80020003`, ticks, two float32 that look like min/max, a position),
per-channel `ChannelDataSource` objects, and in the settings streams the
display scale (`BackupScale top/bottom`), the hardware channel map
(`Input k` → title) and the Data Pad.

## 4. The `Blocks_0` stream: sample data

`55 55 03 aa` (magic), then a chain of **sub-blocks**, one per
`(record block, channel)`, block-major: block 0 → channel 1, 2, …; block 1 → …
The channel count of the file is therefore `n_subblocks / n_blocks`.

### 4.1 Sub-block header (16 bytes)

| type | content |
|---|---|
| int32 | `n`: the sub-block occupies `n + 4` bytes in total |
| int32 | 0 for packed sub-blocks; a non-zero value (checksum?) for unpacked ones |
| int32 | `−n` |
| int32 | flags: `0x100` \| `0x1` (32-bit float32 samples, else int16) \| `0x4` (packed) \| `0x80000000` (unpacked, stored raw) |

Then the body of `n − 12` bytes: for packed sub-blocks `int32 raw_size`
(samples × bytes per sample; 32000 for a full int16 block, 64000 for float32)
followed by the PackBits data; for unpacked sub-blocks the plain little-endian
sample array. LabChart stores a sub-block unpacked when packing would not have
made it smaller (seen only for float32 channels).

### 4.2 PackBits

Apple's PackBits (TN1023): header byte `k`: 0…127 → copy `k+1` literal bytes,
129…255 → repeat the next byte `257−k` times, 128 → no-op. The decompressed
buffer is a set of **bit planes**.

### 4.3 Bit planes

Each plane is `4 × ceil(samples / 32)` bytes (whole 32-bit words). Plane `p`
holds one bit of every sample's symbol: sample `i` at bit `i mod 8` of byte
`i div 8` (LSB first). Unused high planes are all zero and collapse under
PackBits to `81 00` pairs, which is where the compression comes from.

**int16 channels (17 planes, plane 0 = LSB):** the symbol is
`m = 2·|d| + (1 if d < 0 else 0)` with `d = x[i] − x[i−1]` and `x[−1] = 0`,
i.e. the first sample is stored as-is. Reconstruction is a cumulative sum in
int16 arithmetic (deltas wrap modulo 2¹⁶; a saturated input really does jump
from −173 to +32592 in one sample).

**float32 channels (32 planes, plane 0 = bit 31):** the symbol is the
IEEE-754 bit pattern of a float32 **difference** `d[i] = x[i] − x[i−1]`. The
difference chain is re-based at sample 31: `d[0] = x[0] − prev` where `prev` is
the last value of the previous block of the same record (0 for the first
block), and `d[31] = x[31] − x[30] + prev`. Hence

```
x = cumsum(d)          # exact from sample 31 on, and x[-1] = sum(d)
x[:31] += prev          # only the first 31 samples need the previous block
```

so any block can be decoded on its own except for its first 31 samples, and
`prev` of the next block is simply `sum(d)` of this one (or the last raw
sample of an unpacked block).

### 4.4 Sample rates

All channels seen so far are sampled at the tick rate (16000 samples per
16000-tick block). The ADI SDK allows per-channel rates; the reader derives a
channel's rate from `samples / ticks` of its sub-blocks.

### 4.5 Calculated channels

The 2016 files store their *Channel Calculation* outputs (RMS, BTPS, Pdi,
spirometry flow/volume) as float32 channels. ADI's SDK documentation says
calculated data is not stored, and in the 2023 files the *calculated*
`Volume` channel indeed exists only as its raw input; so whether calculated
data is present depends on the LabChart version/settings that wrote the file.
A calculated channel's *definition* is not stored in the v26 files at all
(they have no settings storage).

## 5. Physical units

Raw ADC counts are int16. With the nominal input range `R` volts (from
§3.5, e.g. 10 V or 200 mV) one LSB is `R × 1.024 / 32768` V: the "10 V" range
has a 312.5 µV LSB, i.e. the converter spans ±10.24 V (verified against the
export's quantisation step and the two-point calibration values). Units then
follow §3.4.

## 6. Open questions

* v26 files: the mapping of units-conversion records to channels (18–20
  records for 21 channels); input ranges are not stored in the Map stream
  (no XML), so counts cannot be converted to volts without the user telling
  the range.
* The int32 in the sub-block header of unpacked sub-blocks (checksum?).
* Float32 reconstruction has been verified by internal consistency only
  (continuity across ~1250 packed/unpacked block transitions and the re-base
  invariant `sum(d) = x[-1]`), not against a LabChart export.
* Whether the 16-bit tick field of comments limits blocks to < 32768 ticks (all
  files seen use 16000-tick blocks).
* The v26 44-byte block index records and the `IncMap` stream.

## 7. Evidence

* int16 decoding: sample-exact against five LabChart text exports (81,250 +
  50,000 samples, two files, several records) for the mouth-pressure channel
  in units, and up to one scale factor for the flow channel (the export was
  in L/s while the file has no units record for it).
* Comments: the comment times coincide with the export window boundaries
  (e.g. a comment at 1884.000 s vs. an export ending at 1884.019 s).
* Container: parsed and walked on files of both format versions; every
  stream chain is contiguous and `size` matches the content.
* Float32: see §4.3; the re-base rule was found by observing `d[31] ≈ prev`
  in 438 of 444 packed blocks of one channel (the exceptions being blocks where
  a real jump was larger) and confirmed on unpacked→packed→unpacked chains.
