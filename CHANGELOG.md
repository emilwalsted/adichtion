# Changelog

All notable changes to adichtion. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
  `adichtion videos` lists the movie each block names. Format notes: `docs/MAC_FORMAT.md`.

### Changed
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

- The export's refusal to put channels with different sampling rates in one table now lists
  the rate groups; a channel that is not stored is refused by name.

### Fixed
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
