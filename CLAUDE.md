# CLAUDE.md — adichtion

Project memory for **adichtion**: a reader (and, later, a desktop viewer/exporter
app) for ADInstruments LabChart `.adicht` files, built on a reverse-engineered
format description. Public repo `emilwalsted/adichtion`, GPL-3.0-or-later.
Author: Emil Ingerslev Walsted. The app is modelled on RespMech
(`emilwalsted/respmech`): same layering, packaging and release discipline.

## Sources of truth

- `docs/FORMAT.md` — the format as far as it is known, with the evidence and
  the open questions. Every decoder change must keep it accurate.
- `docs/CALCULATED_CHANNELS.md` — how LabChart's calculations behave at sample
  level, the Mac settings file format and how definitions are recovered.
- `docs/MAC_FORMAT.md` — LabChart for Mac documents ("ADInst Data Res File"):
  the format, its verification against WinChart exports, and how the reader
  maps a document onto the API (§9). Keep it current with every finding.
- `docs/beslutninger.md` — dated decisions and "settled, don't reopen" points.
- `src/adichtion/synth.py` — the executable form of the spec: it writes
  synthetic files that the reader must round-trip exactly (`tests/`).
- `README.md` — user-facing overview.

## Layers (keep them clean)

1. `container.py` — the compound-file container: header, cluster map,
   storages, streams. Pure `struct` + `mmap`, no numpy.
2. `serialization.py`, `chartdata.py`, `model.py` — the `Map` stream
   (records, blocks, comments, channel entries with units objects, block
   index and channel-setup XML, titles) into plain dataclasses that carry no
   file handles or arrays; sequential grammar first, scanning as a fallback.
3. `blocks.py` — sub-block index and the PackBits/bit-plane decoders
   (int16 and float32). Stateless numpy.
   `macdoc.py` is layers 1–3 for LabChart for Mac documents: their chunks into
   the same `ChartData`, a `MacBlockIndex` with the interface `AdichtFile` uses,
   and the Mac block decoder (`struct` + numpy, no file handles).
4. `file.py` (`AdichtFile`, `Record`, `Selection`) + `decimate.py` + `video.py` +
   `qtmovie.py` — the API the UI will use: lazy per-block decoding with an LRU
   cache, unit conversion per record, selections by time or comments, min/max
   envelopes for plotting (from the file's block index when possible),
   LabChart's up-sampling, `AdichtFile.block_table` (one flat table of the block index per channel, for the
   viewer's vectorised curves), video clips (and the timing of LabChart for Mac
   movies).
5. `export.py`, `cli.py` — text/CSV export and the command line. `videoclip.py` cuts the video of an exported
   interval into a clip with an ffmpeg program (stream copy or H.264; docs/beslutninger.md 2026-10-05 (4)); it is Qt-free
   and `export_dialog`/`main_window` call it.
6. `infer.py` (numerical recovery of calculated-channel definitions, on top of
   layer 4) and `macchart.py` (LabChart 7 for Mac documents/settings files,
   standalone). Both are analysis helpers the UI may call; neither changes
   what `AdichtFile` returns.

   `perf.py` (the `ADICHTION_PERF` timeline: numbers-only events, aggregates, Qt-free) and
   `bench.py` (`adichtion bench`) measure what the layers above cost; they change nothing
   that `AdichtFile` returns (`AdichtFile.cache_stats()` and `clear_cache()` are the only
   additions to it). `bench.py` reuses the Qt-free `ui/lod.py` and `ui/timeline.py` so it
   times exactly what the plots run; `ui/__init__.py` imports no Qt, so that stays Qt-free.

7. `ui/` — the PySide6/pyqtgraph viewer (`adichtion` without a subcommand). It talks to layers
   4–6 only; never put Qt imports below it. `src/adichtion/ui/CLAUDE.md` has
   its structure and the Qt rules that were learnt the hard way.

## Non-negotiable rules

- **Correctness is verified, not assumed.** Raw int16 decoding is
  sample-exact against LabChart text exports; keep it that way. Any change to
  a decoder needs (a) the synthetic round-trip tests green and (b) the gated
  integration tests green on the real samples (`ADICHTION_SAMPLES`). A
  numerical change in what the API returns is a deliberate decision recorded
  in `docs/beslutninger.md`, never a side effect.
- **No patient data in the repo.** Recordings, exports and anything derived
  from them stay out of git (see `.gitignore`); tests use synthetic files.
  Do not paste real comment texts, dates or subject identifiers into code,
  tests, docs or commit messages.
