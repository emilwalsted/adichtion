"""The app's entry point (``adichtion`` with no subcommand, ``python -m adichtion``,
the packaged app).

Shows the splash first, builds the themed main window behind it, then reveals the
window maximised (once the splash has been up for a short minimum) and opens the
file given on the command line, else shows the start dialog (recent files, or the file dialog
while there are none).  On macOS a document
double-clicked in the Finder (or dropped on the Dock icon) arrives as a
``QEvent.FileOpen``, before or after the window exists; both are handled.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Optional

#: Floor (ms) the splash stays up before the window is revealed, counted from when the
#: splash was shown: building the window already takes most of it, this only tops it up.
MIN_SPLASH_MS = 1200


def reveal_delay_ms(elapsed_ms: int, floor_ms: int = MIN_SPLASH_MS) -> int:
    """How much longer the splash stays up when building the window took ``elapsed_ms``:
    nothing once the floor is spent, otherwise the remainder. Pure, for the tests."""
    return max(0, floor_ms - elapsed_ms)


def application_icon_choice(platform: str, packaged: bool) -> Optional[str]:
    """Which application icon ``main()`` sets (``QApplication.setWindowIcon``). Pure, for
    the tests.

    - ``None`` in the packaged macOS app: the bundle's ``adichtion.icns`` stays the Dock
      and app-switcher icon. Setting one at run time replaces it with an NSImage Qt builds
      from the QIcon; in the Qt the app ships (PySide6 6.9.3, like all of Qt 6.9.1-6.10.0)
      that image takes its size from the icon's first representation, ``app_icon()``'s
      16 px bare "a", and the running app showed a blurry "a" in the Dock
      (docs/beslutninger.md 2026-09-28 (5)).
    - ``"dock"`` on macOS from source: ``logo.dock_icon()``, the full logo only. There is
      no bundle icon of ours; without an application icon the Dock shows the Python
      interpreter's, or, under a non-bundled Python, Qt promotes the focused window's
      icon (``MainWindow``'s ``app_icon()``, the 16 px "a" first) to the Dock.
    - ``"app"`` on Windows and Linux: ``logo.app_icon()`` with every size (taskbar,
      window list), as before.
    """
    if platform == "darwin":
        return None if packaged else "dock"
    return "app"


def _running_as_packaged_mac_app(app) -> bool:
    """``macbundle.is_packaged_app`` on the bundle AppKit uses and on ``sys.executable``."""
    if sys.platform != "darwin":
        return False
    try:
        from .macbundle import is_packaged_app
        return is_packaged_app((app.applicationFilePath(), sys.executable))
    except Exception:                                # noqa: BLE001 - never block start-up
        return False


def _set_application_icon(app) -> Optional[str]:
    """Set the application icon ``application_icon_choice`` picks for this platform (none
    in the packaged macOS app) and return the choice. Cosmetic: never raises."""
    try:
        choice = application_icon_choice(sys.platform, _running_as_packaged_mac_app(app))
        if choice is not None:
            from .logo import app_icon, dock_icon
            icon = dock_icon() if choice == "dock" else app_icon()
            if icon is not None:
                app.setWindowIcon(icon)
        return choice
    except Exception:                                # noqa: BLE001 - cosmetic
        return None


def _fatal(tb: str) -> None:
    try:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("adichtion could not start")
        box.setText(tb.strip().splitlines()[-1] if tb.strip() else "unknown error")
        box.setDetailedText(tb)
        box.exec()
    except Exception:                                # pragma: no cover - last resort
        print(tb, file=sys.stderr)


def startup_path(argv: list[str]) -> Optional[str]:
    """The ``.adicht`` named on the command line, if any."""
    for a in argv[1:]:
        if a.lower().endswith(".adicht") and os.path.exists(a):
            return a
    return None


def _application_class():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    class AdichtionApplication(QApplication):
        """A QApplication that keeps the document macOS asks it to open."""

        def __init__(self, argv):
            super().__init__(argv)
            self.opened_path: Optional[str] = None      # a FileOpen delivered before the window existed
            self.on_file_open = None                    # set by main() once the window can take it

        def event(self, ev):
            if ev.type() == QEvent.FileOpen:
                path = ev.file()
                if path:
                    if self.on_file_open is not None:
                        self.on_file_open(path)
                    else:
                        self.opened_path = path
                return True
            return super().event(ev)

    return AdichtionApplication


def main(argv: Optional[list[str]] = None) -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6.QtCore import QLoggingCategory, QTimer
    from PySide6.QtWidgets import QApplication

    QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false")
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or _application_class()(argv)
    app.processEvents()                              # a cold double-click launch queues its FileOpen early
    app.setOrganizationName("adichtion")
    app.setApplicationName("adichtion")

    # The splash is the first thing on screen (a self-contained pixmap: it needs neither
    # the theme nor the icon); the window is built behind it and revealed afterwards.
    t0 = time.monotonic()
    splash = None
    try:
        from .splash import make_splash
        splash = make_splash(app)                    # None without Qt SVG
    except Exception:                                # noqa: BLE001 - cosmetic
        splash = None
    if splash is not None:
        splash.show()
        splash.raise_()
        app.processEvents()                          # paint it before the window build

    from . import theme
    theme.apply_theme(app)
    _set_application_icon(app)
    try:
        from .main_window import MainWindow
        win = MainWindow()
    except Exception:                                # noqa: BLE001
        if splash is not None:
            splash.close()
        _fatal(traceback.format_exc())
        return 1

    def _reveal():
        # the window first, then the (modal) splash goes, so the window keeps the focus
        try:
            win.showMaximized()
        finally:
            if splash is not None:
                splash.finish(win)
        win.raise_()
        win.activateWindow()
        # A FileOpen delivered while the splash was up sits in opened_path; from here on a
        # later one (app already running) opens live. No event runs between the two lines.
        path = startup_path(argv) or getattr(app, "opened_path", None)
        if hasattr(app, "on_file_open"):
            app.on_file_open = win.open_file
        if path is not None:
            win.open_file(path)
        else:
            win.open_startup()

    delay = reveal_delay_ms(int((time.monotonic() - t0) * 1000)) if splash is not None else 0
    QTimer.singleShot(delay, _reveal)
    return app.exec()


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
