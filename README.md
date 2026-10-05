<img src="design/logo/renders/adichtion-256.png" width="128" height="128" alt="" align="right">

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

**LabChart for Mac documents** (LabChart 7 for Mac's own format, usually saved
without an extension) open through the same API, command line and app: the
raw channels decode sample-exact against LabChart's WinChart exports of the
same recordings, with their ranges, units conversions, comments and block
times; slow channels keep their own sampling rate. Calculated channels are
not stored in these documents (LabChart computed them when drawing), so they
are listed with the definition the document holds and are not drawn or
exported. The movies LabChart for Mac recorded with the blocks
(`<name> movies/LabChartMovieN.mov`) play in step with the data, timed by the
capture stamp LabChart wrote for every frame. See
[`docs/MAC_FORMAT.md`](docs/MAC_FORMAT.md).

The *adichtion* desktop app (a channel viewer and exporter in the mould of
[RespMech](https://github.com/emilwalsted/respmech)) is built on it, see below.

## Install

**The desktop app** comes as a native installer for each platform, built by
the *Build installers* workflow and attached to every
[GitHub release](https://github.com/emilwalsted/adichtion/releases):

| Platform | File | Notes |
|---|---|---|
| Windows | `adichtion-*.msi` | unsigned: if SmartScreen warns, *More info ▸ Run anyway* |
| macOS (Apple Silicon) | `adichtion-*.dmg` | drag to Applications; an ad-hoc-signed build needs right-click ▸ *Open* the first time |
| Ubuntu 24.04 / Debian-based | `adichtion_*_amd64.deb` | `sudo apt install ./adichtion_*_amd64.deb` |

Each bundles its own Python and libraries; `.adicht` files open in the app by
double-click, and the installed binary is also the command line
(`adichtion info FILE`). LabChart for Mac documents have no extension, so
they are not registered for double-click: open them with *File > Open* (the
file dialog's second filter shows all files) or name them on the command line.
See [`docs/RELEASING.md`](docs/RELEASING.md) for how the installers are built.

**From PyPI** (the library and the command line; the `ui` extra adds the app):

```
pip install adichtion            # the library and the command line (numpy only)
pip install "adichtion[ui]"      # plus the viewer: then `adichtion` alone starts the app
```

**From source** (the library, the command line and, with `ui`, the app):

```
pip install -e ".[dev]"        # the library and the command line
pip install -e ".[ui,dev]"     # plus the viewer (PySide6, pyqtgraph, openpyxl, OpenCV)
```

## The viewer

```
adichtion [recording.adicht]
```

One command does both: `adichtion` alone (or with a file) starts the app,
`adichtion <command> …` runs the command line described below.

Opens maximised with the start dialog (the recent files, or the file dialog while
there are none), or the file given, and shows every channel
of the file on one time axis, the blocks laid end to end as LabChart shows
them, with the block labels and comment flags in a strip above the channels.

* Click a channel to place the cursor on all of them; each channel's overlay
  shows its value there, the *Data at cursor* panel lists them all, and the
  *Video* panel shows the frame the block's video clip has at that moment
  (camera delay compensated). Drag over a channel, or click and then Shift+click,
  to select an interval; the data panel then gives min, max and mean per channel.
  Right click a channel to export the interval (or, with none selected, the whole
  recording).
* Exporting an interval of a recording that has video can also write the matching video clip, in the file's original
  format (copied) or as H.264 in an MP4 file, named like the data file and in the same folder (the export dialog's
  *Video* box; `adichtion.videoclip` in the API). It needs an ffmpeg program: the `imageio-ffmpeg` package brings one.
* The video panel plays the clip from the cursor (Space, or the ▶ button): the
  cursor moves with the video and every overlay and panel follows, the view
  pages along, and playback pauses at the end of the block; with an interval
  selected, Space plays from its start to its end. ⏮/⏭ jump to the
  start and end of the block's video; ◀◀/▶▶ halve or double the speed
  (0.1× to 8×).
* Scroll to zoom the time axis (at most out to the whole file) and Alt+drag, a
  sideways swipe or the middle button to pan it (all channels follow, none can leave
  the file); *View > Drag selects an interval* lets a plain drag pan instead. The
  toolbar (icons; hover for the name and shortcut) has zoom in/out/reset, *Zoom to
  fit* (fits every channel's y-axis to the visible data once) and the *Auto zoom*
  toggle (does so after every move); the wheel over a y-axis, or the small `+ − A`
  buttons in a channel's corner, zoom one channel's y-axis alone. Drag the handles between
  channels to give one more height; *Show channels…* picks the channels shown
  and makes the heights equal again.
* Calculated channels are tinted and carry their definition in the overlay,
  recovered from the data in the background (or read from a LabChart for Mac
  document or settings file by choosing the *LabChart for Mac settings files* type in *File > Open…*).
  In a LabChart for Mac document they are not stored: the file panel lists
  them with the document's definition, and the channel and export dialogs
  show them greyed out.
* *Export…* writes the whole file, the block under the cursor or the selected
  interval as LabChart-style text, CSV or an Excel workbook, for the channels
  shown, all channels or a choice. Channels of different sampling rates share
  one table at the tick rate, the slower ones up-sampled as LabChart's own
  export does; the dialog says which.

The traces are drawn from the file's own per-block min/max index first and
refined from the decoded samples in a worker thread, so a 300 MB file pans
and zooms without waiting; nothing is recomputed: what is shown is what
LabChart stored.

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
adichtion videos FILE                     video clips of the records and their offsets (Mac documents: the movie names)
adichtion export FILE -o out.txt --record 2 --channels 1,2 --between "start" "stop"
adichtion export FILE -o out.csv --format csv --start 355.2 --stop 385.9 --decimal ,   # ';'-separated, decimal comma
adichtion export FILE -o out.mat --record 2 --channels 1,2,3        # MATLAB, channels as rows of data_block1
adichtion export FILE -o all.xlsx --all-records      # every block; format from the extension
adichtion export FILE -o part.csv --start 10 --stop 20 --video h264   # also part.mp4, the video of the interval (original: no re-encoding)
adichtion check FILE                      decode every block and compare with the min/max LabChart stored
adichtion infer FILE --record 14          recover the definitions of the calculated channels from the data
adichtion macsettings MACFILE             channel settings of a LabChart 7 for Mac document or settings file
adichtion bench FILE [--export [SECONDS]] [--pan [STEPS]] time open, curve passes, decoding, cursor look-ups, an export and a pan/zoom scenario in the real window (numbers only; --json)
adichtion verify FILE export.txt --record 24 --channels 1,2,3 [--fit]   # compare with a LabChart text export
adichtion tree FILE                       container storages and streams
adichtion strings FILE                    tagged strings of the Map stream (debugging)
```

`export` writes LabChart-style text (with `Interval=`, `ChannelTitle=` …
header lines and `#* comment` markers), CSV (with a decimal comma the fields are
separated by `;`, as European Excel and RespMech expect), a tab-separated table
(`--format tsv`, one header line), an Excel workbook (one sheet per block, a plain
table with the block, start and interval to the right of it) or a MATLAB level-5
file (one `data_block<N>` matrix per block, the channels as rows).
**RespMech reads the CSV, tab-separated, Excel and MATLAB exports as they are**
(for MATLAB choose the `windows` variant; time is column 1 of a table, and channel
1 is column 1 of a MATLAB file). For RespMech export one block at a time: with several
blocks a CSV or tab table gets a leading `block` column that shifts the column numbers,
and RespMech reads only the first Excel sheet and `data_block1`. Keep the header on
(`--no-header` makes RespMech take the first row of data as column titles). The LabChart-style text is not a table, and
RespMech does not read it: use `tsv` for a `.txt` file RespMech can open.

## Tests

`pytest` runs unit tests and end-to-end tests on synthetic files written by
`adichtion.synth` (v26 and v29 flavours, and the legacy layout for the
fallback parser), including a document whose calculated channels are built
with the standard calculations and must be recovered by `adichtion.infer`,
and the viewer's tests (`tests/ui/`, headless with `QT_QPA_PLATFORM=offscreen`,
skipped when PySide6 is not installed).
Real recordings are private and never committed; point `ADICHTION_SAMPLES`
at a folder with them to run the integration tests
(`tests/test_integration_samples.py`: every file must parse by the grammar,
its block index must match the data, the files with LabChart exports next to
them must decode sample-exact, and every calculated channel must be explained
by a standard calculation of the other channels;
`tests/test_integration_macdocs.py`: every LabChart for Mac document found by
its first bytes must agree with its own block index, and those with a WinChart
export next to them must match it sample for sample).

## Licence

GPL-3.0-or-later, like RespMech. The video clip export uses an ffmpeg program (FFmpeg, GPL build with libx264,
bundled by the `imageio-ffmpeg` package; source: https://ffmpeg.org/). ADInstruments, LabChart and PowerLab are
trademarks of ADInstruments; this project is not affiliated with them.
