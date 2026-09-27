# adichtion

Read ADInstruments LabChart `.adicht` files without LabChart – on macOS,
Windows and Linux, in pure Python (numpy is the only dependency).

The file format was reverse engineered from the files themselves; the byte
level description lives in [`docs/FORMAT.md`](docs/FORMAT.md). Raw (int16)
channels are decoded sample-exact (verified against LabChart's own text
exports); comments, record start times, channel titles, input ranges and the
two-point units conversion are read as well. Calculated (float32) channels are
decoded too, verified by internal consistency only so far.

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
    pmo = rec.read(1, 0, 4000)              # channel 2 in its units (cmH2O) when the file says how
    raw = rec.read_raw(1, 0, 4000)          # int16 ADC counts as stored
    sel = rec.selection_between("start", "stop")            # tick range between two comments
    t_env, lo, hi = rec.overview(0, 2000)   # min/max envelope for drawing a whole record
```

Opening a file only parses metadata; samples are decoded per 16000-tick block
on demand and cached, so a viewer can page through a 200 MB file freely.

## Command line

```
adichtion info FILE                       records, channels, comment counts
adichtion comments FILE [--record N]      comment list with times
adichtion export FILE -o out.txt --record 2 --channels 1,2 --between "start" "stop"
adichtion export FILE -o out.csv --format csv --start 355.2 --stop 385.9 --decimal ,
adichtion verify FILE export.txt --record 24 --channels 1,2,3 [--fit]   # compare with a LabChart text export
adichtion tree FILE                       container storages and streams
adichtion strings FILE                    tagged strings of the Map stream (debugging)
```

`export` writes LabChart-style text (with `Interval=`, `ChannelTitle=` …
header lines and `#* comment` markers) or CSV, both readable by RespMech.

## Tests

`pytest` runs unit tests and an end-to-end test on a synthetic file written by
`adichtion.synth`. Real recordings are private and never committed; point
`ADICHTION_SAMPLES` at a folder with them to run the integration tests
(`tests/test_integration_samples.py`).

## Licence

GPL-3.0-or-later, like RespMech. ADInstruments, LabChart and PowerLab are
trademarks of ADInstruments; this project is not affiliated with them.
