# Changelog

All notable changes to adichtion. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