- **Distinguish verified from inferred** in docs and docstrings: int16 and
  float32 decoding and the units scaling are verified sample-exact against
  LabChart text/MATLAB exports; the Map grammar and block index are
  consistent across all sample files; the rest is inferred. Update
  `docs/FORMAT.md` §7/§8 when the status changes.
- **Ask before any upload to a live site or a release tag.** A `v*` tag runs
  `.github/workflows/release.yml` and publishes a GitHub release with the
  installers; a manual run of that workflow only produces artifacts.
- **Commits** are authored and committed as Emil Walsted <emilwalsted@gmail.com>,
  without tool-added trailers (`.claude/settings.json` turns them off; do not
  reintroduce them). Commit with explicit paths, never `git add -A`.
- Work language with Emil is Danish; code, docs and UI are English.

## Working notes

- **Block summaries and the cache** (`file._ChannelSummaries`, `_BlockCache`, `docs/beslutninger.md`
  2026-10-04 (3)): `decode_block` leaves a 32-bucket min/max summary of each block it decodes for the
  first time; `Record.summary_overview` / `overview(from_summaries=True)` (only `lod` asks for it) serves a
  detail pass from them when every block in range has one and a pixel bucket is at least a summary bucket
  wide. The cache is limited in bytes and keeps 16-bit blocks as int16; `decode_block` still returns int32.
  Anything new that reads `Record.overview` keeps the exact (decoded) answer unless it opts in.
  `Record.quick_overview` (2026-10-05 (2)) is the viewer's first picture and nothing else: summaries where blocks
  were decoded, the block index elsewhere, never a decode, each piece spread over every bucket it touches. It is
  never narrower than the samples but it is not a measurement; do not build statistics or exports on it.

- **Timing is numbers only** (`perf.py`, `bench.py`): an event is a code-literal name plus
  numeric fields, and `perf.event` raises on anything else, so file names, titles, comment
  texts and dates cannot reach a log or a bench report. `adichtion bench` prints no path.
  Without `ADICHTION_PERF` no timer exists and `perf.span` returns one shared no-op object.
  When measuring the GUI from a script never run a tight `processEvents` loop without a
  `sleep` (a GIL convoy inflates every time tenfold); `tests/ui/helpers.py` shows the pattern.
  `perf.quiet_span` (aggregated, printed only above `slow_ms`) and `perf.count` (totals in the
  summary) serve the steps that run many times per pan step; the viewer's pan events are listed in
  `src/adichtion/ui/CLAUDE.md`. `adichtion bench FILE --pan` (`ui/panbench.py`, Qt imported only
  when it runs, so `bench.py` stays Qt-free) drives a real `MainWindow`.

- LabChart's "block" = this library's `Record` (following the ADI SDK); the
  16000-tick storage chunks are "blocks" here. Don't mix the two in the UI.
- `pytest` is fast. The integration tests need `ADICHTION_SAMPLES` pointing at
  a folder with real files (they skip otherwise); the inference one takes a
  couple of minutes on the eleven 2016 files.
- Every channel's units object carries its LSB (volts per count, or the scale
  of a calculated channel), per record; `set_range_volts`/`--range-volts` is
  only a fallback for files the sequential parser cannot read
  (`AdichtFile.parse_mode == "scan"`).
- `adichtion check FILE` decodes every block and compares with the min/max
  LabChart stored in the Map stream: run it on any new kind of file before
  trusting the decoders with it.
- Unpacked sub-blocks carry no 32-bit flag and PackBits code 128 is a run of
  129; both are covered by tests, don't "fix" them back.
- Video clips live in `<name> movies/Record<N>.wmv`; `Record.video` gives
  LabChart's own offset of the record start within the file. That offset
  already includes the camera delay measured before the recording
  (`VideoClip.delay_s`, constant per file): seek to `offset_s + t`.
- The 2016-style files (v26) store the results of calculated channels but not
  their definitions. `adichtion infer` recovers them from the data and
  `adichtion macsettings` reads them from the Mac document or settings file
  (`docs/CALCULATED_CHANNELS.md`). The reader never recomputes a calculated
  channel on its own: the stored values are LabChart's and stay the reference.
