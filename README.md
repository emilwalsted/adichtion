# adichtion

Read ADInstruments LabChart `.adicht` files without LabChart – on macOS,
Windows and Linux, in pure Python (numpy is the only dependency).

The file format was reverse engineered from the files themselves; the byte
level description lives in [`docs/FORMAT.md`](docs/FORMAT.md). Raw (int16)
and calculated (float32) channels are decoded sample-exact (verified against
LabChart's own text and MATLAB exports); comments, record start times,
channel titles, input ranges, the two-point units conversion, the per-block
min/max index and the video-capture clips are read as well. The *definitions*
of calculated channels are not in these files; `adichtion infer` recovers them
from the data (Arithmetic, RMS, Integral, Spirometry Flow and Volume) and
`adichtion macsettings` reads them from a LabChart for Mac document or
settings file, see [`docs/CALCULATED_CHANNELS.md`](docs/CALCULATED_CHANNELS.md).

This repository is the foundation of the *adichtion* desktop app (a channel
viewer and exporter in the mould of [RespMech](https://github.com/emilwalsted/respmech));
the UI is not here yet.

## Install

```
pip install -e ".[dev]"
```

## Library

```python
from adichtion import AdichtFile

with AdichtFile("recording.adicht") as f:
    print(f.channel_titles)                 # ['Flow', 'Pmo', 'Volume']
    for rec in f.records:                   # LabChart "blocks"
        print(rec.start_local, rec.duration_s, [(c.tick, c.text) for c in rec.comments])
    rec = f.records[0]
    t = rec.time(0, 4000)                   # seconds from record start
    pmo = rec.read(1, 0, 4000)              # channel 2 in its units (cmH2O) when the file says so
    raw = rec.read_raw(1, 0, 4000)          # int16 ADC counts as stored
    rec.value_label(1)                      # 'cmH2O', 'V', 'converted' or 'counts'
    sel = rec.selection_between("start", "stop")            # tick range between two comments
    t0, t1, lo, hi = rec.block_envelope(0)  # min/max per 16000-tick block, straight from the file's index
    t_env, lo, hi = rec.overview(0, 2000)   # min/max envelope for drawing a whole record
    rec.video                               # VideoClip(file_name='Record1.wmv', offset_s=0.46, ...) or None
    rec.video.file_time(12.0)               # position in the video file of record time 12 s (camera delay included)
    rec.video_file                          # path in '<name> movies' next to the file, if it exists

from adichtion.infer import infer_record
for ch, hyps in infer_record(rec).items():  # what each float32 channel is, e.g.
    print(ch + 1, hyps[0])                  # "RMS of Ch1, window 200 samples (50 ms), centred: 100 before, 99 after  (exact; ...)"
```

Opening a file only parses metadata; samples are decoded per 16000-tick block
on demand (every block is self-contained) and cached, so a viewer can page
through a 300 MB file freely. Units are per record, as LabChart stores them.

## Command line

```
adichtion info FILE                       records, channels (units, LSB, conversion), videos
adichtion comments FILE [--record N]      comment list with times
adichtion videos FILE                     video clips of the records and their offsets
adichtion export FILE -o out.txt --record 2 --channels 1,2 --between "start" "stop"
adichtion export FILE -o out.csv --format csv --start 355.2 --stop 385.9 --decimal ,
adichtion check FILE                      decode every block and compare with the min/max LabChart stored
adichtion infer FILE --record 14          recover the definitions of the calculated channels from the data
adichtion macsettings MACFILE             channel settings of a LabChart 7 for Mac document or settings file
adichtion verify FILE export.txt --record 24 --channels 1,2,3 [--fit]   # compare with a LabChart text export
adichtion tree FILE                       container storages and streams
adichtion strings FILE                    tagged strings of the Map stream (debugging)
```

`export` writes LabChart-style text (with `Interval=`, `ChannelTitle=` …
header lines and `#* comment` markers) or CSV, both readable by RespMech.

## Tests

`pytest` runs unit tests and end-to-end tests on synthetic files written by
`adichtion.synth` (v26 and v29 flavours, and the legacy layout for the
fallback parser), including a document whose calculated channels are built
with the standard calculations and must be recovered by `adichtion.infer`.
Real recordings are private and never committed; point `ADICHTION_SAMPLES`
at a folder with them to run the integration tests
(`tests/test_integration_samples.py`: every file must parse by the grammar,
its block index must match the data, the files with LabChart exports next to
them must decode sample-exact, and every calculated channel must be explained
by a standard calculation of the other channels).

## Licence

GPL-3.0-or-later, like RespMech. ADInstruments, LabChart and PowerLab are
trademarks of ADInstruments; this project is not affiliated with them.
