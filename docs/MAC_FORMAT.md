# LabChart for Mac documents ("ADInst Data Res File")

Working notes on the native document format of LabChart 7 for Mac, kept up to
date while support for it is built. It complements `FORMAT.md` (the Windows
`.adicht` format) and `CALCULATED_CHANNELS.md` §1 (the container and the
settings properties, which were decoded first). Everything was derived from the
files; nothing comes from ADInstruments documentation.

Status words as in `FORMAT.md`: **verified** (checked against LabChart's own
output: here the WinChart `.adicht` export of the same recording),
**consistent** (checked against redundant information inside the document),
**inferred** (the reading that makes the bytes make sense).

## Evidence

- 14 documents and 1 settings file from one study (2016–17): created with
  LabChart 7.0 and last saved with 7.2 (`vers` chunks), PowerLab with an EMG
  amplifier, 9 raw inputs (5 × EMG at 4 kHz, 4 × pressure/flow at 100 Hz) and
  up to 15 calculated channels, 7–32 LabChart blocks each, 45–61 MB.
- 10 of the 14 documents have a **WinChart export** (`.adicht` v26, written by
  LabChart for Mac's "Read/Write WinChart Files" extension) of the same
  recording; they are matched by the exact tick count of every block. These
  exports are the reference below. A document can be newer than its export
  (the settings, comments and one channel were edited later in some of them).
- The files carry no extension. macOS identifies them by their type/creator
  metadata; adichtion identifies them by the magic at offset 0.

## 1. Container

`u8 20, "ADInst Data Res File"` (a Pascal string), 3 bytes, `uint32
toc_offset`, `uint32 toc_length` at offset 24; a table of contents at the end
with 14-byte entries `char[4] type, uint16 id, uint32 offset, uint32 length`.
Big-endian throughout (`CALCULATED_CHANNELS.md` §1). **Consistent.**

The table does **not** cover every byte: the sample data (before each `Map4`
chunk) and the comment lists (between `Cnt1` and `Alc2`) are reached through
file offsets stored inside chunks.

| chunk | content | status |
|---|---|---|
| `vers` 1, 2, 100 | 4 bytes: `07 00 80 00` = 7.0.0, `07 20 80 00` = 7.2.0 (id 1 the creating version, 2/100 the saving one) | inferred |
| `CntD` 1 | the movie files of the video capture, as `ExQN` entries with a Pascal file name (`LabChartMovieN.mov`) | inferred |
| `Cnt1` 1 | one 44-byte entry per LabChart block: `uint16 first, last` storage block (1-based), then 40 bytes, mostly zero (video linkage?) | consistent (first/last) |
| `Alc2` 1 | one 34-byte entry per storage block (§2) | verified |
| `mTim` 1 | one 12-byte entry per storage block: `uint64` microseconds (the classic Mac `Microseconds()` clock), `float32` unknown | consistent |
| `Map4` *n* | the block index of channel *n* (§3) | verified |
| `Gain` 1 | input ranges (§5) | verified |
| `UntA` 1 | units conversions (§5) | verified |
| `CmtM` 1 | not the comments (520 bytes; unknown) | — |
| `SetI` 1 | per input: coupling and "Raw Data from Input *k*" as text | inferred |
| `Mcro` 30001/30002 | the document's settings properties: titles, calculations (`CALCULATED_CHANNELS.md`) | verified |
| `STR ` 1100… | channel titles | verified |
| `Mcro` (small ids), `McrE`, `McrV`, `wndS`, `FinS`, `FInf` | user macros, window state, Finder info | not needed |

## 2. Storage blocks and LabChart blocks (`Alc2`)

Samples are stored in **storage blocks** of up to 16 000 ticks, as in the
Windows format; a LabChart block (a `Record` in the code) is a run of storage
blocks. `Alc2` has one 34-byte entry per storage block, in file order:

| offset | type | content | status |
|---|---|---|---|
| 0 | uint16 | ticks in this storage block (16 000 except the last of a LabChart block) | verified |
| 2 | uint16 | LabChart block number (1-based) | verified |
| 4 | uint16 | 14 in every file (all 4 kHz): the sample-rate code? | inferred |
| 6 | uint32 | first tick of the storage block within its LabChart block | verified |
| 10 | uint32 | start of the LabChart block: seconds since 1904-01-01, **local** wall-clock time | verified |
| 14 | float64 | fraction of a second of that start | verified |
| 22 | uint16 | 1 | — |
| 24 | 6 bytes | 0 | — |
| 30 | uint32 | file offset of the storage block's comment list, `0xFFFFFFFF` = none (§6) | verified |

- The tick counts per LabChart block equal the export's in all 10 pairs.
- The start times agree with the export within ±5 ms; the export rounds to
  10 ms, the document keeps the finer value. LabChart for Mac stores local time
  without a zone, and its export writes that local time as if it were UTC.
- **Tick rate.** No explicit rate was found. `mTim` steps by exactly
  `ticks × 250 µs` between consecutive storage blocks of a LabChart block, i.e.
  4 kHz, which the export confirms; across LabChart blocks `mTim` is the real
  clock and agrees with `Alc2` to a few ms. The rate is therefore taken from
  those steps (a LabChart block of a single storage block borrows it from
  another block). Whether field 4 encodes it needs a document with another rate.

## 3. Block index (`Map4`)

One `Map4` chunk per channel, **id = channel number** (1-based), with one
24-byte entry per storage block of the document (the chunk length is `24 ×`
the number of storage blocks):

| offset | type | content | status |
|---|---|---|---|
| 0 | uint32 | file offset of the sample block | verified |
| 4 | uint32 | length in bytes (0 = nothing stored) | verified |
| 8 | float32 | maximum, normalised (`counts / 32768`) | consistent |
| 12 | float32 | minimum, normalised | consistent |
| 16 | uint16 | range: 1-based index into the `Gain` entries | verified |
| 18 | uint16 | storage format: 19 = packed, 16 = raw float32 (§4) | consistent |
| 20 | uint16 | units: 1-based index into the `UntA` entries, 0 = no conversion | verified |
| 22 | uint16 | decimation: the channel stores every *d*-th tick (0 = every tick) | verified |

- Calculated channels have an index of zeros: **their samples are not stored**
  in a document. LabChart for Mac computed them when drawing; only their
  definitions are stored (`Mcro` properties, `CALCULATED_CHANNELS.md`).
- The data of a channel sits in one contiguous run directly before its `Map4`
  chunk, in storage-block order.
- `−2147483648.0` in both min and max means "no min/max stored" (seen on two
  slow channels).
- For full-rate channels the decoded min/max equal the index exactly in every
  block; for decimated channels the stored samples always lie inside the
  index's envelope but do not always reach it (the envelope appears to be
  computed before decimation).

