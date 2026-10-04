# Changelog

All notable changes to adichtion. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Built-in timing for finding what is slow on a real recording: start the app with
  `ADICHTION_PERF=1` and it writes a timeline to stderr (opening the file, each stage of
  installing it, every coarse pass, every detail pass in the worker thread with the blocks
  decoded and the cache hits, slow frames, GUI stalls, and a summary when the window closes);
  `ADICHTION_PERF_FILE=path` also writes it as JSON lines. Off by default: no extra timer.
- The timing also breaks a pan or zoom step into its parts (`ADICHTION_PERF=1`, same command as
  before): the range signal cascade (`stack.x_range`, `stack.sync_hbar`, `stack.range_changed`,
  `strip.relayout`, `vb.linked`, `vb.update_range`), the axes' tick computation
  (`axis.ticks`, `axis.draw_specs`), the curve update (`curve.set_data`), the markers and cursor,
  the worker's results arriving on the GUI thread (`stack.on_progress`), and every top-level Qt
  event of the GUI thread by type and widget class (`ev.Paint.…`, `ev.MetaCall.…`, `ev.Timer.…`;
  `ADICHTION_PERF_EVENTS=0` leaves that part out). Counts of range signals, repaints and
  wheel/drag/swipe inputs are in the summary. `adichtion bench FILE --pan [STEPS]` drives the
  real window through a pan and zoom scenario and reports the same numbers.
- `adichtion bench FILE` times the same operations without the GUI (opening, the whole-file
  range, curve passes for the whole file, 10 minutes, 60 seconds and 5 seconds, coarse and
  detailed, cold and warm, decoding per block, cursor look-ups, and with `--export` the
  export of a 60 second selection), as a table or `--json`. Both report only numbers: no file
  names, titles, comment texts or dates.
- The app icon and logo ("a-trace": one ink trace on chart-recorder paper writing an "a"
  that runs on into a selected spike), as the window and dock icon, the installer icons on
  all three platforms, the About dialog and the top of the README.
- A start-up splash on the same chart paper, shown while the window is built.
- The start dialog lists the recently opened files (and "Open a file…"), as RespMech's does.
- Select an interval by dragging over a channel (as well as click then Shift+click); Alt+drag
  pans, and *View > Drag selects an interval* swaps the two. A sideways swipe pans too, and
  Alt+Left / Alt+Right scroll half a screen.
- Right click on a channel: *Export interval…* with an interval selected, *Export whole
  recording…* otherwise; the export dialog remembers its settings (format, scope, channels,
  decimal separator, options, folder) from the last export.
- Space with an interval selected plays the video from the interval's start and stops at its end.
- *Auto zoom* is now a toggle that fits every channel's y-axis after every zoom or scroll;
  the one-off fit is *Zoom to fit* (Ctrl+A), disabled while Auto zoom is on.
- Toolbar buttons are icons with tooltips (name, what it does, shortcut).
- LabChart for Mac documents (LabChart 7 for Mac's own format, no extension; recognised by
  their first bytes) open in the app, the library and the command line: raw channels
  sample-exact against LabChart's WinChart exports, input ranges, units conversions,
  comments, block times, slow channels at their own rate (the value under the cursor is
  the latest sample). Calculated channels are not stored in these documents: they are
  listed with the stored definition and greyed out in the channel and export dialogs.
  Format notes: `docs/MAC_FORMAT.md`.
- The movies of LabChart for Mac documents (`<name> movies/LabChartMovieN.mov`) play in the
  video panel, synchronised by the capture stamp LabChart wrote for every frame;
  `adichtion videos` shows each block's movie and its offset. A movie that is only a cloud
  placeholder (not downloaded) is reported as unreadable.

