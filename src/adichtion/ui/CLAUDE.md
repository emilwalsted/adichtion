# CLAUDE.md — `src/adichtion/ui/`

The PySide6 + pyqtgraph viewer. Modelled on RespMech's UI (theme, titled panels,
x-linked channel stacks, worker threads); the project rules stay in the repo-root
`CLAUDE.md`.

## Structure

- `app.py` — the app entry point (`adichtion` without a subcommand, `python -m adichtion`, the packaged bundle): splash first, then theme and `MainWindow` built behind it, revealed maximised after `MIN_SPLASH_MS`, open dialog, macOS FileOpen events. `MIN_SPLASH_MS` is 3000 (Emil, 29-09-2026). The application icon per platform: `application_icon_choice` (none in the packaged macOS app, `dock_icon()` on macOS from source, `app_icon()` elsewhere).
- `macbundle.py` (Qt-free) — `is_packaged_app`: is this process the packaged `adichtion.app`
  (main-executable slot + our `CFBundleIdentifier` in Info.plist, not a path substring).
- `logo.py` — the a-trace logo as SVG (the single source of palette and geometry: the icon
  drawing up to 64 px, the logo from 128 px) rendered with `QSvgRenderer`: `make_pixmap`,
  `logo_pixmap`, `app_icon` (every size, window/taskbar), `dock_icon` (the logo only, the
  macOS Dock from source). `design/logo/` and `scripts/render_icons.py` render from it.
- `splash.py` — the start-up splash: a sheet of the logo's chart paper with the tile, the
  wordmark and one breathing trace with a selected sniff (`build_splash_svg` is Qt-free).
- `theme.py` — RespMech's palette and style sheet (light/dark, `ADICHTION_THEME`), the
  plot palette (`plot_palette()`), `PLOT_AXIS_WIDTH`, `PLOT_ROW_MIN_HEIGHT`.
- `timeline.py` (Qt-free) — the file axis: blocks end to end, `locate(t)`,
  `selections(t0, t1)`, comments with file times. `lod.py` (Qt-free) — level-of-detail
  curves (vectorised over `AdichtFile.block_table`, one table of the block index per channel, with
  `Timeline.starts/stops/rates/block_ticks` as the per-record arrays), value under the cursor, interval
  statistics; runs on the worker thread.
- `channel_stack.py` — `ChannelStack` (splitter of `ChannelPlot`s + `MarkerStrip` + a horizontal
  time scroll bar `hbar`, kept in step with the x range by `_sync_hbar`/`_on_hbar`),
  cursor/selection, LOD rendering through its own `JobRunner`.
- `icons.py` — the toolbar's line icons as SVG strings in the theme's colours (`toolbar_icon(name)`);
  `startup_dialog.py` — the dialog the app opens with (recent files + "Open a file…").
- `main_window.py` — menus, toolbar, the three panels (`info_panel`, `data_panel`,
  `video_panel`), dialogs (`channel_dialog`, `export_dialog`), inference and export
  jobs on a second `JobRunner`.
- **Opening a file is asynchronous** (`MainWindow.open_file`): `open_runner` (a third `JobRunner`) runs `_open_job`
  (`AdichtFile(path)`, which touches no Qt); `_on_open_done` installs the file on the GUI thread. `open_file`
  returning True means "started", not "opened". The newest request wins; a cancelled, replaced or late (window
  closing) result is closed unused; `close_file` cancels a pending open; Export and Mac settings wait while one runs.
  Tests and `bench --pan` use `open_file_sync` (the old synchronous path).
