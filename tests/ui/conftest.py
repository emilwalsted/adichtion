"""Fixtures for the viewer tests: a headless QApplication and helpers to pump its event loop."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))          # `from helpers import ...` in the tests here

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("ADICHTION_THEME", "light")

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="session", autouse=True)
def _isolated_settings(tmp_path_factory):
    """The tests never read or write the user's real QSettings (recent files, export choices, ...)."""
    home = tmp_path_factory.mktemp("config")
    os.environ["XDG_CONFIG_HOME"] = str(home)
    os.environ["HOME"] = str(home)
    from PySide6.QtCore import QSettings
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        QSettings.setPath(fmt, QSettings.UserScope, str(home))
    yield


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from adichtion.ui import theme
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)
    yield app


@pytest.fixture
def window(qapp):
    """A main window that is closed (threads joined) after the test."""
    from adichtion.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()
