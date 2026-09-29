# LabChart for Mac documents ("ADInst Data Res File")

Working notes on the native document format of LabChart 7 for Mac, kept up to
date while support for it is built. It complements `FORMAT.md` (the Windows
`.adicht` format) and `CALCULATED_CHANNELS.md` §1 (the container and the
settings properties, which were decoded first). Everything was derived from the
files; nothing comes from ADInstruments documentation. How adichtion maps a
document onto its API is in §9.

Status words as in `FORMAT.md`: **verified** (checked against LabChart's own
output: here the WinChart `.adicht` export of the same recording),
**consistent** (checked against redundant information inside the document),
**inferred** (the reading that makes the bytes make sense).

## Evidence

- 15 documents and 9 settings files (one next to the documents, eight
  processing templates) from one study (2016–17). The documents were created with
  LabChart 7.0 and last saved with 7.2 (`vers` chunks), PowerLab with an EMG
  amplifier, 9 raw inputs (5 × EMG at 4 kHz, 4 × pressure/flow at 100 Hz) and
  3–15 calculated channels (12, 23 or 24 channels in all), 7–32 LabChart
  blocks each, 45–61 MB.
- 10 of the documents have a **WinChart export** (`.adicht` v26, written by
  LabChart for Mac's "Read/Write WinChart Files" extension) of the same
  recording; they are matched by the exact tick count of every block. These
  exports are the reference below. A document can be newer than its export
  (the settings, comments, titles and one channel were edited later in some of
  them).
- The files carry no extension. macOS identifies them by their classic Finder
  type and creator codes: `CRec`/`MM16` for the documents, `CReS`/`MM16` for
  the templates (the settings file next to the documents is `CRec` as well);
  they keep a small resource fork. adichtion identifies them by the magic at
  offset 0 of the data fork. A settings file has the same magic and no `Map4`
  chunks.
- The documents name 41 movie files of LabChart's video capture (QuickTime,
  0.1–13 GB each). They are in a second copy of the study folder; four of them
  were read (their `moov` atoms and synchronisation samples) to establish the
  timing (§7.1).

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
| `CntD` 1 | the movie table: tagged items for each LabChart block with a movie (§7) | consistent |
| `Cnt1` 1 | one 44-byte entry per LabChart block: first and last storage block, the block's movie entry (§7) | consistent |
| `Alc2` 1 | one 34-byte entry per storage block (§2) | verified |
| `mTim` 1 | one 12-byte entry per storage block: `uint64` microseconds (the classic Mac `Microseconds()` clock), `float32` unknown | consistent |
| `Map4` *n* | the block index of channel *n* (§3) | verified |
| `Gain` 1 | input ranges (§5) | verified |
| `UntA` 1 | units conversions (§5) | verified |
| `CmtM` 1 | not the comments (520 bytes; unknown) | — |
| `SetI` 1 | per input: coupling and "Raw Data from Input *k*" as text | inferred |
| `Mcro` 30001/30002 | the document's settings properties: channel count, titles, calculations (§1.1, `CALCULATED_CHANNELS.md`) | verified |
| `McrE` 1 | the extension registry: the key range LabChart gave each extension; the keys of the calculations' settings follow it (`CALCULATED_CHANNELS.md` §1) | consistent |
| `STR ` 1100… | channel titles (§1.1) | verified |
| `Mcro` (small ids), `McrV`, `wndS`, `FinS`, `FInf` | user macros, window state, Finder info | not needed |

### 1.1 Channels and titles

- The number of channels is the number of `Map4` chunks (ids 1…*n*, §3). The
  document-level settings property `0x287F` (int16) holds the same number in
  all 15 documents; the settings files have it too. `0x2849`, first read as
  the channel count, is 21 in all 24 files whatever the count. **Consistent.**