- `info_panel.py` — the file panel. **Right click on a Blocks or Comments row = "Mark interval"** (`_row_menu`, `markIntervalRequested(t0, t1)` → `MainWindow.mark_interval`: zoom to the interval with 2 % air, cursor at the start, then the selection; `set_cursor` clears a selection, so the order matters). The interval rules live in `Timeline.block_interval`/`comment_interval` (a comment runs to the next later comment (exclusive end), to the end of the file for the last; row index = index into `timeline.comments`/`blocks`, so never sort or filter these views); Channels has no menu. Blocks and Comments are `_RowsView`s over a `_RowsModel` (a cell's text is made when
  it is shown; never one item per cell: 5000 comments cost 300 ms that way), Channels stays a small `QTableWidget`.
  `_RowsView` keeps `cellClicked`/`cellDoubleClicked`/`rowCount()`. The "(n found)" video count is looked up on a
  thread (`_video_counted`, token-guarded); never call `Record.video_file` for every record on the GUI thread
  (a file-system lookup each: use `video_candidate` for the path alone).
- `warmup.py` (Qt-free) — background decoding while the user looks: `Warmer` (one per open file; `run(stale, report)` is
  the job of the stack's own `warm_runner`, a second `JobRunner` so it never queues in front of a render), `Activity`
  (when the user last did something) and `channel_blocks` (a channel's storage blocks with their file times). Two jobs:
  **prefetch** (after a detail pass that used decoded samples, `ChannelStack._request_prefetch` asks for the blocks half a
  screen to each side, nearest first, cached) and **warm-up** (a sweep over the whole file, visible channels first and
  nearest the view first, decoded with `decode_block(cache=False)` so only the summaries stay and the block cache is not
  pushed out). Both wait until `Activity` has been quiet (`IDLE_S` 0.4 s, prefetch `PREFETCH_IDLE_S` 0.12 s), sleep after each
  block (`DUTY` 0.3 of one core, never under `MIN_PAUSE_S`), stop at `stale()` and report `(done, total)` for the status bar.
- `perfmon.py` — `StallMonitor`, the 5 ms `QTimer` that measures the GUI thread's stalls; created by
  `MainWindow` only when `ADICHTION_PERF` is set (`adichtion.perf`, which also times `open`, `install.*`,
  `render.coarse`, `render.detail` and the plots' repaints).
- `panbench.py` — `adichtion bench FILE --pan`: runs a pan and zoom scenario on a real `MainWindow`
  and returns the `ADICHTION_PERF` numbers (Qt imported inside `run_pan` only).
- **The pan timeline (`ADICHTION_PERF=1`).** Per move: `input.wheel|drag|swipe` (the whole synchronous
  handling of one input, with `input.<kind>` and `input.<kind>.x_range` counted: range signals per input),
  `stack.x_range` (the master's signal) containing `stack.sync_hbar` and `stack.range_changed` (every
  listener of `ChannelStack.rangeChanged`), `vb.linked` and `vb.update_range` (the x-linked ViewBoxes),
  `strip.relayout`, `axis.ticks` inside `axis.draw_specs` (both inside the repaint `frame.paint`),
  `markers.paint`, `cursor.paint`, `cursor.set`, `cursor.values`, `curve.set_data`, `plot.auto_y`,
  `stack.render` (the debounced pass, with `render.coarse` inside), `stack.on_progress` (a decoded curve
  arriving on the GUI thread), `stack.axis_width`. Counts: `signal.x_range` (master), `signal.x_range.all`
  (every view's), `paint.plot` (plot repaints), `render.result_stale`, `render.skipped_fast`. Spans nest, so totals are not
  additive. `ev.<Type>.<receiver class>` is every top-level Qt event of the GUI thread, timed by
  `perfmon.timed_notify` from `AdichtionApplication.notify` (only when the timing is on; a
  QGraphicsView repaints inside its `MetaCall`, so `ev.MetaCall.GraphicsScene` contains `frame.paint`).
  The names are code (event type from a fixed set, a class of this program or its toolkits, at most 200 distinct names, else `ev.Other`), never data. A modal dialog's own event loop is inside the event that opened it, so that event shows the dialog's open time. `bench --pan` reports `event_types_timed` 0 when it runs in a process that already had a QApplication (no `notify` hook then). `ev.MetaCall.GraphicsScene` is expected to contain `frame.paint` (the sandbox's offscreen run showed totals of the same size; confirm on the Mac).
- `workers.py` — `JobRunner` (one thread, coalesced jobs per kind, `stale()`/`report()`)
  and `FrameGrabber` (OpenCV, latest request wins).

## Rules

- **Background decoding is polite (P9).** Anything that decodes on its own initiative (prefetch, warm-up) runs on
  `ChannelStack.warm_runner`, never on `runner` (a long job there delays every render behind it), calls
  waits for `ChannelStack.activity` through `Warmer`, and never fills the block cache with blocks nobody asked for
  (`decode_block(cache=False)`). `Warmer.cancel()` is called before the file is closed (`set_file`, `close`): it returns
  only when no block is being decoded, which is what makes closing safe. The activity is touched by a move, a click, the cursor, a render and a drawn curve (not by a y-zoom,
  a resize or hovering); anything new that reacts to the user should `touch()` it, or the background will not know to wait. The status bar
  label (`MainWindow.warm_label`) is always shown and has a fixed minimum width: showing it the first time laid the
  status bar out again and cost 70 to 110 ms in the offscreen run.

- **Only the workers decode long ranges.** The GUI thread may decode a block or two
  (the value under the cursor); anything proportional to the visible span goes through
  `JobRunner.submit(kind, fn)` and comes back via `progress`/`done` signals with a job id
  the receiver checks. `AdichtFile`'s block cache is lock-guarded, so both threads may
  decode.
- **Coarse first, but never over a decoded curve.** A pan/zoom draws the quick picture at once
  (`coarse=True`: the block index, or the block summaries where blocks are 4 px wide or more), then the
  decoded curve replaces it. `ChannelPlot.detail_covers(t0, t1, width)` decides whether the decoded curve is
  still good enough (span AND resolution): forgetting the resolution made a whole-file curve "cover" a 0.5 s
  zoom. A decoded curve that reaches into the view is NOT replaced by the quick picture
  (`detail_usable`, `ChannelStack._draw_quick`, `docs/beslutninger.md` 2026-10-05 (2)): it stays on
  `ChannelPlot.curve` untouched and only the strip beside it goes on `ChannelPlot.edge`, computed with
  `decode=False`. Replacing the whole view with the block index on every move past the margin is what made
  slow scrolls look blocky; don't bring it back. Anything that reads what a plot shows
  (`visible_y_range`) counts both curves.
- **Render passes while the view moves.** `_schedule_render` debounces (60 ms) with a ceiling
  (`RENDER_MAX_WAIT_MS`), so a steady scroll gets passes; `_render` tells a pass made mid-move
  (`settled=False`) from the one after the last move by the pace of the moves (`_move_gap`: a frame can take
  longer than the debounce). A mid-move pass asks ahead in the pan direction (`RENDER_AHEAD`, `RENDER_LEAD`),
  leaves a job that still serves the view running (`_render_live`), skips when the pan outruns the decoding
  (`_too_fast`, counted as `render.skipped_fast`) and never fits the y-axes. Curves that arrive mid-move are
  held (`_held`) and shown in the turn of the next move, or by the settled pass: that keeps the one repaint
  per plot per move below.
- **pyqtgraph auto-range is off** on every ViewBox (`disableAutoRange()`); the stack owns
  the x range (linked to the first shown plot) and each plot its y range. With it on, new
  data silently re-fitted y to the whole curve including the decode margins.
- **Body mouse = x only** (`ChannelViewBox`): the wheel zooms time, a left drag selects an
  interval (`sigSelectDrag`; under 4 px it is a click) and Alt+drag / the middle button /
  a sideways swipe (`ChannelPlotWidget`) pan; `ChannelStack.set_drag_selects(False)` swaps
  the drag roles. The y-axis wheel and the corner `+ − A` buttons zoom y. Plain click =
  cursor, Shift+click = interval, right click = `sigContextMenu`; the ViewBox emits its own
  `sigClicked`.
- **x limits go on EVERY view** (`ChannelStack._apply_limits`: each plot and the strip, with
  `maxXRange`), not on the master alone: a drag on a plot without limits left that plot's
  range out of step with the rest (measured (50,65) against (30,45)). `set_range` also
  re-syncs the views as a safety net.
- **The left-axis width is shared and measured** (`ChannelStack._update_axis_width`, from
  `MinPitchAxis.needed_width`): every channel gets the widest tick labels' width, in steps of
  4 px with a hysteresis, so the plots line up and the gap stays small. Nothing may use
  `theme.PLOT_AXIS_WIDTH` as the live width any more; use `stack.axis_width`.
- **Auto zoom** (`ChannelStack.set_auto_zoom`) fits the y-axes at the end of `_render` (already
  debounced, so after the move) and again when a decoded curve arrives; *Zoom to fit* is the one-off.
- **Lists centre the marker**: a comment or block picked in the file panel calls
  `set_cursor(t, center=True)` (zoom kept, clamped at the file's edges); interval playback keeps
  the selection (`set_cursor(..., keep_selection=True)`, `VideoPanel.play(stop_at=...)`).
- **Export runs with a progress dialog** (`MainWindow.run_export`): `QProgressDialog` with a Cancel button
  (`canceled` sets a `threading.Event`; `_export_job` turns it, or the runner's `stale()`, into `ExportCancelled` and
  returns `None`, shown as "Export cancelled."); `report((done, total))` arrives as `progress` and is shown as a percentage.
  `QProgressDialog.cancel()` does not emit `canceled` (only the button does): tests click the button.
  **Never call `QProgressDialog.setValue` on a dialog that may be showing** (`_export_progress` sets the dialog's
  `QProgressBar` instead): for a modal dialog it calls `processEvents()`, the worker's `done` is then handled inside
  the progress slot, `_finish_export` deletes the dialog, and `setValue` returns into freed memory. It segfaulted
  on the macOS CI runners and only there (a use-after-free that usually reads intact memory);
  `test_the_export_is_never_finished_from_inside_a_progress_update` checks the nesting, not the crash.
- **The video clip of an export** (`videoclip.py`, the dialog's *Video* box, `_export_job`): the box shows only when the
  exported blocks have a video file (`videoclip.has_video`, stops at the first found) and starts unticked every time;
  only the kind of clip (original copy or H.264) is remembered. The job cuts the clip FIRST (0 to 80 % of the bar),
  keeps it as `<name>.part<ext>`, writes the data, then `PendingClip.commit()`s; a cancel or a failed data export calls
  `discard()`, so neither file is left. A clip that cannot be made (`ExportRequest.video_error`) does not stop the data
  export; `_finish_export` warns. ffmpeg runs as a subprocess (never on the GUI thread), found by `videoclip.find_ffmpeg`
  (which starts a program once to check it: the dialog calls it only when a video is first on offer, never otherwise).
- **The export dialog remembers** its choices in `QSettings` group `export`; tests point
  `QSettings` at a temp dir (`tests/ui/conftest.py`), never the user's real settings.
- **One `GraphicsObject` per plot for many lines** (`MarkersItem`, `CursorItem`): they paint
  in one call using `viewRect()` for the height and return `None` from `dataBounds` so they
  never feed a range calculation. Never one `InfiniteLine` per comment.
- **One repaint per plot per move** (`tests/ui/test_repaints.py`): `ChannelViewBox.updateViewRange` applies the
  view transform at once (pyqtgraph does it inside the first paint, which dirties the plot again: two paints per
  plot), and `ChannelStack._coarse_pass` draws the block-index envelope in the same turn as the move
  (`ChannelPlot.coarse_key` keeps the debounced pass from drawing it twice; it is skipped while a pass costs more
  than `COARSE_SYNC_BUDGET_MS` and runs at most once per `COARSE_SYNC_MIN_GAP_S`; a detail result that shows less of
  the view than the curve on screen is dropped in `_show_result`, one that arrives mid-move waits in `_held` for the
  next move). Anything new that changes a plot on a move belongs in that turn, not in a timer.
- **Wide views use the block index, not a decode**: `lod.curve_for_range` takes the index
  envelope whenever a bucket spans a quarter block or more; a whole-file view of a 200 MB
  file then renders in under a second instead of ten.
- **A missing min/max is never filled on the GUI thread.** LabChart for Mac documents leave
  many blocks without one (NaN in the index): the coarse pass and `global_range` call
  `block_envelope(..., fill=False)`, the worker's pass fills them (`fill=True`, cached in
  `AdichtFile`), and a channel with no stored min/max at all sits in `ChannelStack._y_unset`
  until its first decoded curve sets the y-range. Filling on the GUI thread would decode
  every such block of a whole-file view there (some documents store no min/max at all for
  their EMG channels).
- **Channels without data** (`AdichtFile.has_data` False: a Mac document's calculated
  channels) get no plot and no data-panel row, and are greyed out with a note in the channel
  and export dialogs; the file panel still lists them with their definition.
- **Mac movies are clips like any other** once timed (`VideoClip.stamped`): the panel seeks
  `offset_s + t` and its caption says "synchronised by the movie's frame stamps" instead of a
  camera delay. A movie file that exists but cannot be read (a cloud placeholder) gets its
  own message, not "not found". The export dialog shows `upsampling_notice` in its note and
  the window repeats it in the status bar after the export.
- **Inference and statistics never share the render thread** — a 12-channel inference
  blocked every render for ten seconds when they did.
- **Threads are joined on close** (`JobRunner.stop()` marks every job stale and waits;
  `FrameGrabber.stop()`); tests close the window, so a job that ignores `stale()` shows up
  as a slow test, not a crash.
- **Playback is a clock, not a media player** (`video_panel.py`): a 40 ms timer advances
  file time at the chosen rate from the wall-clock moment play was pressed (re-anchored on
  a rate change or a click), emits `positionChanged`, and the window moves the cursor with
  `set_cursor(t, follow=True)`; frames are requested per tick and the grabber serves only
  the latest, reading forward when the target is close (`grab_frame(..., last_pos)`) and
  seeking otherwise. The panel ignores `set_position` calls that are its own tick coming
  back (`_driving`); an external cursor move while playing re-anchors the clock. The data
  table is rebuilt at most 10×/s while playing; overlays and readout follow every tick.
- **No run-time application icon in the packaged macOS app.** `QApplication.setWindowIcon`
  replaces the bundle's icns in the Dock and Cmd-Tab with an NSImage Qt builds from the
  QIcon; in PySide6 6.9.3 (what the dmg ships) that image is sized by the icon's first,
  16 px representation (the bare "a"). A window icon reaches the Dock only through
  `QCocoaIntegration::focusWindowChanged`, when the app has neither a bundle icon nor a
  `QGuiApplication` icon: the packaged app is covered by its `CFBundleIconFile`, a run from
  source by `logo.dock_icon()` (large full-logo pixmaps only). Keep both, or `MainWindow`'s
  `app_icon()` (16 px first) can end up in the Dock under a non-bundled Python.
- **`theme.apply_theme` is a no-op for an application that already has the theme** (a mark on the application
  object, valid while its style sheet is the one set with it). `setStyle`, `setPalette` and `setStyleSheet` on the
  application polish every widget alive even when nothing changed: `main()` called inside the test suite spent 3.2 s
  there on the Mac (over 800 widgets left by earlier tests, 2 ms alone), enough to run
  `test_app_main_opens_the_file_from_the_command_line` out of its 15 s on the Windows runners now and then. Don't set
  an application-wide style, palette or sheet anywhere else.
- Tests grab the widgets offscreen (`widget.grab().save(...)`) when a look needs checking;
  the platform plugin prints "does not support propagateSizeHints", which is noise.
