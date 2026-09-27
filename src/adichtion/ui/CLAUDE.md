# CLAUDE.md — `src/adichtion/ui/`

The PySide6 + pyqtgraph viewer. Modelled on RespMech's UI (theme, titled panels,
x-linked channel stacks, worker threads); the project rules stay in the repo-root
`CLAUDE.md`.

## Structure

- `app.py` — entry point (`adichtion-app`): theme, maximised `MainWindow`, open dialog.
- `theme.py` — RespMech's palette and style sheet (light/dark, `ADICHTION_THEME`), the
  plot palette (`plot_palette()`), `PLOT_AXIS_WIDTH`, `PLOT_ROW_MIN_HEIGHT`.
- `timeline.py` (Qt-free) — the file axis: blocks end to end, `locate(t)`,
  `selections(t0, t1)`, comments with file times. `lod.py` (Qt-free) — level-of-detail
  curves, value under the cursor, interval statistics; runs on the worker thread.
- `channel_stack.py` — `ChannelStack` (splitter of `ChannelPlot`s + `MarkerStrip`),
  cursor/selection, LOD rendering through its own `JobRunner`.
- `main_window.py` — menus, toolbar, the three panels (`info_panel`, `data_panel`,
  `video_panel`), dialogs (`channel_dialog`, `export_dialog`), inference and export
  jobs on a second `JobRunner`.
- `workers.py` — `JobRunner` (one thread, coalesced jobs per kind, `stale()`/`report()`)
  and `FrameGrabber` (OpenCV, latest request wins).

## Rules

- **Only the workers decode long ranges.** The GUI thread may decode a block or two
  (the value under the cursor); anything proportional to the visible span goes through
  `JobRunner.submit(kind, fn)` and comes back via `progress`/`done` signals with a job id
  the receiver checks. `AdichtFile`'s block cache is lock-guarded, so both threads may
  decode.
- **Coarse first.** A pan/zoom draws the block-index envelope at once (`coarse=True`),
  then the decoded curve replaces it. `ChannelPlot.detail_covers(t0, t1, width)` decides
  whether the decoded curve is still good enough (span AND resolution): forgetting the
  resolution made a whole-file curve "cover" a 0.5 s zoom.
- **pyqtgraph auto-range is off** on every ViewBox (`disableAutoRange()`); the stack owns
  the x range (linked to the first shown plot) and each plot its y range. With it on, new
  data silently re-fitted y to the whole curve including the decode margins.
- **Body mouse = x only** (`ChannelViewBox`): wheel and drag over a plot pan/zoom time;
  the y-axis wheel and the corner `+ − A` buttons zoom y. Plain click = cursor,
  Shift+click = interval; the ViewBox emits `sigClicked` itself.
- **One `GraphicsObject` per plot for many lines** (`MarkersItem`, `CursorItem`): they paint
  in one call using `viewRect()` for the height and return `None` from `dataBounds` so they
  never feed a range calculation. Never one `InfiniteLine` per comment.
- **Wide views use the block index, not a decode**: `lod.curve_for_range` takes the index
  envelope whenever a bucket spans a quarter block or more; a whole-file view of a 200 MB
  file then renders in under a second instead of ten.
- **Inference and statistics never share the render thread** — a 12-channel inference
  blocked every render for ten seconds when they did.
- **Threads are joined on close** (`JobRunner.stop()` marks every job stale and waits;
  `FrameGrabber.stop()`); tests close the window, so a job that ignores `stale()` shows up
  as a slow test, not a crash.
- Tests grab the widgets offscreen (`widget.grab().save(...)`) when a look needs checking;
  the platform plugin prints "does not support propagateSizeHints", which is noise.