- Titles are the `STR ` resources from id 1100, one per channel; every file
  has 32 of them (the unused ones keep old titles). The settings' title
  property (`0x290B`) holds the same text. They equal the export's titles for
  199 of 213 channels; the other 14 were renamed or added after the export.
  **Verified** with that allowance.
- A file can carry settings for channels beyond its count, calculated ones
  included (left over from an earlier setup): `adichtion macsettings` hides
  them unless given `--verbose`.
- The settings keys of the calculations (Arithmetic expression, RMS and
  spirometry sources, …) are not fixed: they follow the extension key ranges
  in `McrE`, which differ in three of the documents (and two settings files).
  With the keys resolved through the registry, every calculated channel of
  every file has a complete definition (`CALCULATED_CHANNELS.md` §1).

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
- `−2147483648.0` in both min and max means "no min/max stored". It is
  common: every block of the five EMG channels in 4 documents, and 15 % of the
  blocks of the decimated channels (5 446 of 37 064, scattered, in every
  document). Why is unknown; the reader computes those from the samples when
  it needs them (§9).
- For full-rate channels the decoded min/max equal the index exactly in every
  block that has one; for decimated channels the stored samples always lie
  inside the index's envelope but do not always reach it (the envelope appears
  to be computed before decimation).

## 4. Sample blocks

A channel's samples in a storage block number `ceil(ticks / d)`; sample *k*
belongs to tick `k·d` of the block.

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

**LabChart's up-sampling** (what its WinChart export writes between the
stored samples of a slow channel): between samples *x_k* at tick *k·d* and
*x_k+1*, the value at tick *k·d + j* is the linear interpolation
`x_k + (x_k+1 − x_k) · j / d`, rounded to whole counts half away from zero;
after the last stored sample of a block its value is held. **Verified** on the
10 pairs: 628 channel × block combinations, 377 968 664 values, all but
7 574 identical (99.998 %); those differ by one count, at exact half-count
ties that LabChart rounded toward zero instead (in 0.04 % of the 20 million
ties, mostly in intervals where the signal changes sign; no floating-point
model tried reproduces them). adichtion's export uses this rule when channels
of different rates share a table (§9).

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
    uint32 n + 3: the length of the text part after its own length field (0 = no text)
    uint16 tick within the storage block
    uint32 0
    uint16 comment number
    int32  channel mask: −1 = all channels, else bit k = channel k+1
    uint32 kind: 1 = comment, 2 = mark without text
then, for each kind-1 header in order, the text part:
    uint32 n + 3, uint16 0, uint8 n, n bytes of Mac Roman text
