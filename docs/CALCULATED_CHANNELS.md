# Calculated channels: what the files hold and how the definitions come back

LabChart's *Channel Calculations* (Arithmetic, RMS, Integral, Spirometry
Flow/Volume, ...) produce float32 channels. An `.adicht` written by LabChart 7
for Mac (the "Read/Write WinChart Files" extension, format v26) stores only
the **results** of those calculations, with a title and a units object each;
the **definitions** (source channel, function, parameters) are not in the file
(`FORMAT.md` §3 accounts for the whole Map stream). This document describes
where the definitions can be found instead, and how far they have been
recovered for the files at hand.

Two routes are implemented:

1. `adichtion.macchart` reads the channel settings of the LabChart for Mac
   **documents and settings files** ("ADInst Data Res File", §1), where the
   definitions live as properties.
2. `adichtion.infer` recovers definitions **numerically** from the `.adicht`
   itself by testing the standard calculations against the data (§2); the
   inputs are usually the other channels of the same record, so most
   definitions can be confirmed sample-exactly.

Both are exposed on the command line (`adichtion macsettings FILE`,
`adichtion infer FILE --record N`). The sample-level behaviour of each
calculation, as established on the data, is in §3.

Verification status follows the convention of `FORMAT.md`: **verified** means
reproduced sample-exact (or within the count resolution of the raw inputs) on
real files, **inferred** means read from the settings files without a
numerical check.

## 1. LabChart 7 for Mac files ("ADInst Data Res File")

The classic Mac LabChart wrote documents (data plus settings) and settings
files in one container, big-endian throughout:

| offset | content |
|---|---|
| 0 | `u8 20` + `"ADInst Data Res File"` (a Pascal string) |
| 21 | 3 bytes |
| 24 | `uint32 toc_offset`, `uint32 toc_length` |
| ... | chunk bodies |
| `toc_offset` | table of contents: 14-byte entries `char[4] type, uint16 id, uint32 offset, uint32 length` |