- `AdichtFile.kind` is `"adicht"` or `"mac"`. Above layer 3, never assume that
  every channel has samples or one sample per tick: ask `has_data(c)` (a Mac
  document's calculated channels have dtype `"none"`), use
  `Record.tick_to_sample`/`n_samples`/`sample_at` (Mac channels can store every
  40th tick), and read a NaN min/max in the index as "not stored"
  (`block_minmax(..., fill=False)` never decodes; `fill=True` does and caches).
- Mac documents: calculated channels are listed with `channel_definition(c)` and
  never recomputed (Emil, 29-09-2026: not now, maybe later). Their movies are
  timed by the per-frame capture stamps of the movie's "Synchronisation track"
  against `mTim` (`qtmovie.py`, `VideoClip.stamped`, `docs/MAC_FORMAT.md` §7.1);
  the DV frames are interlaced and need OpenCV < 4.13 (pinned in both
  requirement lists of `pyproject.toml`, a test keeps them equal).
  `tests/test_integration_macdocs.py` finds the documents below
  `ADICHTION_SAMPLES` by their first bytes and pairs them with their exports.
- **Export writers** (`docs/beslutninger.md` 2026-10-04 (9)): text/CSV are formatted a chunk of rows at a time
  (`_strings`, `_text_rows`), the output byte-identical to the old per-value writer (kept as `legacy_write_text`
  in `tests/test_export_fast.py`, the oracle: change both or neither); xlsx streams `<sheetData>` into openpyxl's own
  empty-workbook skeleton (`write_xlsx(fast=False)` is the cell-by-cell fallback and oracle). An export writes
  `<target>.part` and renames it when complete; `export_selections(progress=, stale=)` raises `ExportCancelled`.
- Export of channels with different rates: one table at the tick rate, slower
  channels up-sampled by LabChart's own rule (`Record.read_ticks`,
  `decimate.upsample_like_labchart`: linear, whole counts half away from zero,
  last sample held; 99.998 % identical to the WinChart exports), and the user is
  told (`export.upsampling_notice` in the CLI, the dialog and the status bar).
- `pytest` (unit + synthetic) stays under a few seconds; the inference tests
  build their own synthetic document with derived channels, the viewer tests
  run headless (`tests/ui/`, `QT_QPA_PLATFORM=offscreen`).
- UI wording: LabChart's blocks are "blocks" in the app (Emil's call,
  27-09-2026), `Record` in the code. The app never recomputes a calculated
  channel; it shows LabChart's values and the recovered definition.
- One entry point: `adichtion` (`adichtion.__main__:main`) starts the app,
  `adichtion <command>` the CLI; `CLI_COMMANDS` there is a copy of the argparse
  subcommands (a test keeps them equal). There is no `adichtion-app` any more.
- The logo lives in `src/adichtion/ui/logo.py` (palette, geometry, SVG builders; the icon
  drawing up to 64 px, the full logo from 128 px). `design/logo/adichtion_logo.py` writes
  the design SVGs and renders from it, `scripts/render_icons.py` the installer icons; a test
  fails when the checked-in SVGs drift. The splash (`ui/splash.py`) is the same paper.
- Installers (`docs/RELEASING.md`, repo skill `.claude/skills/release`):
  briefcase, config in `pyproject.toml` `[tool.briefcase]`, icons in
  `installer/`. The version is in two places (`__init__.py` and
  `[tool.briefcase]`) plus a `CHANGELOG.md` entry the Linux build requires; a
  test checks all three agree. Windows MSI unsigned in CI and Certum-signed
  locally afterwards (`scripts/sign-msi-certum.sh`), macOS dmg signed+notarised
  only with the Apple secrets (`docs/SIGNING.md`), Ubuntu 24.04 `.deb` via
  Docker.
- **A locally rebuilt app can keep showing a stale icon** (`docs/RELEASING.md`
  §5): Briefcase copies `installer/adichtion.*` into the bundle at
  `briefcase create` time only, not on every `build`/`run`, and macOS itself
  also caches app icons. Regenerating `scripts/render_icons.py`'s output is
  not enough on its own — see RELEASING.md for the fresh-`create` /
  `--update-resources` fix. Not a bug in this repo: `tests/ui/test_logo.py`
  pins the committed installer icons to the current drawing. A GitHub
  *Re-run* of an old *Build installers* run rebuilds that run's old commit.
- **The packaged macOS app sets no application icon** (`ui/app.py`
  `application_icon_choice`, `ui/macbundle.py`): the bundle's `adichtion.icns`
  is the Dock and Cmd-Tab icon. `QApplication.setWindowIcon` replaced it, and
  in the PySide6 the dmg ships (6.9.3, pinned by `min_os_version = "12.0"`)
  that image is sized by its first, 16 px representation, the bare "a"
  (`docs/beslutninger.md` 2026-09-28 (5)).
  Don't reintroduce a run-time app icon on macOS; from source it is
  `logo.dock_icon()`, the full logo only.
