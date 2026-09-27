"""GUI entry point: ``adichtion-app`` (or ``python -m adichtion.ui``).

Builds the themed main window, shows it maximised and opens the file given on
the command line, else asks for one.
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


def main(argv: Optional[list[str]] = None) -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6.QtCore import QLoggingCategory, QTimer
    from PySide6.QtWidgets import QApplication

    QLoggingCategory.setFilterRules("qt.qpa.fonts.warning=false")
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
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
    path = startup_path(argv)

    def _begin():
        if path is not None:
            win.open_file(path)
        else:
            win.open_dialog()

    QTimer.singleShot(0, _begin)
    return app.exec()


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
