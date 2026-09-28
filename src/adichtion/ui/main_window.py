"""The main window: menu bar, toolbar, the channel stack and the three panels below it."""
from __future__ import annotations

import os
import time
import traceback
from functools import partial
from typing import Optional

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QProgressDialog,
                               QSplitter, QToolBar, QWidget, QVBoxLayout)

from .. import __version__
from ..file import AdichtFile
from . import icons, lod, theme
from .channel_dialog import ChannelDialog
from .channel_stack import ChannelStack
from .data_panel import DataPanel
from .export_dialog import ExportDialog, ExportRequest
from .info_panel import InfoPanel
from .panel import titled_panel
from .startup_dialog import StartupDialog
from .timeline import Timeline
from .video_panel import VideoPanel
from .workers import JobRunner

FILE_FILTER = "LabChart files (*.adicht);;All files (*)"
MAC_FILTER = "LabChart for Mac documents and settings (*)"
MAX_RECENT = 8


def _short_error(tb: str) -> str:
    lines = [ln for ln in tb.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else tb


class MainWindow(QMainWindow):
    fileChanged = Signal(object)                 # the AdichtFile or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file: Optional[AdichtFile] = None
        self.timeline: Optional[Timeline] = None
        self.definitions: dict[int, str] = {}
        self._stats_job = 0
        self._infer_job = 0
        self._export_job = 0
        self._export_dialog: Optional[QProgressDialog] = None
        self._export_request: Optional[ExportRequest] = None
        self.settings = QSettings("adichtion", os.environ.get("ADICHTION_SETTINGS_APP", "adichtion"))   # the tests use their own
        self.setWindowTitle(f"adichtion {__version__}")
        try:
            from .logo import app_icon
            icon = app_icon()
            if icon is not None:
                self.setWindowIcon(icon)
        except Exception:                           # pragma: no cover - cosmetic
            pass

        self.stack = ChannelStack()
        self.info = InfoPanel()
        self.data = DataPanel()
        self.video = VideoPanel()
        self.bottom = QSplitter(Qt.Horizontal)
        self.bottom.setChildrenCollapsible(False)
        self.info_box = titled_panel("File", self.info)
        self.data_box = titled_panel("Data at cursor", self.data)
        self.video_box = titled_panel("Video", self.video)
        for w in (self.info_box, self.data_box, self.video_box):
            self.bottom.addWidget(w)
        self.bottom.setStretchFactor(0, 5)
        self.bottom.setStretchFactor(1, 4)
        self.bottom.setStretchFactor(2, 3)
        self.video_box.setVisible(False)
        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.stack)
        self.split.addWidget(self.bottom)
        self.split.setStretchFactor(0, 5)
        self.split.setStretchFactor(1, 2)
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(8, 6, 8, 6)
        col.addWidget(self.split)
        self.setCentralWidget(central)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.statusBar().showMessage("Ready.")

        self.stack.cursorChanged.connect(self._on_cursor)
        self.stack.selectionChanged.connect(self._on_selection)
        self.stack.rangeChanged.connect(self._on_range)
        # statistics and exports run on their own thread so they never delay a render, and the
        # inference of calculated-channel definitions (seconds per channel) on a third, so the
        # data panel never waits behind it
        self.runner = JobRunner(self)
        self.runner.progress.connect(self._on_progress)
        self.runner.done.connect(self._on_done)
        self.runner.start()
        self.infer_runner = JobRunner(self)
        self.infer_runner.progress.connect(self._on_progress)
        self.infer_runner.done.connect(self._on_done)
        self.infer_runner.start()
        self.info.blockActivated.connect(self.go_to_block)
        self.info.blockFitRequested.connect(self.fit_block)
        self.info.commentActivated.connect(self.go_to_time)
        self.stack.contextRequested.connect(self._on_context_menu)
        self.stack.autoZoomChanged.connect(self._on_auto_zoom_changed)
        self.video.btn_play.clicked.disconnect()
        self.video.btn_play.clicked.connect(self.toggle_play)     # the button and Space behave the same (intervals)
        self.video.positionChanged.connect(self._on_playback_position)
        self.video.playingChanged.connect(self._on_playing_changed)
        self._data_refresh = 0.0
        self._stats_range = None
        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(120)
        self._stats_timer.timeout.connect(self._submit_stats)
        # view choices survive a restart
        drag_selects = self.settings.value("view/dragSelects", True, type=bool)
        self.act_drag_selects.setChecked(drag_selects)
        self.stack.set_drag_selects(drag_selects)          # setChecked(False) on an unchecked action emits nothing
        self.act_auto_zoom.setChecked(self.settings.value("view/autoZoom", False, type=bool))
        self._update_actions()

    # -- actions, menus, toolbar ---------------------------------------------------------
    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, tip=None, icon=None, checkable=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            # the toolbar shows a picture, so the tooltip carries the words: name, what it does, the key
            keys = QKeySequence(shortcut).toString(QKeySequence.NativeText) if shortcut else ""
            a.setToolTip(text.rstrip("…") + (f": {tip}" if tip else "") + (f"  ({keys})" if keys else ""))
            a.setIconText(text.rstrip("…"))
            if icon:
                a.setIcon(icons.toolbar_icon(icon))
            a.setCheckable(checkable)
            (a.toggled if checkable else a.triggered).connect(slot)
            return a

        self.act_open = act("Open…", self.open_dialog, "Ctrl+O", "Open a .adicht file", icon="open")
        self.act_close = act("Close file", self.close_file, "Ctrl+W")
        self.act_export = act("Export…", self.export_dialog, "Ctrl+E",
                              "Export the file, a block or the selection as text, CSV or Excel", icon="export")
        self.act_mac = act("Load LabChart for Mac settings…", self.load_mac_settings, None,
                           "Read the calculated-channel definitions from a LabChart 7 for Mac document or settings file")
        self.act_quit = act("Quit", self.close, "Ctrl+Q")
        self.act_zoom_in = act("Zoom in", self.stack.zoom_in, "Ctrl++", "Zoom the time axis in (around the cursor)",
                               icon="zoom_in")
        self.act_zoom_in.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.act_zoom_out = act("Zoom out", self.stack.zoom_out, "Ctrl+-", "Zoom the time axis out (at most to the whole file)",
                                icon="zoom_out")
        self.act_zoom_reset = act("Reset zoom", self.stack.zoom_reset, "Ctrl+0", "Show the whole file across the full width",
                                  icon="zoom_all")
        self.act_zoom_fit = act("Zoom to fit", self.stack.zoom_to_fit, "Ctrl+A",
                                "Fit every channel's y-axis to the visible data, once", icon="zoom_fit")
        self.act_auto_zoom = act("Auto zoom", self.set_auto_zoom, "Ctrl+Shift+A",
                                 "Fit every channel's y-axis to the visible data after every zoom or scroll",
                                 icon="auto_zoom", checkable=True)
        self.act_drag_selects = act("Drag selects an interval", self.set_drag_selects, None,
                                    "On: a left drag over a channel selects an interval and Alt+drag pans. "
                                    "Off: a left drag pans and Alt+drag selects.", checkable=True)
        self.act_scroll_left = act("Scroll left", lambda: self.stack.scroll_view(-0.5), "Alt+Left",
                                   "Move the view half a screen earlier")
        self.act_scroll_right = act("Scroll right", lambda: self.stack.scroll_view(0.5), "Alt+Right",
                                    "Move the view half a screen later")
        self.act_channels = act("Show channels…", self.show_channels_dialog, "Ctrl+K", "Choose the channels to show",
                                icon="channels")
        self.act_all_channels = act("Show all channels", self.show_all_channels)
        self.act_equalize = act("Equal channel heights", self.stack.equalize)
        self.act_clear_sel = act("Clear selection", self.stack.clear_selection, "Esc", "Remove the selected interval",
                                 icon="clear_selection")
        self.act_export_interval = act("Export interval…", lambda: self.export_dialog("selection"), None,
                                       "Export the selected interval")
        self.act_export_recording = act("Export whole recording…", lambda: self.export_dialog("file"), None,
                                        "Export the whole recording")
        self.act_step_back = act("Cursor one sample back", lambda: self.stack.nudge_cursor(samples=-1), "Left")
        self.act_step_fwd = act("Cursor one sample forward", lambda: self.stack.nudge_cursor(samples=1), "Right")
        self.act_sec_back = act("Cursor one second back", lambda: self.stack.nudge_cursor(seconds=-1.0), "Shift+Left")
        self.act_sec_fwd = act("Cursor one second forward", lambda: self.stack.nudge_cursor(seconds=1.0), "Shift+Right")
        self.act_play = act("Play video", self.toggle_play, "Space",
                            "Play the block's video from the cursor (from the start of a selected interval, "
                            "stopping at its end), or pause it")
        self.act_video_start = act("Video: go to start", self.video.go_start, "Home")
        self.act_video_end = act("Video: go to end", self.video.go_end, "End")
        self.act_slower = act("Play slower", self.video.slower, "Ctrl+Down")
        self.act_faster = act("Play faster", self.video.faster, "Ctrl+Up")
        self.act_about = act("About adichtion", self.about)
        self.recent_menu = QMenu("Open recent", self)
        self.recent_menu.aboutToShow.connect(self._rebuild_recent)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_open)
        m.addMenu(self.recent_menu)
        m.addSeparator()
        m.addAction(self.act_export)
        m.addAction(self.act_mac)
        m.addSeparator()
        m.addAction(self.act_close)
        m.addAction(self.act_quit)
        v = mb.addMenu("&View")
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset, self.act_zoom_fit, self.act_auto_zoom):
            v.addAction(a)
        v.addSeparator()
        for a in (self.act_scroll_left, self.act_scroll_right, self.act_drag_selects):
            v.addAction(a)
        v.addSeparator()
        for a in (self.act_channels, self.act_all_channels, self.act_equalize):
            v.addAction(a)
        v.addSeparator()
        v.addAction(self.act_clear_sel)
        c = v.addMenu("Cursor")
        for a in (self.act_step_back, self.act_step_fwd, self.act_sec_back, self.act_sec_fwd):
            c.addAction(a)
        vid = mb.addMenu("&Video")
        for a in (self.act_play, self.act_video_start, self.act_video_end, self.act_slower, self.act_faster):
            vid.addAction(a)
        h = mb.addMenu("&Help")
        h.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setIconSize(QSize(22, 22))
        self.addToolBar(tb)
        tb.addAction(self.act_open)
        tb.addAction(self.act_export)
        tb.addSeparator()
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset, self.act_zoom_fit, self.act_auto_zoom):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_channels)
        tb.addAction(self.act_clear_sel)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy())
        tb.addWidget(spacer)
        self.readout = QLabel("")
        self.readout.setProperty("status", "muted")
        self.readout.setContentsMargins(8, 0, 8, 0)
        tb.addWidget(self.readout)
        self.toolbar = tb

    def _update_actions(self) -> None:
        has = self.file is not None
        for a in (self.act_close, self.act_export, self.act_mac, self.act_zoom_in, self.act_zoom_out, self.act_zoom_reset,
                  self.act_auto_zoom, self.act_channels, self.act_all_channels, self.act_equalize, self.act_clear_sel,
                  self.act_step_back, self.act_step_fwd, self.act_sec_back, self.act_sec_fwd,
                  self.act_scroll_left, self.act_scroll_right):
            a.setEnabled(has)
        # "Zoom to fit" is pointless while Auto zoom fits after every move
        self.act_zoom_fit.setEnabled(has and not self.act_auto_zoom.isChecked())
        for a in (self.act_play, self.act_video_start, self.act_video_end, self.act_slower, self.act_faster):
            a.setEnabled(has and self.video.has_videos)

    # -- view choices ------------------------------------------------------------------
    def set_auto_zoom(self, on: bool) -> None:
        """Auto zoom: fit every channel's y-axis after every zoom or scroll (and now)."""
        self.stack.set_auto_zoom(on)

    def _on_auto_zoom_changed(self, on: bool) -> None:
        self.settings.setValue("view/autoZoom", bool(on))
        if self.act_auto_zoom.isChecked() != on:
            self.act_auto_zoom.setChecked(on)
        self._update_actions()

    def set_drag_selects(self, on: bool) -> None:
        self.stack.set_drag_selects(on)
        self.settings.setValue("view/dragSelects", bool(on))

    # -- files --------------------------------------------------------------------------
    def recent_files(self) -> list[str]:
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [p for p in (v or []) if isinstance(p, str)]

    def _remember(self, path: str) -> None:
        rec = [p for p in self.recent_files() if p != path]
        self.settings.setValue("recent", [path] + rec[: MAX_RECENT - 1])

    def _rebuild_recent(self) -> None:
        self.recent_menu.clear()
        files = self.recent_files()
        if not files:
            a = self.recent_menu.addAction("(no recent files)")
            a.setEnabled(False)
            return
        for p in files:
            a = self.recent_menu.addAction(os.path.basename(p))
            a.setToolTip(p)
            a.triggered.connect(partial(self.open_file, p))

    def open_startup(self) -> None:
        """What the app does when it opens without a file: the start dialog with the recent
        files, or straight to the file dialog while there are none."""
        recent = self.recent_files()
        if not recent:
            self.open_dialog()
            return
        dlg = StartupDialog(recent, self)
        if dlg.exec() == StartupDialog.Accepted:
            if dlg.path:
                self.open_file(dlg.path)
            elif dlg.browse:
                self.open_dialog()
        dlg.deleteLater()

    def open_dialog(self) -> None:
        start = os.path.dirname(self.file.path) if self.file else (os.path.dirname(self.recent_files()[0]) if self.recent_files() else "")
        path, _ = QFileDialog.getOpenFileName(self, "Open LabChart file", start, FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            f = AdichtFile(path)
        except Exception:                           # noqa: BLE001 - reported to the user
            QApplication.restoreOverrideCursor()
            tb = traceback.format_exc()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Could not open the file")
            box.setText(f"{os.path.basename(path)} could not be read.\n\n{_short_error(tb)}")
            box.setDetailedText(tb)
            box.exec()
            return False
        try:
            self._install_file(f)
        finally:
            QApplication.restoreOverrideCursor()
        self._remember(path)
        self.statusBar().showMessage(f"Opened {os.path.basename(path)}: {len(f.records)} blocks, {f.n_channels} channels.")
        return True

    def _install_file(self, f: Optional[AdichtFile]) -> None:
        old = self.file
        self.file = f
        self.timeline = Timeline(f) if f is not None else None
        self.definitions = {}
        self.stack.set_file(f)
        self.info.set_file(f, self.timeline)
        self.data.set_file(f, self.timeline)
        self.video.set_file(f, self.timeline)
        self.video_box.setVisible(self.video.has_videos)
        self.readout.setText("")
        self.setWindowTitle(f"adichtion {__version__}" + (f" – {os.path.basename(f.path)}" if f else ""))
        self._update_actions()
        if old is not None:
            old.close()
        if f is not None:
            self._start_inference()
        self.fileChanged.emit(f)

    def close_file(self) -> None:
        self._install_file(None)
        self.statusBar().showMessage("File closed.")

    # -- calculated channels ---------------------------------------------------------------
    def _start_inference(self) -> None:
        f = self.file
        targets = [c for c in range(f.n_channels) if f.index.channel_dtype(c) == "float32"]
        if not targets or not f.records:
            return
        rec = max(f.records, key=lambda r: r.ticks)
        self._infer_job = self.infer_runner.submit("infer", partial(_infer_job, rec, targets))

    def set_definition(self, channel: int, text: Optional[str]) -> None:
        if text:
            self.definitions[channel] = text
        self.stack.set_definition(channel, text)
        self.info.set_definition(channel, text)

    def load_mac_settings(self, path: Optional[str] = None) -> None:
        if self.file is None:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "LabChart for Mac document or settings file",
                                                  os.path.dirname(self.file.path), MAC_FILTER)
        if not path:
            return
        try:
            from ..macchart import channel_settings
            settings = channel_settings(path)
        except Exception as exc:                    # noqa: BLE001
            QMessageBox.warning(self, "Not a LabChart for Mac file", f"{os.path.basename(path)}: {exc}")
            return
        n = 0
        for ch, s in settings.items():
            c = ch - 1
            if s.is_calculated and 0 <= c < self.file.n_channels:
                inferred = self.definitions.get(c)
                text = s.describe() + (f"   (data: {inferred})" if inferred and inferred != s.describe() else "")
                self.set_definition(c, text)
                n += 1
        self.statusBar().showMessage(f"Definitions of {n} calculated channels read from {os.path.basename(path)}.")

    # -- cursor, selection, panels ---------------------------------------------------------
    def _on_cursor(self, t) -> None:
        if t is None:
            self.readout.setText("")
            self.data.show_point(None)
            return
        self.readout.setText(self.timeline.describe(t) if self.timeline else f"{t:.3f} s")
        self.video.set_position(t)               # ignored while the panel's own clock is moving the cursor
        if self.video.playing:
            # the table is rebuilt at most ten times a second while the video plays; the
            # channel overlays and the readout follow every tick
            now = time.monotonic()
            if now - self._data_refresh < 0.1:
                return
            self._data_refresh = now
        self.data.show_point(t)

    def toggle_play(self) -> None:
        """Space: pause when playing; otherwise play from the cursor, or, with an interval
        selected, from its start to its end (the interval stays selected)."""
        if self.video.playing:
            self.video.pause()
            return
        sel = self.stack.selection
        if sel is None:
            self.video.play()
            return
        t0, t1 = sel
        self.stack.set_cursor(t0, follow=True, keep_selection=True)      # the video panel locates its clip
        self.video.play(stop_at=t1)
        if not self.video.playing:
            self.statusBar().showMessage("The interval starts in a block without a playable video.")

    def _on_playback_position(self, t: float) -> None:
        """The video clock moved: the cursor (and everything that follows it) goes along; a
        selected interval stays selected while it plays."""
        self.stack.set_cursor(t, follow=True, keep_selection=self.stack.selection is not None)

    def _on_playing_changed(self, playing: bool) -> None:
        self.act_play.setText("Pause video" if playing else "Play video")
        if not playing and self.stack.cursor is not None:
            self.data.show_point(self.stack.cursor)         # the final position, unthrottled
        self.statusBar().showMessage(f"Playing at {self.video.rate:g}×." if playing else "Paused.")

    def _on_selection(self, sel) -> None:
        if sel is None:
            if self.stack.cursor is not None:
                self.data.show_point(self.stack.cursor)
            return
        t0, t1 = sel
        self.data.show_interval(t0, t1)
        self._stats_range = (t0, t1)
        self._stats_timer.start()                 # a drag changes the interval on every mouse move: decode once it rests
        self.readout.setText(f"Selection {t0:.3f} → {t1:.3f} s ({t1 - t0:.3f} s)")

    def _submit_stats(self) -> None:
        if self.file is None or self._stats_range is None or self.stack.selection is None:
            return
        t0, t1 = self._stats_range
        self._stats_job = self.runner.submit("stats", partial(_stats_job, self.file, self.timeline, t0, t1))

    def _on_range(self, t0: float, t1: float) -> None:
        if self.stack.cursor is None and self.stack.selection is None:
            self.readout.setText(f"Showing {t0:.2f} – {t1:.2f} s")

    def go_to_time(self, t: float) -> None:
        """A comment picked from a list: the cursor goes there and the view scrolls to centre it."""
        self.stack.set_cursor(t, center=True)

    def go_to_block(self, index: int) -> None:
        """A block picked from the list (its video clip is that block's): the cursor goes to
        the block's start and the view scrolls to centre it, the zoom unchanged."""
        if self.timeline is None or not 0 <= index < len(self.timeline.blocks):
            return
        self.stack.set_cursor(self.timeline.blocks[index].start_s, center=True)

    def fit_block(self, index: int) -> None:
        """Double-click on a block: show that block across the full width."""
        if self.timeline is None or not 0 <= index < len(self.timeline.blocks):
            return
        b = self.timeline.blocks[index]
        self.stack.set_range(b.start_s, b.stop_s)
        self.stack.set_cursor(b.start_s)

    def _on_progress(self, job_id: int, kind: str, payload) -> None:
        if kind == "stats" and job_id == self._stats_job:
            c, stats = payload
            self.data.set_stats(c, stats)
        elif kind == "infer" and job_id == self._infer_job:
            c, text = payload
            if c not in self.definitions:
                self.set_definition(c, text)

    def _on_done(self, job_id: int, kind: str, result, error) -> None:
        if kind == "export" and job_id == self._export_job:
            self._finish_export(result, error)
        elif error and kind in ("stats", "infer"):
            self.statusBar().showMessage(f"{kind} failed: {_short_error(error)}")

    # -- channels ----------------------------------------------------------------------------
    def show_channels_dialog(self) -> None:
        if self.file is None:
            return
        f = self.file
        dlg = ChannelDialog(f.channel_titles, [f.index.channel_dtype(c) == "float32" for c in range(f.n_channels)],
                            self.stack.visible_channels, self)
        if dlg.exec() == ChannelDialog.Accepted:
            chosen = dlg.selected()
            if chosen:
                self.stack.set_visible_channels(chosen, equalize=True)
            else:
                self.statusBar().showMessage("At least one channel must stay visible.")
        dlg.deleteLater()

    def show_all_channels(self) -> None:
        if self.file is not None:
            self.stack.set_visible_channels(list(range(self.file.n_channels)), equalize=True)

    # -- export --------------------------------------------------------------------------
    def export_dialog(self, default_scope: Optional[str] = None) -> None:
        """The export dialog; ``default_scope`` ("selection" or "file") presets what to export."""
        if self.file is None or self.timeline is None:
            return
        if not isinstance(default_scope, str):              # a QAction's ``triggered(checked)`` lands here
            default_scope = None
        dlg = ExportDialog(self.file, self.timeline, self.stack.visible_channels, self.stack.cursor, self.stack.selection,
                           self, settings=self.settings, default_scope=default_scope)
        if dlg.exec() == ExportDialog.Accepted and dlg.request is not None:
            self.run_export(dlg.request)
        dlg.deleteLater()

    def _on_context_menu(self, global_pos) -> None:
        """Right click on a plot: export the selected interval, or, with none, the whole recording."""
        if self.file is None:
            return
        menu = QMenu(self)
        if self.stack.selection is not None:
            menu.addAction(self.act_export_interval)
            menu.addAction(self.act_export_recording)
            menu.addSeparator()
            menu.addAction(self.act_clear_sel)
        else:
            menu.addAction(self.act_export_recording)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        self._context_menu = menu
        menu.popup(global_pos)

    def run_export(self, req: ExportRequest) -> None:
        from ..export import export_selections
        self._export_request = req
        f = self.file
        self._export_job = self.runner.submit(
            "export", partial(_export_job, f, req, export_selections), coalesce=False)
        dlg = QProgressDialog(f"Exporting {req.scope_label} to {os.path.basename(req.path)}…", "", 0, 0, self)
        dlg.setWindowTitle("Export")
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.setValue(0)
        self._export_dialog = dlg

    def _finish_export(self, result, error) -> None:
        if self._export_dialog is not None:
            self._export_dialog.reset()
            self._export_dialog.deleteLater()
            self._export_dialog = None
        req = self._export_request
        if error:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Export failed")
            box.setText(_short_error(error))
            box.setDetailedText(error)
            box.exec()
            self.statusBar().showMessage("Export failed.")
            return
        self.statusBar().showMessage(f"Exported {result:,} rows to {req.path if req else 'file'}.")

    # -- misc ---------------------------------------------------------------------------
    def about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About adichtion")
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b>adichtion {__version__}</b><br>Read and explore ADInstruments LabChart .adicht files "
                    "without LabChart.<br><br>GPL-3.0-or-later · Emil Ingerslev Walsted<br>"
                    "<a href='https://github.com/emilwalsted/adichtion'>github.com/emilwalsted/adichtion</a><br><br>"
                    "ADInstruments, LabChart and PowerLab are trademarks of ADInstruments; this project is not affiliated with them.")
        try:
            from .logo import logo_pixmap
            pm = logo_pixmap(96, self.devicePixelRatioF())
            if pm is not None:
                box.setIconPixmap(pm)
        except Exception:                            # noqa: BLE001 - cosmetic
            pass
        box.exec()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not getattr(self, "_split_done", False):
            self._split_done = True
            h = max(600, self.split.height())
            self.split.setSizes([int(h * 0.64), int(h * 0.36)])
            w = max(900, self.bottom.width())
            self.bottom.setSizes([int(w * 0.42), int(w * 0.33), int(w * 0.25)])

    def closeEvent(self, ev) -> None:
        try:
            self.stack.close()
            self.runner.stop()
            self.infer_runner.stop()
            self.video.close()
            if self.file is not None:
                self.file.close()
                self.file = None
        finally:
            super().closeEvent(ev)


# -- worker jobs (run on the stack's job runner thread) ----------------------------------------

def _stats_job(f, tl, t0, t1, stale, report):
    for c in range(f.n_channels):
        if stale():
            return None
        report((c, lod.interval_stats(f, tl, c, t0, t1, stale=stale)))
    return f.n_channels


def _infer_job(rec, targets, stale, report):
    from ..infer import infer_channel
    for c in targets:
        if stale():
            return None
        try:
            hyps = infer_channel(rec, c, length=200_000)
        except Exception as exc:                    # noqa: BLE001 - one channel must not stop the rest
            report((c, f"inference failed: {exc}"))
            continue
        if hyps:
            h = hyps[0]
            text = f"{h.description}  ({h.quality})"
        else:
            text = "definition not recoverable from the other channels"
        report((c, text))
    return len(targets)


def _export_job(f, req, export_selections, stale, report):
    return export_selections(f, req.selections, req.channels, req.path, fmt=req.fmt, decimal=req.decimal,
                             header=req.header, units=req.units, comments=req.comments)
