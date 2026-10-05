"""Applying the theme: once per application and theme.  A style, a palette or a style sheet set on the application
polishes every widget alive, also when nothing changed; ``main()`` and ``bench --pan`` apply the theme to an application
that may already have it (seconds in a process with hundreds of widgets, which timed a test out on the Windows runners)."""
from PySide6.QtGui import QFont

from adichtion.ui import theme


class _App:
    """What ``apply_theme`` asks of an application, recorded."""

    def __init__(self):
        self.calls, self.props, self.sheet = [], {}, ""

    def font(self):
        return QFont()

    def setFont(self, f):
        self.calls.append("font")

    def setStyle(self, name):
        self.calls.append(f"style:{name}")

    def setPalette(self, p):
        self.calls.append("palette")

    def setStyleSheet(self, sheet):
        self.calls.append("sheet")
        self.sheet = sheet

    def styleSheet(self):
        return self.sheet

    def property(self, name):
        return self.props.get(name)

    def setProperty(self, name, value):
        self.props[name] = value


def test_the_theme_is_applied_once_per_application_and_theme(qapp, monkeypatch):
    app = _App()
    try:
        monkeypatch.setenv("ADICHTION_THEME", "light")
        assert theme.apply_theme(app) == "light"
        assert app.calls == ["style:Fusion", "font", "palette", "sheet"] and app.sheet
        assert theme.apply_theme(app) == "light"
        assert len(app.calls) == 4                                   # the same theme again: the application is left alone
        monkeypatch.setenv("ADICHTION_THEME", "dark")
        light = app.sheet
        assert theme.apply_theme(app) == "dark" and theme.is_dark()
        assert len(app.calls) == 8 and app.sheet != light            # another theme is applied ...
        assert theme.apply_theme(app) == "dark" and len(app.calls) == 8      # ... once
        app.setStyleSheet("")                                        # somebody replaced the style sheet: the mark is void
        assert theme.apply_theme(app) == "dark" and app.sheet and app.calls[-4:] == ["style:Fusion", "font", "palette", "sheet"]
        other = _App()                                               # the mark belongs to the application, not the module
        assert theme.apply_theme(other) == "dark" and len(other.calls) == 4
    finally:
        monkeypatch.setenv("ADICHTION_THEME", "light")
        theme.apply_theme(qapp)                                      # the plots' palette and pyqtgraph's defaults back


def test_applying_the_theme_again_does_not_touch_the_running_application(qapp, monkeypatch):
    theme.apply_theme(qapp)
    sheet = qapp.styleSheet()
    assert sheet and qapp.property(theme._APPLIED_PROPERTY) in ("light", "dark")
    calls = []
    monkeypatch.setattr(theme, "_apply_to_application", lambda *a: calls.append(a))
    assert theme.apply_theme(qapp) in ("light", "dark")
    assert calls == [] and qapp.styleSheet() == sheet
