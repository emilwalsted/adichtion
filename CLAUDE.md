# CLAUDE.md — adichtion

Project memory for **adichtion**: a reader (and, later, a desktop viewer/exporter
app) for ADInstruments LabChart `.adicht` files, built on a reverse-engineered
format description. Private repo `emilwalsted/adichtion`, GPL-3.0-or-later.
Author: Emil Ingerslev Walsted. The app is modelled on RespMech
(`emilwalsted/respmech`): same layering, packaging and release discipline.

## Sources of truth

- `docs/FORMAT.md` — the format as far as it is known, with the evidence and
  the open questions. Every decoder change must keep it accurate.
- `docs/CALCULATED_CHANNELS.md` — how LabChart's calculations behave at sample
  level, the Mac settings file format and how definitions are recovered.
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
4. `file.py` (`AdichtFile`, `Record`, `Selection`) + `decimate.py` + `video.py` — the
   API the UI will use: lazy per-block decoding with an LRU cache, unit
   conversion per record, selections by time or comments, min/max envelopes
   for plotting (from the file's block index when possible), video clips.
5. `export.py`, `cli.py` — text/CSV export and the command line.
6. `infer.py` (numerical recovery of calculated-channel definitions, on top of
   layer 4) and `macchart.py` (LabChart 7 for Mac documents/settings files,
   standalone). Both are analysis helpers the UI may call; neither changes
   what `AdichtFile` returns.

7. `ui/` — the PySide6/pyqtgraph viewer (`adichtion-app`). It talks to layers
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
- **Ask before any upload to a live site or a release tag.**
- Work language with Emil is Danish; code, docs and UI are English.

## Working notes

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
- `pytest` (unit + synthetic) stays under a few seconds; the inference tests
  build their own synthetic document with derived channels, the viewer tests
  run headless (`tests/ui/`, `QT_QPA_PLATFORM=offscreen`).
- UI wording: LabChart's blocks are "blocks" in the app (Emil's call,
  27-09-2026), `Record` in the code. The app never recomputes a calculated
  channel; it shows LabChart's values and the recovered definition.
