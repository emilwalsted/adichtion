"""Zoom limits and sync, interval selection by drag, the context menu, playback of an interval,
auto zoom, list activation, the toolbar's icons, the start dialog, the shared axis width and the
export dialog's memory (headless Qt, real mouse events on the plots)."""
import xml.etree.ElementTree as ET

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFileDialog

from adichtion.ui import icons
from adichtion.ui.export_dialog import ExportDialog
from adichtion.ui.startup_dialog import StartupDialog, recent_entries
from helpers import pump, wait_until


# -- helpers ---------------------------------------------------------------------------------

def _widget(plot):
    return getattr(plot, "widget", plot)             # a ChannelPlot wraps its PlotWidget; the marker strip is one


def _send(plot, kind, pos, button, buttons, modifier=Qt.NoModifier):
    vp = _widget(plot).viewport()
    ev = QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)), button, buttons, modifier)
    QApplication.sendEvent(vp, ev)


def _px(plot, t):
    """Viewport position of file time ``t`` in the middle of ``plot``."""
    p = _widget(plot).mapFromScene(plot.vb.mapViewToScene(QPointF(t, 0)))
    return QPoint(p.x(), _widget(plot).viewport().height() // 2)


def drag(plot, t0, t1, modifier=Qt.NoModifier, button=Qt.LeftButton, steps=10):
    a, b = _px(plot, t0), _px(plot, t1)
    _send(plot, QEvent.MouseButtonPress, a, button, button, modifier)
    for k in range(1, steps + 1):
        _send(plot, QEvent.MouseMove, QPoint(a.x() + (b.x() - a.x()) * k // steps, a.y()), Qt.NoButton, button, modifier)
    _send(plot, QEvent.MouseButtonRelease, b, button, Qt.NoButton, modifier)


def click(plot, t, button=Qt.LeftButton, modifier=Qt.NoModifier):
    a = _px(plot, t)
    _send(plot, QEvent.MouseButtonPress, a, button, button, modifier)
    _send(plot, QEvent.MouseButtonRelease, a, button, Qt.NoButton, modifier)


def ranges(stack):
    return [tuple(round(v, 6) for v in stack.plots[c].vb.viewRange()[0]) for c in stack.visible_channels]


# -- 1. never zoom out past the file, 2. never out of step ------------------------------------

def test_the_whole_file_is_the_widest_view(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    assert st.view_range() == pytest.approx((0.0, 45.0))
    st.zoom_out()
    assert st.view_range() == pytest.approx((0.0, 45.0))
    st.set_range(-10.0, 200.0)
    assert st.view_range() == pytest.approx((0.0, 45.0))
    st.set_range(10.0, 20.0)
    for plot in (st.plots[0], st.plots[2]):               # the wheel over ANY plot
        plot.vb.scaleBy((50.0, None))
        pump(qapp, 0.05)
        assert ranges(st) == [(0.0, 45.0)] * 4 and st.strip.vb.viewRange()[0] == pytest.approx([0.0, 45.0])
        st.set_range(10.0, 20.0)
    # the widest view is drawn across the plot's full width
    m = st.plots[0].vb
    st.zoom_reset()
    assert m.mapViewToScene(QPointF(0.0, 0.0)).x() == pytest.approx(m.sceneBoundingRect().left(), abs=1.5)
    assert m.mapViewToScene(QPointF(45.0, 0.0)).x() == pytest.approx(m.sceneBoundingRect().right(), abs=1.5)


@pytest.mark.parametrize("index", [1, 3])
def test_panning_any_plot_stops_at_the_file_and_keeps_every_plot_in_step(qapp, window, synthetic, index):
    window.open_file(synthetic["path"])
    st = window.stack
    vb = st.plots[index].vb
    st.set_range(30.0, 40.0)
    vb.translateBy(x=25.0)                                # a drag well past the end
    pump(qapp, 0.05)
    assert ranges(st) == [(35.0, 45.0)] * 4
    st.set_range(5.0, 15.0)
    vb.translateBy(x=-25.0)                               # and past the start
    pump(qapp, 0.05)
    assert ranges(st) == [(0.0, 10.0)] * 4 and st.strip.vb.viewRange()[0] == pytest.approx([0.0, 10.0])


def test_panning_with_the_mouse_stays_in_step(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    st.set_range(30.0, 40.0)
    pump(qapp, 0.05)
    drag(st.plots[2], 38.0, 5.0, modifier=Qt.AltModifier)           # Alt+drag pans: the data follows the mouse, past the end
    pump(qapp, 0.05)
    assert ranges(st) == [(35.0, 45.0)] * 4
    assert st.selection is None
    drag(st.plots[3], 36.0, 44.0, modifier=Qt.AltModifier)          # and back past the start
    drag(st.plots[3], 36.0, 44.0, modifier=Qt.AltModifier)
    drag(st.plots[3], 36.0, 44.0, modifier=Qt.AltModifier)
    pump(qapp, 0.05)
    r = ranges(st)
    assert len(set(r)) == 1 and r[0][0] >= 0.0 and r[0][1] - r[0][0] == pytest.approx(10.0)


# -- 7. an interval by drag, by Shift+click; the context menu ---------------------------------

def test_a_drag_selects_an_interval_and_a_short_one_is_a_click(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    drag(st.plots[1], 10.0, 20.0)
    pump(qapp, 0.05)
    a, b = st.selection
    assert a == pytest.approx(10.0, abs=0.1) and b == pytest.approx(20.0, abs=0.1)
    assert st.cursor == pytest.approx(a)                              # the cursor at the drag's start, as with Shift+click
    assert st.view_range() == pytest.approx((0.0, 45.0))              # nothing panned
    drag(st.plots[3], 30.0, 12.0)                                     # backwards, on another plot
    a, b = st.selection
    assert a == pytest.approx(12.0, abs=0.1) and b == pytest.approx(30.0, abs=0.1)
    # a drag of a couple of pixels is a click: the cursor, no interval
    st.clear_selection()
    x = _px(st.plots[1], 5.0)
    _send(st.plots[1], QEvent.MouseButtonPress, x, Qt.LeftButton, Qt.LeftButton)
    _send(st.plots[1], QEvent.MouseMove, x + QPoint(2, 0), Qt.NoButton, Qt.LeftButton)
    _send(st.plots[1], QEvent.MouseButtonRelease, x + QPoint(2, 0), Qt.LeftButton, Qt.NoButton)
    assert st.selection is None
    # click then Shift+click still works
    click(st.plots[0], 8.0)
    click(st.plots[0], 14.0, modifier=Qt.ShiftModifier)
    a, b = st.selection
    assert a == pytest.approx(8.0, abs=0.1) and b == pytest.approx(14.0, abs=0.1)
    # dragging on the marker strip selects too
    st.clear_selection()
    drag(st.strip, 20.0, 25.0)
    assert st.selection is not None and st.selection[1] == pytest.approx(25.0, abs=0.2)


def test_the_drag_mode_can_be_swapped(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    st.set_range(10.0, 30.0)
    window.act_drag_selects.setChecked(False)                         # a plain drag pans, Alt+drag selects
    assert window.settings.value("view/dragSelects", True, type=bool) is False
    drag(st.plots[1], 20.0, 15.0)
    pump(qapp, 0.05)
    assert st.selection is None and st.view_range()[0] > 10.5
    st.set_range(10.0, 30.0)
    drag(st.plots[1], 12.0, 18.0, modifier=Qt.AltModifier)
    assert st.selection is not None and st.view_range() == pytest.approx((10.0, 30.0))
    window.act_drag_selects.setChecked(True)
    assert window.settings.value("view/dragSelects", False, type=bool) is True


def test_right_click_offers_export_of_the_interval_or_the_whole_recording(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    seen = []
    st.contextRequested.connect(seen.append)
    click(st.plots[1], 10.0, button=Qt.RightButton)
    assert len(seen) == 1
    window._on_context_menu(QPoint(100, 100))
    assert [a.text() for a in window._context_menu.actions()] == ["Export whole recording…"]
    window._context_menu.close()
    st.set_selection(5.0, 12.0)
    window._on_context_menu(QPoint(100, 100))
    texts = [a.text() for a in window._context_menu.actions() if not a.isSeparator()]
    assert texts == ["Export interval…", "Export whole recording…", "Clear selection"]
    window._context_menu.close()


def test_the_context_menu_presets_the_export_scope(qapp, window, synthetic, monkeypatch):
    window.open_file(synthetic["path"])
    st = window.stack
    made = []

    class Spy(ExportDialog):
        def exec(self):
            made.append(self)
            return 0

    monkeypatch.setattr("adichtion.ui.main_window.ExportDialog", Spy)
    st.set_cursor(42.5)                                               # a cursor in block 2 would make "block" the default
    window.export_dialog("file")
    assert made[-1].rb_file.isChecked() and not made[-1].rb_block.isChecked()
    st.set_selection(5.0, 12.0)
    window.export_dialog("selection")
    assert made[-1].rb_sel.isChecked()
    window.export_dialog()                                            # the toolbar with an interval set: the interval
    assert made[-1].rb_sel.isChecked()
    window.act_export.trigger()                                       # a QAction passes ``checked``, not a scope
    assert made[-1].rb_sel.isChecked()


# -- 8. the export dialog remembers --------------------------------------------------------------

def test_the_export_dialog_remembers_the_last_choices(qapp, window, synthetic, tmp_path, monkeypatch):
    window.open_file(synthetic["path"])
    f, tl, settings = window.file, window.timeline, window.settings
    settings.remove("export")
    asked = []

    def fake_save(parent, title, suggested, filt):
        asked.append(suggested)
        return str(tmp_path / "out" / "x.csv"), filt

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_save))
    (tmp_path / "out").mkdir()
    dlg = ExportDialog(f, tl, [0, 1], 42.5, None, window, settings=settings)
    assert dlg.rb_block.isChecked() and dlg.rb_shown.isChecked() and dlg.fmt() == "txt"      # the defaults, nothing remembered yet
    dlg.format.setCurrentIndex(1)
    dlg.rb_file.setChecked(True)
    dlg.boxes[0].setChecked(False)
    dlg.boxes[1].setChecked(True)
    dlg.boxes[3].setChecked(True)                                     # a pick: channels 2 and 4
    dlg.decimal.setCurrentIndex(1)
    dlg.cb_header.setChecked(False)
    dlg.cb_comments.setChecked(False)
    dlg.cb_units.setChecked(False)
    dlg._accept()
    assert dlg.request is not None and dlg.request.channels == [1, 3]
    dlg.deleteLater()

    again = ExportDialog(f, tl, [0, 1, 2, 3], 42.5, None, window, settings=settings)
    assert again.fmt() == "csv" and again.rb_file.isChecked() and again.rb_pick.isChecked()
    assert again.channels() == [1, 3] and again.decimal.currentIndex() == 1
    assert not again.cb_header.isChecked() and not again.cb_comments.isChecked() and not again.cb_units.isChecked()
    again._accept()
    assert asked[-1].startswith(str(tmp_path / "out"))                # the folder exported to last time
    assert again.request.decimal == "," and again.request.header is False
    again.deleteLater()
    # an interval, or an explicit scope, still wins over the remembered scope
    sel = ExportDialog(f, tl, [0], None, (1.0, 2.0), window, settings=settings)
    assert sel.rb_sel.isChecked()
    sel.deleteLater()
    blk = ExportDialog(f, tl, [0], None, None, window, settings=settings, default_scope="block")
    assert blk.rb_block.isChecked()
    blk.deleteLater()
    rem = ExportDialog(f, tl, [0], 42.5, None, window, settings=settings)
    assert rem.rb_file.isChecked()                                    # nothing else asked for: the remembered scope, not "block"
    rem.deleteLater()
    # a channel pick that no longer fits the file is ignored
    settings.setValue("export/picked", [0, 99])
    odd = ExportDialog(f, tl, [0, 1], None, None, window, settings=settings)
    assert not odd.rb_pick.isChecked() and odd.channels() == [0, 1]
    odd.deleteLater()


# -- 9. Space plays an interval ------------------------------------------------------------------

try:
    import cv2  # noqa: F401
except Exception:                                     # pragma: no cover
    cv2 = None
needs_cv2 = pytest.mark.skipif(cv2 is None, reason="OpenCV is not installed")
from test_video import video_document  # noqa: E402,F401  (the fixture that writes the clip)


@needs_cv2
def test_space_plays_the_selected_interval_and_stops_at_its_end(qapp, window, video_document):
    window.open_file(video_document)
    st, v = window.stack, window.video
    v.set_rate(4.0)
    st.set_cursor(30.0)
    st.set_selection(5.0, 6.0)
    window.act_play.trigger()
    assert v.playing and st.selection == (5.0, 6.0)
    assert st.cursor == pytest.approx(5.0, abs=0.3)                   # from the interval's start, not the cursor at 30
    assert wait_until(qapp, lambda: not v.playing, timeout=10)
    assert st.selection == (5.0, 6.0)                                 # still selected
    assert st.cursor == pytest.approx(6.0, abs=0.01)                  # paused at its end
    # again: from the start of the interval
    window.act_play.trigger()
    assert v.playing and st.cursor < 5.6
    window.act_play.trigger()                                         # Space while playing pauses
    assert not v.playing and st.selection == (5.0, 6.0)
    # without an interval: from the cursor to the end of the block, as before
    st.clear_selection()
    st.set_cursor(39.0)
    window.act_play.trigger()
    assert wait_until(qapp, lambda: not v.playing, timeout=10)
    assert st.cursor == pytest.approx(39.999)


# -- 10. auto zoom and zoom to fit -----------------------------------------------------------------

def test_auto_zoom_fits_the_y_axes_after_every_move(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    assert window.act_auto_zoom.isCheckable() and window.act_zoom_fit.isEnabled()
    assert window.act_zoom_fit.text() == "Zoom to fit" and window.act_auto_zoom.text() == "Auto zoom"
    window.act_auto_zoom.setChecked(True)
    assert st.auto_zoom and not window.act_zoom_fit.isEnabled()      # meaningless while auto zoom is on
    assert window.settings.value("view/autoZoom", False, type=bool) is True

    def fitted(channel):
        lo, hi = st.plots[channel].vb.viewRange()[1]
        x0, x1 = st.view_range()
        r = st.plots[channel].visible_y_range()
        return r is not None and lo <= r[0] and r[1] <= hi and (hi - lo) < 1.5 * (r[1] - r[0]) + 1e-12

    st.set_range(10.0, 10.5)
    assert wait_until(qapp, lambda: all(p.level == "samples" for p in st.plots) and all(fitted(c) for c in range(4)), timeout=20)
    before = st.plots[1].vb.viewRange()[1]
    st.set_range(30.0, 30.6)                                          # a move elsewhere: fitted again, without a click
    assert wait_until(qapp, lambda: all(fitted(c) for c in range(4)) and st.plots[1].vb.viewRange()[1] != before, timeout=20)
    # switched off: a move leaves the y-axes alone
    window.act_auto_zoom.setChecked(False)
    assert not st.auto_zoom and window.act_zoom_fit.isEnabled()
    y = st.plots[1].vb.viewRange()[1]
    st.set_range(20.0, 20.6)
    pump(qapp, 0.4)
    assert st.plots[1].vb.viewRange()[1] == pytest.approx(y)
    st.zoom_to_fit()                                                  # the one-off fit still works
    assert st.plots[1].vb.viewRange()[1] != pytest.approx(y)


# -- 6. a comment or block picked from a list ------------------------------------------------------

def test_a_comment_or_block_from_the_list_centres_the_view_on_its_marker(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    st.set_range(0.0, 10.0)
    window.info.comments.cellClicked.emit(1, 0)                       # comment #2 at 16.005 s
    assert st.cursor == pytest.approx(16.005)
    x0, x1 = st.view_range()
    assert (x0 + x1) / 2 == pytest.approx(16.005) and x1 - x0 == pytest.approx(10.0)      # centred, zoom kept
    st.set_range(0.0, 10.0)
    window.info.comments.cellClicked.emit(2, 0)                       # 39.995 s
    x0, x1 = st.view_range()
    assert (x0 + x1) / 2 == pytest.approx(39.995)
    window.info.comments.cellClicked.emit(3, 0)                       # 42.5 s (block 2), ~ the end: the view stops at the edge
    x0, x1 = st.view_range()
    assert x0 <= 42.5 <= x1 and x1 == pytest.approx(45.0)
    st.set_range(20.0, 30.0)
    window.info.comments.cellClicked.emit(0, 0)                       # 0.01 s: the view stops at the start
    x0, x1 = st.view_range()
    assert x0 == pytest.approx(0.0) and x1 - x0 == pytest.approx(10.0) and x0 <= st.cursor <= x1
    # a block row: the cursor at its start, centred, zoom kept
    st.set_range(0.0, 10.0)
    window.info.blocks.cellClicked.emit(1, 0)
    assert st.cursor == pytest.approx(40.0)
    x0, x1 = st.view_range()
    assert x0 <= 40.0 <= x1 and x1 - x0 == pytest.approx(10.0) and x1 == pytest.approx(45.0)
    # a double-click fits the block across the full width
    window.info.blocks.cellDoubleClicked.emit(1, 0)
    assert st.view_range() == pytest.approx((40.0, 45.0))
    # the marker of a click in the middle: exactly centred
    window.stack.set_range(0.0, 10.0)
    st.center_on(22.0)
    assert st.view_range() == pytest.approx((17.0, 27.0))


# -- 3. icons and tooltips ------------------------------------------------------------------------------

def test_toolbar_buttons_are_icons_with_tooltips(qapp, window, synthetic):
    from PySide6.QtWidgets import QToolButton
    tb = window.toolbar
    assert tb.toolButtonStyle() == Qt.ToolButtonIconOnly
    actions = [a for a in tb.actions() if not a.isSeparator() and a.icon() is not None and tb.widgetForAction(a).__class__ is QToolButton]
    names = {a.iconText() for a in actions}
    assert {"Open", "Export", "Zoom in", "Zoom out", "Reset zoom", "Zoom to fit", "Auto zoom", "Show channels", "Clear selection"} <= names
    for a in actions:
        assert not a.icon().isNull(), a.text()
        assert a.toolTip() and a.toolTip() != a.text(), a.text()
        assert a.icon().pixmap(22, 22).width() >= 22
    assert "Ctrl+" in window.act_zoom_fit.toolTip() or "⌘" in window.act_zoom_fit.toolTip()
    assert window.act_open.iconText() == "Open"
    for a in actions:
        assert tb.widgetForAction(a).toolButtonStyle() == Qt.ToolButtonIconOnly


def test_every_icon_is_a_valid_coloured_drawing():
    for name in icons.ICONS:
        root = ET.fromstring(icons.icon_svg(name, "#123456"))
        assert root.tag.endswith("svg") and root.get("stroke") == "#123456"
        assert len(list(root)) == len(icons.ICONS[name]) >= 1
    for name in ("open", "export", "zoom_in", "zoom_out", "zoom_all", "zoom_fit", "auto_zoom", "channels", "clear_selection"):
        assert name in icons.ICONS


# -- 4. the start dialog ---------------------------------------------------------------------------------

def test_the_start_dialog_lists_the_recent_files(qapp, window, synthetic, tmp_path, monkeypatch):
    gone = str(tmp_path / "gone.adicht")
    entries = recent_entries([synthetic["path"], gone])
    assert entries[0][2] is True and entries[1] == ("gone.adicht", str(tmp_path), False)
    dlg = StartupDialog([synthetic["path"], gone], window)
    assert dlg.list.count() == 2 and dlg.list.currentRow() == 0
    assert not dlg.list.item(1).flags() & Qt.ItemIsEnabled and "(not found)" in dlg.list.item(1).text()
    dlg._on_activated(dlg.list.item(1))                               # a missing file cannot be chosen
    assert dlg.path is None and dlg.result() == 0
    dlg._on_open_selected()
    assert dlg.path == synthetic["path"] and dlg.result() == 1
    dlg.deleteLater()
    b = StartupDialog([synthetic["path"]], window)
    b.btn_browse.click()
    assert b.browse and b.path is None
    b.deleteLater()

    # the window: with recent files the start dialog is what opens, a pick opens the file
    window._remember(synthetic["path"])
    shown = []

    def fake_exec(self):
        shown.append(self)
        self.path = synthetic["path"]
        return StartupDialog.Accepted

    monkeypatch.setattr(StartupDialog, "exec", fake_exec)
    window.open_startup()
    assert shown and window.file is not None and window.file.path == synthetic["path"]
    # "Open a file…" goes on to the file dialog
    asked = []
    monkeypatch.setattr(window, "open_dialog", lambda: asked.append(1))

    def fake_browse(self):
        self.browse = True
        return StartupDialog.Accepted

    monkeypatch.setattr(StartupDialog, "exec", fake_browse)
    window.open_startup()
    assert asked == [1]
    # no recent files: straight to the file dialog
    window.settings.remove("recent")
    asked.clear()
    window.open_startup()
    assert asked == [1]


# -- 5. the shared axis width -------------------------------------------------------------------------------

def test_the_left_axes_share_a_width_that_fits_their_labels(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    pump(qapp, 0.2)
    widths = {p.axis_width for p in st.plots}
    # one width for all; how wide follows the platform's fonts (116 px on the Windows runner, so
    # not below the old fixed 90 px everywhere): what is checked is that it fits the labels
    assert widths == {st.axis_width}
    need = max(st.plots[c].needed_axis_width() for c in st.visible_channels)
    assert st.axis_width >= need > 0 and st.axis_width - need <= 8             # no loose gap beyond the step
    # the strip and the overlays follow the same width
    assert st.strip.getPlotItem().getContentsMargins()[0] == st.axis_width
    assert st.plots[0].item.getAxis("left").width() == pytest.approx(st.axis_width, abs=1)
    # fewer channels (shorter labels) may narrow it, and the plots still line up
    st.set_visible_channels([0, 1])
    pump(qapp, 0.2)
    assert len({p.axis_width for p in st.plots}) == 1 and st.axis_width >= max(st.plots[c].needed_axis_width() for c in (0, 1))
    lefts = {round(st.plots[c].vb.sceneBoundingRect().left(), 1) for c in (0, 1)}
    assert len(lefts) == 1


# -- regressions found in review ---------------------------------------------------------------------

def test_scroll_actions_move_the_view_half_a_screen(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    st.set_range(10.0, 20.0)
    window.act_scroll_right.trigger()
    assert st.view_range() == pytest.approx((15.0, 25.0))
    window.act_scroll_left.trigger()
    window.act_scroll_left.trigger()
    assert st.view_range() == pytest.approx((5.0, 15.0))


def test_a_saved_drag_mode_reaches_the_stack_at_start(qapp, synthetic):
    from adichtion.ui.main_window import MainWindow
    first = MainWindow()
    first.act_drag_selects.setChecked(False)
    first.close()
    second = MainWindow()
    try:
        assert not second.act_drag_selects.isChecked() and second.stack.drag_selects is False
    finally:
        second.act_drag_selects.setChecked(True)
        second.close()


def test_a_drag_keeps_its_role_when_alt_is_pressed_midway(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    plot = st.plots[1]
    a, b, c = _px(plot, 10.0), _px(plot, 15.0), _px(plot, 20.0)
    _send(plot, QEvent.MouseButtonPress, a, Qt.LeftButton, Qt.LeftButton)
    _send(plot, QEvent.MouseMove, b, Qt.NoButton, Qt.LeftButton)
    _send(plot, QEvent.MouseMove, c, Qt.NoButton, Qt.LeftButton, Qt.AltModifier)
    _send(plot, QEvent.MouseButtonRelease, c, Qt.LeftButton, Qt.NoButton, Qt.AltModifier)
    assert st.selection is not None and st._drag_anchor is None
    drag(plot, 30.0, 35.0)
    assert st.cursor == pytest.approx(30.0, abs=0.2)


@needs_cv2
def test_moving_the_cursor_during_interval_playback_drops_the_interval_stop(qapp, window, video_document):
    window.open_file(video_document)
    st, v = window.stack, window.video
    v.set_rate(4.0)
    st.set_selection(5.0, 6.0)
    window.act_play.trigger()
    st.set_cursor(20.0)                                # a click elsewhere while playing
    pump(qapp, 0.6)
    assert v.playing and st.cursor > 20.0
    v.pause()


def test_an_abandoned_drag_does_not_leave_a_stale_anchor(qapp, window, synthetic):
    window.open_file(synthetic["path"])
    st = window.stack
    plot = st.plots[1]
    a, b = _px(plot, 10.0), _px(plot, 20.0)
    _send(plot, QEvent.MouseButtonPress, a, Qt.LeftButton, Qt.LeftButton)      # no release ever arrives
    for k in range(1, 6):
        _send(plot, QEvent.MouseMove, QPoint(a.x() + (b.x() - a.x()) * k // 5, a.y()), Qt.NoButton, Qt.LeftButton)
    drag(plot, 30.0, 35.0)
    assert st.cursor == pytest.approx(30.0, abs=0.2)


def test_no_context_menu_without_a_file(qapp, window):
    window._on_context_menu(QPoint(10, 10))
    assert getattr(window, "_context_menu", None) is None
