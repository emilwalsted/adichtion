"""The app's entry point (``adichtion`` with no subcommand, ``python -m adichtion``,
the packaged app).

Builds the themed main window, shows it maximised and opens the file given on
the command line, else asks for one.  On macOS a document double-clicked in
the Finder (or dropped on the Dock icon) arrives as a ``QEvent.FileOpen``,
before or after the window exists; both are handled.
"""
from __future__ import annotations

import os
import sys
import traceback
from typing import Optional


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
    from . import theme
    theme.apply_theme(app)
    try:
        from .logo import app_icon
        icon = app_icon()
        if icon is not None:
            app.setWindowIcon(icon)
    except Exception:                                # noqa: BLE001 - cosmetic
        pass
    try:
        from .main_window import MainWindow
        win = MainWindow()
    except Exception:                                # noqa: BLE001
        _fatal(traceback.format_exc())
        return 1
    win.showMaximized()
    path = startup_path(argv) or getattr(app, "opened_path", None)
    if hasattr(app, "on_file_open"):
        app.on_file_open = win.open_file             # a later FileOpen (app already running) opens live

    def _begin():
        if path is not None:
            win.open_file(path)
        else:
            win.open_dialog()

    QTimer.singleShot(0, _begin)
    return app.exec()


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