## 4. Sample blocks

A channel's samples in a storage block number `ceil(ticks / d)`.

**Format 19, packed** (all but a handful of blocks): byte `0x01`, then the
PackBits variant of the Windows format (code 128 is a run of 129), expanding to
32 **bit planes** of `4 × ceil(n / 32)` bytes each:

- plane 0 holds bit 31 of each sample's symbol, plane 31 bit 0 (MSB first);
- sample *i* is bit `7 − (i mod 8)` of byte `i div 8` of a plane (big-endian
  bit order, no reversal within 32-bit words, unlike the Windows float32
  planes);
- the symbol is a sign-magnitude difference, `2·|Δ| + (Δ < 0)`, to the
  previous sample; the first sample is stored as its difference to 0;
- the running sum gives the ADC counts (a 16-bit PowerLab's counts; the 32
  planes leave room for more).

**Format 16, raw**: big-endian float32 samples, normalised (`counts / 32768`),
no marker byte; used for tiny blocks (5 samples seen).

**Verified** on all 10 pairs, 1 485 channel × LabChart-block combinations:

- the EMG channels (every tick) are identical to the export's int16 counts;
- the decimated channels are identical to every 40th sample of the export;
  the export's samples in between are LabChart's own up-sampling to the tick
  rate (close to a rounded linear interpolation: 85 % agree), not stored data;