Chunk types seen: `CntD`, `Cnt1`, `Alc2`, `CmtM`, `UntA`, `Gain`, `SetI`,
`mTim`, `vers`, `Map4` (the sample data, documents only), `FInf`, `McrV`,
`McrE`, `Mcro`, `STR `, `FinS`, `wndS`. Only the `Mcro` chunks with ids
**30001/30002** (the document's own settings; user macros have small ids) and
the `STR ` resources from id 1100 (channel titles) are read.

The settings are a stream of **property records**:

```
u8 0, u8 type, uint16 key, uint32 channel (1-based; 0 = document), payload
```

with the payload given by the type: `0x01` int16, `0x0A` uint16, `0x04`
int32, `0x0C` 4 bytes, `0x0E` 6 bytes, `0x10` 8 bytes, `0x18` float64,
`0x08` Pascal string (Mac Roman). Anything else is skipped byte by byte until
the next record. Keys with an established meaning:

| key | scope | meaning |
|---|---|---|
| `0x290B` | channel | title |
| `0xE878` | channel | the calculation, stored as its menu label (`Arithmetic…`, `RMS…`, `Integral…`, `Spiro. Flow…`, `Spiro. Volume…`); absent for raw inputs |
| `0x2BB8` | channel | Arithmetic expression (`Ch9-Ch8`, `(Ch11+Abs(Ch11))/2`), stored with a trailing `0x01` |
| `0x2BBB` | channel | Arithmetic output units |
| `0x2BB9`, `0x2BBA` | channel | Arithmetic display range top/bottom |
| `0x2DC4` | channel | RMS source channel (1-based) |
| `0x2E4B`, `0x2E4E` | channel | Spirometry Flow: raw flow channel; calibration in L/s per V |
| `0x2E55`, `0x2E58` | channel | Spirometry Volume: flow channel; volume ratio *k* (1.09 in every file seen) |
| `0x2849` | document | number of channels |

Every extension's dialog leaves its own keys on a channel whether or not that
channel uses the extension (a Spirometry Flow channel carries an RMS source
key, a raw channel an old Arithmetic expression), so a key means something
only together with `0xE878`. Not decoded: the Integral extension's source and
reset mode, the RMS window (`0x2DC5..0x2DC8` are `(340, 2, 1.0, 84)` on every
channel that ever opened the RMS dialog and are not the window), and the
spirometry dead-space volume (0.4 L, the default, in every file seen; it may
only be written when changed). A file's *current* settings can postdate the
`.adicht` export made from it (one document had two more channels and a
changed expression), so the numerical route is the check.

## 2. Numerical inference (`adichtion.infer`)

`infer_channel(record, channel)` reads the first 200 000 ticks of the record
(configurable), and tests for the channel:

* a **linear function of one other channel** (`a*x+b`), and the same with the
  zero drift correction of Spirometry Flow (§3.4);
* a **linear combination of the positive and negative parts** of one channel
  (LabChart's `(x±Abs(x))/2` idiom), also of the channel shifted by a
  constant first (`(x+c±Abs(x+c))/2`, the constant read off the samples where
  the result is not zero);
* a **linear combination of two other channels**;
* the **integral** of another channel from the record start (§3.3), and the
  Spirometry Volume with volume correction (§3.5);
* a **moving RMS** of a raw channel, searching the window length (2 to 4000
  samples, geometric grid then every length near the best) and the alignment
  (§3.2).

Every hypothesis carries its maximum absolute error over the analysed samples.
*Exact* means within 8 float32 ulp of the stored values (16 ulp plus that for
integrals, which accumulate); *within the input resolution* means the error
is at most one count of the raw inputs, the signature of a calculation that
ran on a finer version of the input than the whole counts the file stores
(the raw flow channel of the 2016 files behaves so: every derived value is
off by up to half a count). Coefficients are rounded to the fewest decimals
that keep the error inside the same bound, so `0.999996*Ch9-0.999968*Ch8`
becomes `Ch9-Ch8`. Hypotheses are ranked exact first, then within
resolution; those explained only by channels *after* the target (the
inverse relation, e.g. `Spiro Flow = Ch18+Ch19` when Ch18 and Ch19 were
made from it) are pushed down as probably circular.

`infer_record(record)` runs this for every float32 channel. Times: about one
second per channel, dominated by the RMS search.

## 3. The calculations at sample level

Established on the 2016 EMGdi files (11 recordings, 4 kHz) by comparing
against the stored channels; the Mac settings and templates of the same study
give the intended definitions, which agree.

### 3.1 Arithmetic (verified)

Plain arithmetic on the converted values of the inputs, sample by sample.
Results are exact to float32 when the inputs are float32 channels (BTPS
splits, `1.095*Ch18+Ch19`) and within half a count of the raw inputs when
those are int16 channels (`Pdi = Ch9-Ch8` from two pressures), see §2.

### 3.2 RMS (verified)

A centred moving root-mean-square of the source **counts**: a window of *w*
samples covering `i-before .. i+after` with `before = w/2`, `after = w/2-1`
(for *w* = 200 at 4 kHz: 50 ms, 100 before and 99 after), exact to float32
away from the record edges. The window length is not among the decoded
settings keys; the inference finds it. The stored values are in the source's
count scale (same LSB, conversion off), so the units name the RMS channel
inherits can be stale.

### 3.3 Integral (verified)

A rectangular sum: `y[i] = Σ_{j≤i} x[j]·dt`, reset to 0 at the start of every
record, in float32 (the stored value drifts from a float64 sum by a few ulp
over 100 s). `Vol BTPS = ∫Flow BTPS dt` in the study files.

### 3.4 Spirometry Flow (verified within the input resolution)

`Spiro Flow = cal × (x − z(i))` with *cal* the calibration of the settings
(L/s per V of the raw channel) and **z a zero level interpolated linearly from
the raw value at the record's first sample to the raw value at its last
sample**. The correction is exact to half a count on every record of three
files (44 records, up to 1.3 counts over the two records of more than a
million samples, presumably float32 precision of the interpolation on
LabChart's side). Over long records the zero moves by hundreds of counts, so a
plain `a*x+b` fits only the shortest ones. The rule presupposes that the
subject is off the mouthpiece (zero flow) at both ends of every block; how
LabChart 7 exposed the option is not known, and the two zero levels are not
stored as settings.

### 3.5 Spirometry Volume (verified)

ADInstruments documents the Spirometry Volume calculation with *volume
correction* as `V = ∫F dt` while inspiring and `V = ∫F/k dt` for "expiring
air in excess of dead-space", with a default dead-space (DAS) volume of
400 mL for humans and *k* the volume ratio (the BTPS factor 1.09 by default,
or a value fitted from the data). At sample level:

* a reservoir of `dead_space` litres is full at the record start;
* samples of the *other* phase (positive flow in the files seen) are
  integrated as they are and refill the reservoir, capped at `dead_space`;
* samples of the *corrected* phase (negative flow) drain the reservoir and are
  integrated as they are while the reservoir lasts; the first sample whose
  volume exceeds what is left, and every following sample of that phase, is
  divided by *k* and the reservoir is empty;
* the integral is rectangular and resets at the record start.

`adichtion.infer.spirometry_volume` implements this; it reproduces the stored
channel to float32 precision on every record of three files (maximum error
8 × 10⁻⁷ L over 100 s of breathing), including records with forced manoeuvres
where a plain integral is off by litres. The dead space and *k* are found from
the data by the inference (`0.4 L`, `1.09` throughout); the phase LabChart
corrected is the **negative** flow in these files. Whether that sign is
expiration depends on the flow head's orientation: in the study files the
negative phase is the one the Arithmetic channels label "In" (inspiration),
and a 5 L negative excursion right after a 0.1 L positive one can only be an
inspiration, so the correction was applied to inspiration there. That is a
property of the recordings, not of the reader; a viewer should display the
recovered definition and leave the interpretation to the user.

The `Integral` extension (§3.3) has none of this: `Vol BTPS` is the plain
integral of `Flow BTPS`.

## 4. Where this leaves the reader

* A v26 file with calculated channels is read as stored: the values are
  exact, the definitions are absent. `adichtion infer` recovers them when the
  inputs are in the file; `adichtion macsettings` reads them from the Mac
  document or a settings template when one is available.
* Newer files (v29) seen so far carry no calculated channels. If one turns
  up, its `Settings/ChannelSettings` streams are the place to look for the
  definitions (`FORMAT.md` §7).
* Recomputing a calculated channel from its inputs is possible for every kind
  above, but the reader does not do it silently: what the file stores is what
  LabChart computed, and that stays the reference.