```

- **Verified** against the export in 8 of the 10 pairs (record, tick, channel
  and text identical, 13–47 comments per document); the other two differ by a
  comment moved and a comment added after the export.
- Lists seen: 357 with one comment, 7 with one kind-2 entry, 1 with a comment
  and a kind-2 entry; no storage block holds two comments in these files.
- Kind 2 occurs at most once per document, without text and with number 0:
  most likely LabChart's single Marker (inferred). A list holding only a
  kind-2 entry is followed by 4 zero bytes (26 bytes in all); the reader does
  not need them.
- In the one list with two entries, the text part's own length field reads
  `0x00070000` instead of 7 (the headers are right, and the kind-2 header
  carries the comment's 7 instead of 0), so the reader relies on the Pascal
  length byte, not on the length fields.

## 7. Video

Two chunks describe LabChart for Mac's video capture.

**`CntD`** is a run of items `char[4] tag, uint16 1, uint32 length, uint32 0`,
each followed by `length` bytes. `ExQN` holds a movie file name as a Pascal
string (`LabChartMovieN.mov`); `ExQI` holds a `uint32` (0 wherever seen). The
items tile the chunk exactly in all 15 documents. **Consistent.**

**`Cnt1`** has one 44-byte entry per LabChart block:

| offset | type | content | status |
|---|---|---|---|
| 0 | uint16 | first storage block (1-based) | consistent |
| 2 | uint16 | last storage block | consistent |
| 4 | 8 bytes | 0 | — |
| 12 | uint32 | 1 on exactly one LabChart block per document, else 0 | unknown |
| 16 | uint32 | size of the block's group of `CntD` items (33) | consistent |
| 20 | uint32 | offset of that group in `CntD` | consistent |
| 24 | uint16 | 1 = the block has a movie entry | consistent |
| 26 | uint16 | 1 (2 or 3 in some blocks of one document) | unknown |
| 28 | 16 bytes | 0 | — |

- In 14 documents every movie entry names a file (41 in all, 1–6 per
  document); the number in a name is LabChart's own counter, not the block
  order (one document has movies 2, 3, 4, 1 on its last four blocks, another
  only movie 3). One document, a copy made for analysis, has an empty name
  followed by an `ExQI` item in every entry: the copy kept no movies. The
  groups sit in `CntD` in block order, and the blocks with a movie are always
  the last ones of a document.
- **Where the files live**: in a folder `<document name> movies` next to the
  document, as in the Windows format (`<name> movies/Record<N>.wmv`). A
  second copy of the study (a backup folder) has such a folder for every
  document, with all 41 named movies; the first copy had only two, empty.
  Other video files in the study folders (two MPEG-2 recordings from another
  device, screen recordings made for figures) are named by no document.

### 7.1 The movies and their timing

Each `LabChartMovieN.mov` is a QuickTime movie (0.1 to 13 GB): a `wide` atom,
the `mdat` with the frames, a `free` atom holding an older `mvhd`, a second
`wide`, a small `mdat` and the `moov` at the end. Its tracks:

| track | content | status |
|---|---|---|
| video | DV, PAL 720 × 576, interlaced (`dvsd` or `dv5p` in the sample description, 288 000 bytes a frame); frame durations vary (2–200 ms, 35 ms typical), timescale 600 | verified (decoded) |
| sound | 44.1 kHz 16-bit PCM | — |
| "Synchronisation track" | generic media (`gnrc`), one 8-byte sample per video frame with the same durations, all in the small `mdat` | consistent |

**The sync samples are the capture time of every frame**: a big-endian
`uint64` in microseconds on the classic Mac `Microseconds()` clock, the clock
of the document's `mTim` chunk (§1). With `clock0` the `mTim` of the block's
first storage block, frame *i* was captured at block time `(stamp_i −
clock0) / 10⁶`. Checked on four movies of three documents (a 2016 and a 2017
installation; 1 391 to 25 840 frames):

- the first frame is captured 20–45 ms after the block starts; the movie
  runs about 0.12 s past the block's end (its duration is the block's + 0.12 s);
- for every frame after the first, `movie time − block time` is one constant
  per movie, 3.1–5.4 ms, to within 1 µs (the rounding of the stamps): the
  movie's timeline is the block's, shifted by that offset. The first frame is
  shown from movie time 0, before it was captured, so it is left out.

**Consistent** (two clocks agree to 1 µs over every frame); the clock of the
stamps being `mTim`'s is **inferred** from that agreement and from the first
frame falling just after the block start in every movie. What the stamps
cannot tell is how long a frame took from the camera to the Mac (the Windows
format stores a camera delay LabChart measured, 0.17 s in its files); no such
delay is recorded here, so none is applied.

adichtion reads the `moov` and the stamps (`adichtion.qtmovie`, a few hundred
kB even for a 13 GB movie) and plays the frame at `offset + t` for block time
*t*, as for `.adicht` clips (§9). The DV frames are interlaced: OpenCV 4.13
(FFmpeg 8) returns them black, so the viewer needs OpenCV below 4.13, where
they decode (checked with 4.10–4.12 against FFmpeg itself). Files that exist
but hold nothing (cloud placeholders that are not downloaded yet) are reported
as unreadable.

## 8. Open questions

- Where the tick rate is stored (field 4 of `Alc2` is 14 at 4 kHz).
- `mTim`'s `float32`, `CmtM`, the tail of the `Gain` entries, the `Cnt1`
  fields at 12 and 26, the `ExQI` item.
- The camera's own delay before a frame reaches the Mac (§7.1).
- Why so many blocks carry no min/max (§3).
- Documents with more than 16-bit counts, other rates, ranges or units that
  change within a LabChart block, or a channel recorded in only part of one
  (the reader refuses the last two with a message).

## 9. How adichtion reads a document

`AdichtFile(path)` recognises a document by its first bytes
(`macdoc.is_mac_document`, no extension needed) and builds the same data model
as for an `.adicht` file (`macdoc.read_mac_document` gives a `ChartData` and a
block index with the interface `AdichtFile` uses), so the API, export, command
line and viewer work unchanged. A settings file is refused with a message
saying so (`adichtion macsettings` reads it). Specifics:

- `AdichtFile.kind` is `"mac"`, `format_label` `"LabChart for Mac document
  (7.2)"`, `format_version` 0. LabChart blocks are `Record`s, storage blocks
  are blocks, as for `.adicht` files.
