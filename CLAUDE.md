# CLAUDE.md — adichtion

Project memory for **adichtion**: a reader (and, later, a desktop viewer/exporter
app) for ADInstruments LabChart `.adicht` files, built on a reverse-engineered
format description. Private repo `emilwalsted/adichtion`, GPL-3.0-or-later.
Author: Emil Ingerslev Walsted. The app is modelled on RespMech
(`emilwalsted/respmech`): same layering, packaging and release discipline.

## Sources of truth

- `docs/FORMAT.md` — the format as far as it is known, with the evidence and
  the open questions. Every decoder change must keep it accurate.
- `docs/beslutninger.md` — dated decisions and "settled, don't reopen" points.
- `src/adichtion/synth.py` — the executable form of the spec: it writes
  synthetic files that the reader must round-trip exactly (`tests/`).
- `README.md` — user-facing overview.

## Layers (keep them clean)

1. `container.py` — the compound-file container: header, cluster map,
   storages, streams. Pure `struct` + `mmap`, no numpy.
2. `serialization.py`, `chartdata.py`, `model.py` — the `Map` stream
   (records, blocks, comments, titles, units records, channel-setup XML) into
   plain dataclasses that carry no file handles or arrays.
3. `blocks.py` — sub-block index and the PackBits/bit-plane decoders
   (int16 and float32). Stateless numpy.
4. `file.py` (`AdichtFile`, `Record`, `Selection`) + `decimate.py` — the API the
   UI will use: lazy per-block decoding with an LRU cache, unit conversion,
   selections by time or comments, min/max envelopes for plotting.
5. `export.py`, `cli.py` — text/CSV export and the command line.

The UI (not yet written) goes in `src/adichtion/ui/` and must talk to layer 4
only. Never put Qt imports below that layer.

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
- **Distinguish verified from inferred** in docs and docstrings: int16 = verified
  against exports; float32 (calculated channels) = verified by internal
  consistency only; anything else = inferred. Update `docs/FORMAT.md` §6/§7
  when the status changes.
- **Ask before any upload to a live site or a release tag.**
- Work language with Emil is Danish; code, docs and UI are English.

## Working notes

- LabChart's "block" = this library's `Record` (following the ADI SDK); the
  16000-tick storage chunks are "blocks" here. Don't mix the two in the UI.
- `pytest` is fast (< 2 s). The integration tests need `ADICHTION_SAMPLES`
  pointing at a folder with real files (they skip otherwise).
- The 2016-style files (format v26) carry no input ranges: `AdichtFile.set_range_volts`
  (or `--range-volts` on the CLI) is how a user supplies them. Their units
  records cannot yet be mapped to channels (`docs/FORMAT.md` §6).