### Changed
- Viewing a whole long file is faster the second time and no longer pays for every short record.
  Each block that is decoded leaves a small min/max summary (32 buckets) behind, and a detail
  pass whose pixels are at least that wide (a 1/32 block) is drawn from the summaries instead of
  decoding again, also after the block cache has been emptied; the first view of a file still
  decodes. A pixel bucket can then show an extreme at most one summary bucket (500 ticks of a
  full block) beyond its own samples, never miss one. The decoded values, the eight-bucket
  minimum per record (gone: a record narrower than a few pixels now gets as many buckets as it
  has pixels) and the exports are unchanged. The block cache is now limited by memory (a quarter
  of the free RAM, 256 MB to 1 GB; blocks of 16-bit channels kept as 16-bit) instead of by 256 blocks.
- A horizontal scroll bar under the graphs moves the zoomed time axis (enabled only while the
  view is zoomed); the splash stays up for at least 3 seconds; the empty band between the toolbar
  and the first channel is smaller.
- *File > Load LabChart for Mac settings…* is gone: pick the file type *LabChart for Mac
  settings files* in the *Open…* dialog instead.
- The view can no longer be zoomed out past the whole file, and no plot can be scrolled past its
  start or end; every plot (not only the first) now carries the limits, which also fixes plots
  drifting out of step with the others after a drag.
- Picking a comment, or a block, in the file panel scrolls the graphs to centre the marker (zoom
  unchanged); double-click a block to show it across the full width.
- The left axes take the width their tick labels need (the same for every channel) instead of
  a fixed 90 px, so the gap between the unit and the trace shrinks.
- Exporting channels with different sampling rates no longer fails: the table gets a row per
  tick and the slower channels are up-sampled as LabChart's own export does (linear
  interpolation, whole ADC counts); the command line and the export dialog say which channels
  were up-sampled from which rate. A channel that is not stored is refused by name.

### Fixed
- OpenCV is kept below 4.13: its FFmpeg 8 returns interlaced DV frames (the LabChart for Mac
  movies) black.
- macOS: the running app's Dock and app-switcher icon was a blurry bare "a" instead of
  the logo. The packaged app now leaves its bundle icon in place, and a run from source
  gives the Dock the full logo only.
- `adichtion macsettings` took property `0x2849` (21 in every file) for the channel count;
  it is `0x287F`. Settings left over from channels beyond the count are now hidden unless
  `--verbose`.
- LabChart for Mac settings: the keys of the calculations' settings move with the extension
  key ranges a file records (`McrE`); read with fixed keys, files from other installations
  lost their Arithmetic expressions and RMS/spirometry sources. They are now resolved per file.

## [0.3.0] – 2026-09-27

### Added
- The desktop app (`adichtion`, or the installers): every channel on one time axis with
  the blocks laid end to end, block labels and comment flags, cursor and interval selection
  with per-channel values and statistics, per-channel y-zoom and draggable heights, a
  channel chooser, calculated channels tinted with their definition, export of the file, a
  block or a selection as text, CSV or Excel, the video frame at the cursor and playback
  with the cursor following (0.1× to 8×).
- `adichtion infer`: recovers the definitions of calculated channels from the data
  (Arithmetic, RMS, Integral, Spirometry Flow with its zero correction, Spirometry Volume
  with the dead-space / volume-ratio correction).
- `adichtion macsettings`: reads channel settings from LabChart 7 for Mac documents and
  settings files.
- Multi-block export and Excel workbooks (`adichtion export --all-records`, `.xlsx`).
- `VideoClip.delay_s` / `file_time()`: the stored offset includes the camera delay.
- Native installers via briefcase: Windows MSI, macOS dmg, Ubuntu .deb.

## [0.2.0] – 2026-09-27

### Added
- The Map stream parsed by its grammar (v26 and v29), per-record units objects, the
  block index with min/max per block, channel-setup XML, video clips.
- Exact float32 decoding (samples reversed within 32-bit words, float32 running sum),
  verified against LabChart MATLAB exports.
- `adichtion check`, `adichtion videos`.

## [0.1.0] – 2026-09-27

### Added
- Reader for LabChart `.adicht` files: container, Map stream, PackBits and bit-plane
  decoders, lazy per-block API, text/CSV export, command line, synthetic-file tests,
  format documentation.