- **Channels without samples** (the calculated ones): `has_data(c)` is False,
  their dtype is `"none"`, `read_raw` returns nothing and `n_samples` is 0;
  `channel_definition(c)` gives the stored calculation in LabChart's terms
  (`"Arithmetic: Ch9-Ch8 [cmH2O]"`). They are not recomputed
  (`beslutninger.md`, 2026-09-28 (6)). The viewer lists them with their
  definition but draws, exports and tabulates only stored channels.
- **Decimated channels**: `samples_per_tick` is `1/d`, sample *k* is at tick
  `k·d`, `Record.tick_to_sample` is `ceil(tick / d)`, and
  `Record.sample_at(channel, tick)` gives the latest stored sample at or
  before a tick (the viewer's value under the cursor).
- **Min/max**: from `Map4` where stored; `AdichtFile.block_minmax(record,
  channel, fill=True)` decodes the blocks without one and remembers the
  result, `fill=False` returns NaN for them without decoding (what the GUI
  thread uses; the viewer's worker fills them, and a channel without any
  stored min/max takes its y-range from its first decoded curve).
- **Times**: `start_utc` is the local wall-clock time with a zero UTC offset,
  as in the WinChart export.
- **Export**: a channel without data is refused by name. Channels of one
  rate keep it; channels of different rates share a table with a row per
  tick, the slower ones up-sampled as LabChart's export does
  (`Record.read_ticks`, §4), and the command line and the export dialog say
  which channels were up-sampled from which rate
  (`export.upsampling_notice`).
- **Movies**: `AdichtFile.videos` has a clip per named movie
  (`mac.record_movies`); where `<name> movies/<file>` can be read, the clip is
  timed from its frame stamps (`VideoClip.stamped`, `offset_s` as in §7.1,
  `delay_s` 0) and `Record.video_file` gives the path; otherwise the clip is
  untimed and the viewer says the file is missing or unreadable.
- **Command line**: `info` (format, channels not stored, movies found),
  `check` (skips channels without data, counts blocks without a stored
  min/max, checks decimated channels against the envelope), `videos` (the
  movie of each record and its offset), `tree` (the chunks), `export` (with
  the up-sampling note), `macsettings`.
- `synth.write_mac_document` writes synthetic documents with all of the above,
  `synth.write_labchart_movie` and `synth.add_labchart_sync_track` movies with
  LabChart's synchronisation track (`tests/test_macdoc.py`,
  `tests/test_qtmovie.py`, `tests/ui/test_macdoc_ui.py`, where the viewer shows
  the frame at `offset + t`); `tests/test_integration_macdocs.py` checks real
  documents against their own index, their exports (samples, up-sampling) and
  their movies when `ADICHTION_SAMPLES` holds them.