- the one exception is a channel that the export holds as a processed float32
  channel (0.998 correlation with a 5-sample moving average of the document's
  raw samples): the document was changed back to the raw input later.

## 5. Ranges and units (`Gain`, `UntA`)

**`Gain`**: 72-byte entries, referenced per storage block by the `Map4` field:

| offset | type | content |
|---|---|---|
| 0 | Pascal string (34 bytes) | base units with a leading no-break space: `" V"` or `"mV"` |
| 34 | float64 | full scale in those units: range × 1.024 (2.048, 10.24, 102.4, …) |
| 42 | float64 | the same again |
| 50 | float64 | +0.99997 (= 32767/32768, normalised top) |
| 58 | float64 | −0.99997 |
| 66 | 3 × uint16 | 0, then two small numbers (unknown) |

A sample in volts is `counts × full_scale / 32768`, with `mV` scaled by 10⁻³.
**Verified**: equal to the export's LSB for all 1 485 combinations, including
two channels recorded on 10 mV and 100 mV ranges in some blocks.

**`UntA`**: 130-byte entries, referenced by the `Map4` field (0 = none):

| offset | type | content |
|---|---|---|
| 0 | uint16 | 0x0205 |
| 2 | uint16 | 0 |
| 4 | uint16 | 0x0100 = conversion switched on, 0 = off (kept but unused) |
| 6 | 80-bit extended | offset |
| 16 | 80-bit extended | slope |
| 26 | Pascal string (8 bytes) | units, with a leading no-break space (`" cmH2O"`) |
| 0x52 | 4 × Pascal string (12 bytes each) | the calibration dialog: voltage 1, value 1, voltage 2, value 2 |

The value is `slope × volts + offset` when switched on. The floats are the
classic Mac 80-bit *extended* format (sign/exponent `uint16`, 64-bit mantissa
with explicit integer bit). **Verified**: slope, offset, on/off and units equal
the export's units objects for every channel and LabChart block.

Because both references are per storage block, a range or calibration can in
principle change inside a LabChart block; none of the documents does that.

## 6. Comments

The comment list of a storage block is at the file offset in its `Alc2` entry:

```
uint16 count
count × header (20 bytes):
    uint32 length of the text part (0 = none)
    uint16 tick within the storage block
    uint32 0
    uint16 comment number
    int32  channel mask: −1 = all channels, else bit k = channel k+1
    uint32 kind: 1 = comment, 2 = mark without text
then, for each kind-1 header in order:
    uint32 length, uint16 0, uint8 n, n bytes of Mac Roman text
```

- **Verified** against the export in 8 of the 10 pairs (record, tick, channel
  and text identical, 13–47 comments per document); the other two differ by a
  comment moved and a comment added after the export.
- Kind 2 occurs at most once per document, without text and with number 0:
  most likely LabChart's single Marker (inferred).
- Only one list with two entries was seen (a comment next to a kind-2 mark);
  its text-part length field read `0x00070000` instead of 7, so the reader
  relies on the Pascal length byte, not on that field.

## 7. Video

`CntD` names the movie files (`LabChartMovie1.mov` …); a document can name
several and two documents can use the same names, so they must live in a
folder of their own next to the document. How a movie's time maps to the
LabChart blocks (probably the 40 unexplained bytes of the `Cnt1` entries) is
**open**: none of the movie files was available when this was written.

## 8. Open questions

- Where the tick rate is stored (field 4 of `Alc2` is 14 at 4 kHz).
- `mTim`'s `float32`, `CmtM`, the tail of the `Gain` entries.
- The movie files and their timing (§7).
- Documents with more than 16-bit counts, other rates, or ranges that change
  within a LabChart block.
