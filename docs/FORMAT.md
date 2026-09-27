# The LabChart `.adicht` file format (reverse engineered)

This document describes the ADInstruments LabChart data file format as far as
it has been reverse engineered for `adichtion`. Everything here was derived
from the files themselves (see *Evidence* at the end); nothing comes from
ADInstruments documentation or from disassembling their software.

Verified on:

| files | LabChart | format version | cluster | tick rate | channels |
|---|---|---|---|---|---|
| 2 recordings from 2022–23 (PowerLab 16/35, spirometry + mouth pressure, video) | 8.1.16 | v29 | 4096 B | 1000 Hz | 3 × int16 |
| 11 recordings from 2016–17 (diaphragm EMG, oesophageal/gastric pressure, spirometry) | 8.1.x | v26 | 1024 B | 4000 Hz | 9–11 × int16 + 9–14 × float32 |

All integers are little-endian. "int32/int64" are signed unless noted.
Statements are marked **verified** (checked against LabChart's own output),
**consistent** (checked against redundant information inside the files) or
**inferred** (the reading that makes the bytes make sense).

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
ChildDocuments/           ScopeView, Scripts, ADIStdPart2..4 (ADIStdPart4 = the video module, §6),
                          DataPadStorage/Spreadsheet*Stream (when a Data Pad exists)
Map                       the "Chart Data" document: records, blocks, comments, channels, units (§3)
Blocks_0                  the sample data (§4)
```

The v26 files have only `blocks_0`, `IncMap` (empty) and `Map`: the settings
storage is absent. They hold data, titles, units conversions, block indexes
and comments, but no channel-setup XML, no device settings and no video
stream.

## 2. Serialisation conventions inside streams

The streams are serialized object graphs in the style of MFC's `CArchive`:

* A **prefixed string** is `int32 n, int32 −n` followed by `n` UTF-16LE code
  units, the last being the terminating NUL.
* A **tagged string** is the tag `0x80000001` (bytes `01 00 00 80`) followed by
  a prefixed string.
* **Lists** are `int32 n, int32 −n` followed by `n` items.
* An **object slot** is a uint32: `0x80000000 | id` introduces a *new object*
  with the running id `id` (ids are assigned in order of appearance, shared by
  all object kinds of the stream); a plain `id` is a *reference* to an object
  serialized earlier; `0` is an empty slot. Records after the first reference
  the units objects and channel setups of the first record instead of
  repeating them (**consistent**: every reference seen resolves to an object
  of the same channel).

The redundant `−n` makes every length field self-checking, which is what made
the format discoverable. Nothing is aligned.

## 3. The `Map` stream ("ADInstruments Chart Data")

The stream follows a fixed grammar (**consistent**: 13 files of both versions
parse to the last byte, and every offset in it points at a sub-block header):

```
header
record  { int32 next_record_number, int32 0, record }*
int32 -1, titles
trailer
```

### 3.1 Header

| type | content |
|---|---|
| 4 bytes | magic `aa aa 14 55` (v26) / `aa aa 15 55` (v29) |
| int32 | 2 |
| 8 bytes | zero |
| uint16 | 25 |
| 25 bytes | `"ADInstruments Chart Data\0"` (Latin-1) |
| int16 × 3 | 3, 5 (v26) / 7 (v29), **number of recorded channels** |
| 9 bytes | zero |

### 3.2 Records

LabChart calls them *blocks*; the ADI SDK and this library call them
**records** (a continuous recording; a new one starts every time sampling is
restarted).

| type | content |
|---|---|
| int32 | record structure version: 2 (v26 files), 3 (v29 files) |
| int32, int32 | 1, 0 |
| int64 | tick rate in Hz (the highest sampling rate of the record) |
| float64 | start time, seconds since 1970-01-01 UTC |
| float64 | UTC − local time in seconds (−3600 for CET; 0 in the v26 files) |
| 12 bytes | zero |
| int32 | number of 16000-tick blocks in all previous records |
| int64 | ticks in the record |
| tagged string | record name (always empty so far) |
| object slot | v26: a small object `{int32 1, tagged string ""}` in the first record (it takes object id 1), a reference to it afterwards; v29: 0 |
| uint32 | 0 (v26) / 0x30000000 (v29) |
| block list | §3.3 |
| list of channel entries | `int32 n, int32 −n` (n = channels in the header), then §3.4 |

Records are separated by `int32 k, int32 0` where `k` is the 1-based number
of the record that follows; after the last record comes the `int32 −1` of the
titles table.

### 3.3 Block list and comments

Records are stored in **blocks of 16000 ticks** (the last one shorter; a
trailing block of 0 ticks has been seen once, it has no sample data). The
block list is `int32 n, int32 −n`, then per block a 16-byte entry

`int32 3, int32 ticks, uint32 flags, int16 0, int16 n_comments`

with `flags` 0x80000000 on the first block of the first record, 0xC0000000 on
the first block of later records and 0 otherwise, followed by `n_comments`
comment records:

`int32 2, int32 0, int32 comment id, int16 channel (−1 = all channels, else 0-based), int16 tick within the block, tagged text`

The absolute tick of a comment is the block's start tick plus the 16-bit tick.
Comment ids are global to the file and may have gaps (deleted comments).

### 3.4 Channel entries

One per recorded channel, in channel order, in every record:

| type | content |
|---|---|
| uint8 | 1 |
| uint32 | 0x801B0000 (entry tag) |
| int32 | entry version: 2 (v26), 3 (v29) |
| int32 | channel index (0-based) |
| uint32 | 0x80000000 (v26) / 1 (v29) |
| uint32 | data type tag: 0x80020001 = int16 samples, 0x80020003 = float32 samples |
| int32 × 3 | 1, 0, 1 |
| 12 bytes | zero |
| int32 × 2 | 1, 0 |
| int64 | tick rate (Hz) |
| object slot | **units object** (§3.5) or a reference to one |
| int32 | 1 |
| object slot | v29 only: **`ChannelDataSource`** object or reference: `int32 2, tagged "ChannelDataSource", tagged XML` (§3.6) |
| int64 | ticks in the record |
| int32 | record index |
| int32 | 16000 (block length in ticks) |
| int32 | 16000 (samples per full block for this channel) |
| block index | `int32 n, int32 −n` (n = blocks with data) then per block the 36-byte entry below |

Block index entry:

| type | content |
|---|---|
| uint32 | data type tag as above |
| int64 | 0 |
| int32 | ticks in the block |
| int32 | 0 |
| float32 | minimum of the stored samples (counts or float32 values) |
| float32 | maximum |
| int64 | **byte offset of the sub-block header in `Blocks_0`** |

The index makes every block addressable without walking the `Blocks_0` chain
and gives a free min/max envelope per 4 s (at 4 kHz) of every channel. The
minima/maxima are exact for int16 channels and within a few float32 ulp for
calculated channels (**verified**, all 141,056 sub-blocks of 13 files).

### 3.5 Units object

| type | content |
|---|---|
| uint32 | 0x80000000 \| id |
| int32, int32 | 5, 2 |
| float64 | **LSB**: value of one stored unit (§5) |
| 8 bytes | zero |
| uint8 | 0 (ADC channels) / 0x6B (calculated channels) |
| int16 × 4 | display related (e.g. −2, 70, −2, 1478), not interpreted |
| 9 bytes | `00 00 00 01 00 00 00 00 00` (v26) / `00 00 00 07|01 00 00 00 01 00` (v29), not interpreted |
| tagged string | input units: `"V"` for ADC channels, `""` for calculated channels |
| float64 | slope (units per input unit) |
| float64 | offset (units) |
| int32 | conversion type as stored: 1 = two-point calibration, 4 = scale/offset (`0.0 → a, 1.0 → b`), 3 (integrated volume), 2 = none, 0 = stale (calibration strings left over, conversion off) |
| int32 | 1 or 8 (decimals?) |
| int32 | flags: **bit 16 (0x10000) = conversion enabled** |
| int32 | 0 or 0x40000000 |
| tagged string | units name, e.g. `" cmH2O"` (a leading space in v26; may be empty even when the conversion is on) |
| tagged string × 4 | the two calibration points as displayed: input 1, input 2, output 1, output 2 (locale decimals, e.g. `"4,5455 V"`, `"60"`) |
| uint8 | 0 |

`value = slope × (LSB × stored) + offset` when the flag is set, else
`LSB × stored` (**verified** for both cases and both data types, §7). Slope and
offset are exactly the two-point calibration LabChart shows. Calibration
strings can be present with the conversion switched off: LabChart keeps the
last settings of an input even after the channel is reused for something
else (five EMG channels of the 2016 files carry pressure calibrations).

### 3.6 Channel setup XML (v29 only)

The `ChannelDataSource` object's XML, `<_ChannelSetup value-code="k"> …`,
holds among others `<PhysicalGainIndex …>10 V</PhysicalGainIndex>` (the input
range), `<SpeedIndex …>1000 /s</SpeedIndex>` (sampling rate) and
`<Device>PowerLab 16/35-NNNN</Device>`. The range agrees with the LSB of the
units object (`range × 1.024 / 32768`, **consistent** in all v29 channels).
Later records reference the first record's object unless a setting changed,
in which case a new object is written.

### 3.7 Channel titles and trailer

`int32 −1, int32 N, int32 −N`, then `N` tagged strings: the titles of
channels 1…N, N being the number of channels the hardware offers (16 or 32);
unused channels carry the default `"Channel k"` (one file has the title
`"Flow (r"` where another has `"Flow (raw)"`: titles are stored as LabChart
had them, cut or not).

Trailer: v26 — 16 zero bytes; v29 — `int64` (≈ total raw sample bytes),
`int64` (size of `Blocks_0`), tagged empty string.

## 4. The `Blocks_0` stream: sample data

`55 55 03 aa` (magic), then a chain of **sub-blocks**, one per
`(record block, channel)`, block-major: block 0 → channel 1, 2, …; block 1 → …
The §3.4 block index points at every sub-block; walking the chain gives the
same positions (**consistent**).

### 4.1 Sub-block header (16 bytes)

| type | content |
|---|---|
| int32 | `n`: the sub-block occupies `n + 4` bytes in total |
| int32 | 0 for packed sub-blocks; a non-zero value (checksum?) for unpacked ones |
| int32 | `−n` |
| uint32 | flags: `0x100` \| `0x1` (float32 samples) \| `0x4` (packed) \| `0x80000000` (unpacked, stored raw) |

Then the body of `n − 12` bytes: for packed sub-blocks `int32 raw_size`
(samples × bytes per sample; 32000 for a full int16 block, 64000 for float32)
followed by the PackBits data; for unpacked sub-blocks the plain little-endian
sample array. LabChart stores a sub-block unpacked when packing would not have
made it smaller (seen only for float32 channels, 1.8 % of sub-blocks).
**Unpacked sub-blocks do not carry the `0x1` flag**: the data type of an
unpacked block is the channel's (from §3.4 or from the channel's packed
sub-blocks), and its sample count is `body / 4` for float32 channels.

### 4.2 PackBits

PackBits with header byte `k`: 0…127 → copy `k+1` literal bytes, 128…255 →
repeat the next byte `257−k` times. Unlike Apple's TN1023, **`k = 128` is a
run of 129**, not a no-op (LabChart emits it; treating it as a no-op drops
129 bytes and corrupts the upper bit planes of that block — 10 of 4,000
int16 blocks in one file). The decompressed buffer is a set of **bit
planes**.

### 4.3 Bit planes

Each plane is `4 × ceil(samples / 32)` bytes (whole 32-bit words). Plane `p`
holds one bit of every sample's symbol. Unused high planes are all zero and
collapse under PackBits to `81 00` pairs, which is where the compression comes
from.

**int16 channels (17 planes, plane 0 = LSB, LSB-first bit order):** sample `i`
is at bit `i mod 8` of byte `i div 8` of the plane. The symbol is
`m = 2·|d| + (1 if d < 0 else 0)` with `d = x[i] − x[i−1]` and `x[−1] = 0`,
i.e. the first sample is stored as-is. Reconstruction is a cumulative sum in
int16 arithmetic (deltas wrap modulo 2¹⁶; a saturated input really does jump
from −173 to +32592 in one sample). **Verified** sample-exact.

**float32 channels (32 planes, plane 0 = bit 31, word-reversed sample
order):** sample `32k + j` is at bit `31 − j` of the k-th little-endian 32-bit
word of the plane, i.e. the 32 samples of every word are stored in reverse
order. The symbol is the IEEE-754 bit pattern of the float32 **difference**
`d[i] = x[i] − x[i−1]` with `d[0] = x[0]` (first sample as-is). LabChart
accumulates in float32; `numpy.cumsum(d, dtype=float32)` reproduces its
values bit for bit. Every block is self-contained. **Verified** sample-exact
(§7); an earlier reading of this library ("re-base at sample 31") was an
artefact of the wrong sample order.

### 4.4 Sample rates

All channels seen so far are sampled at the tick rate (16000 samples per
16000-tick block, and the channel entry says so). The ADI SDK allows
per-channel rates; the reader derives a channel's rate from
`samples / ticks` of its sub-blocks.

### 4.5 Calculated channels

The 2016 files store their *Channel Calculation* outputs (RMS, BTPS, Pdi,
spirometry flow/volume) as float32 channels with a units object each; the
2023 files store no calculated channels (their *Volume* channel exists only
as its raw input). Whether calculated data is present therefore depends on
the LabChart version/settings that wrote the file. A calculated channel's
*definition* (source channel, function, parameters) is **not stored** in the
v26 files: the Map stream is fully accounted for (§3) and there is no other
stream. The units name a calculated channel inherits from its source can be
stale (an RMS channel of an EMG input labelled `" cmH2O"`, with the
conversion off). The definitions can be read from the LabChart for Mac
document or settings file the `.adicht` was exported from, or recovered from
the data; `CALCULATED_CHANNELS.md` covers both and gives the sample-level
behaviour of Arithmetic, RMS, Integral, Spirometry Flow and Spirometry Volume
as established on the files.

## 5. Physical units

Every channel's units object carries the **LSB**, the value of one stored
unit:

* ADC channels: `LSB = range × 1.024 / 32768` V, e.g. 312.5 µV for the "10 V"
  range, 62.5 µV for 2 V, 6.25 µV for 200 mV: the converter spans ±1.024 ×
  range over 2¹⁶ counts (**verified** against exports and the v29 XML range);
* calculated channels store their values in "counts" of a virtual range:
  `2⁻¹⁵` (= 1 V range / 32768 without the 1.024 factor) for channels derived
  from converted channels (Pdi, BTPS, spirometry), the source channel's LSB
  for channels derived from raw inputs (RMS of an EMG channel: 6.25 × 10⁻⁵).

Then `value = slope × (LSB × stored) + offset` when the conversion is enabled,
else `LSB × stored`. **Verified** on the MATLAB export of a 2016 recording:
raw EMG (counts × LSB), Pdi (float32, conversion on: `1.024 × 2⁻¹⁵ ×
stored`) and three RMS channels (float32, conversion off) are sample-exact;
the pressure channels (int16, two-point calibration) match to the
interpolation the export applied to them.

## 6. Video (LabChart video capture)

LabChart writes one video file per record into the folder
`<document name> movies` next to the `.adicht`, named `Record<N>.wmv` with
`N` the 1-based record number (`RecordN` = LabChart block N), and keeps a
stream in `ChildDocuments/ADIStdPart<k>` that starts with the GUID
`a46425de-6022-4944-b4c6-b13bc0eaa648`:

| type | content |
|---|---|
| 16 bytes | GUID above |
| 4 bytes | `aa aa 15 55` |
| int32 | 3 (version) |
| int32 | number of records |
| per record | `float64 start, stop, video_start, offset`, tagged file name |
| int32 | 3 |
| tagged string | empty |

`start`/`stop` are the video-capture clock (seconds) when the record started
and stopped (`stop − start` exceeds the record by ~0.1 s), `video_start` the
clock at the first frame of the file, and `offset` is the position in the
video file that corresponds to the record's first sample, **including the
camera delay**: `offset = start − video_start + delay`, where `delay` is the
video latency the user measures in LabChart's video setup (a visual feedback
procedure) before recording. The delay is set per recording, so it is constant
within a file and differs between files (0.17 s in both files seen). To show
the frame belonging to record time `t`, seek to `offset + t`; `VideoClip.delay_s`
exposes the measured delay for information and for a manual correction in a
viewer. (Semantics **confirmed** by the author of the recordings; the constant
is consistent in all 27 entries.) Records without video have four −1.0 values
and an empty name; an unfinished capture leaves a `VidC<n>.tmp` name.

## 7. Open questions

* The int32 in the sub-block header of unpacked sub-blocks (checksum?).
* The meaning of the small fields not interpreted in §3.4–3.5 (display range
  int16s, the 9-byte groups, `0x30000000`) and of the record-level object of
  the v26 files.
* Units conversion types 3 (integrated volume) and the spirometry/BTPS
  channels have not been checked against an independent export (the MATLAB
  export available was made from a re-analysed copy whose spirometry
  differs); the formula is the same as for the verified type 4 channel. Their
  values are, however, reproduced from their input channels to float32
  precision (`CALCULATED_CHANNELS.md` §3), which checks the scaling
  indirectly.
* Whether the 16-bit tick field of comments limits blocks to < 32768 ticks (all
  files seen use 16000-tick blocks).
* Channels sampled below the tick rate have not been seen.
* The `IncMap` stream (empty in all files) and the settings streams of v29
  files (display scales, hardware channel map, Data Pad) are not read. A v29
  file with calculated channels has not been seen; when one turns up,
  `Settings/ChannelSettings` is the place to look for their definitions.
* Of the LabChart for Mac settings (`CALCULATED_CHANNELS.md` §1): the RMS
  window length, the Integral extension's source and reset mode, the
  spirometry dead-space volume and the option behind the Spirometry Flow
  zero correction are not decoded; the data gives them instead.

## 8. Evidence

* **Map grammar and block index**: all 13 files parse sequentially to the last
  byte; every block-index offset equals the position found by walking the
  `Blocks_0` chain (141,056 sub-blocks); decoded min/max of every block equals
  the stored value (int16 exactly, float32 within 1e-6 relative).
* **int16 decoding and units**: sample-exact against five LabChart text exports
  (81,250 + 50,000 samples, two v29 files, several records) for the
  mouth-pressure channel in converted units, up to one scale factor for the
  flow channel (exported in L/s), and against the MATLAB export below for
  five EMG channels (counts).
* **float32 decoding and units**: sample-exact against two LabChart MATLAB
  exports (2 × 125,400 samples) of a 2016 recording for the arithmetic
  channel Pdi (conversion on) and three RMS channels (conversion off); the
  export window was located by matching the raw EMG counts.
* **PackBits 128**: the 10 int16 blocks decoded 129 bytes short of the 34,000
  expected are exactly those containing a `80` header; treating it as a
  129-run gives 34,000 bytes and the stored min/max.
* **Comments**: comment times coincide with the export window boundaries
  (e.g. a comment at 1884.000 s vs. an export ending at 1884.019 s).
* **Container**: parsed and walked on files of both format versions; every
  stream chain is contiguous and `size` matches the content.
* **Video**: 27 entries in two files; the file names exist in the `movies`
  folders (except the `.tmp` one), `stop − start` tracks the record
  durations, `offset − (start − video_start)` is constant (0.17 s) and is the
  camera delay measured before the recording, per the author of the files.
* **Calculated channels**: on the longest record of each of the eleven 2016
  files, every float32 channel is reproduced from the other channels by a
  standard calculation, exactly or within half a count of the raw inputs
  (`tests/test_integration_samples.py`); the Spirometry Volume rule was
  checked on every record of three files (44 records) and the Spirometry
  Flow zero correction likewise. The definitions read from the Mac settings
  and templates of the study (`adichtion macsettings`) agree with the
  numerical result wherever both exist.
