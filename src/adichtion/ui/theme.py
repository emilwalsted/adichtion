"""Visual theme of the adichtion app, ported from RespMech's ``ui/theme.py``.

A Fusion-based ``QPalette`` plus one style sheet, and matching colours for the
pyqtgraph channel plots: white cards on a soft neutral canvas, one restrained
steel-blue accent, quiet status colours.  The dark variant follows the OS
colour scheme (or ``ADICHTION_THEME=dark|light``).

Import-safe without Qt: every Qt/pyqtgraph import is lazy and guarded, so the
colour constants can be used by headless code, and nothing here raises.

Public API::

    apply_theme(app) -> "light" | "dark"
    plot_palette()   -> dict of plot colours for the active theme
    active_theme()   -> dict of widget colour tokens
    is_dark()
"""
from __future__ import annotations

import os
from string import Template

ACCENT = "#2C6E9B"   # restrained clinical steel-blue, the single accent
OK = "#1F7A4D"
WARN = "#B7791F"
ERROR = "#B4322A"
MUTED = "#5C6B7A"

#: Trace colours for the channel plots (colour-blind friendly cycle, as in RespMech).
CYCLE = [ACCENT, "#B4322A", "#1F7A4D", "#B7791F", "#7D5BA6", "#0E7C7B", "#B4507A", "#5C6B7A"]

#: Fixed left-axis width (px) of the stacked channel plots, so their x-axes line up.
PLOT_AXIS_WIDTH = 72
#: Floor (px) of one channel row; below this a trace is a line without a shape.
PLOT_ROW_MIN_HEIGHT = 48

_PLOT_LIGHT = {
    "bg": "#FCFDFE", "bg_calc": "#F6F2FB", "fg": "#33404D", "grid_alpha": 0.15,
    "cycle": [(44, 110, 155), (180, 50, 42), (31, 122, 77), (183, 121, 31),
              (125, 91, 166), (14, 124, 123), (180, 80, 122), (92, 107, 122)],
    "cursor": (44, 110, 155), "selection": (44, 110, 155, 36), "selection_edge": (44, 110, 155, 160),
    "block_line": (150, 165, 180), "comment_line": (183, 121, 31, 150), "comment_flag": (183, 121, 31),
    "zero_line": (150, 165, 180), "overlay_bg": (255, 255, 255, 200), "calc_text": (125, 91, 166),
}
_PLOT_DARK = {
    "bg": "#14181D", "bg_calc": "#1B1824", "fg": "#B4BFCB", "grid_alpha": 0.18,
    "cycle": [(96, 172, 226), (232, 118, 106), (94, 200, 142), (226, 178, 92),
              (180, 152, 226), (74, 200, 198), (230, 140, 182), (150, 166, 182)],
    "cursor": (110, 172, 224), "selection": (110, 172, 224, 46), "selection_edge": (110, 172, 224, 170),
    "block_line": (98, 112, 128), "comment_line": (226, 178, 92, 150), "comment_flag": (226, 178, 92),
    "zero_line": (98, 112, 128), "overlay_bg": (20, 24, 29, 210), "calc_text": (180, 152, 226),
}

_LIGHT = {
    "window": "#EEF1F5", "surface": "#FFFFFF", "surface_alt": "#F5F8FA",
    "base": "#FFFFFF", "text": "#1F2933", "text_muted": "#5C6B7A",
    "border": "#D8DEE6", "border_strong": "#C4CCD6", "grid": "#E6EBF0",
    "header_bg": "#F1F4F8",
    "accent": "#2C6E9B", "accent_hover": "#2A6591", "accent_pressed": "#24597F",
    "accent_fg": "#FFFFFF", "accent_soft": "#E6F0F7",
    "disabled_bg": "#F0F2F5", "disabled_fg": "#7A8593",
    "st_info_fg": "#215C86", "st_info_bg": "#E7F0F7", "st_info_bd": "#C4DBEC",
    "st_ok_fg": "#1F6B48", "st_ok_bg": "#E6F3EC", "st_ok_bd": "#BFE0CD",
    "st_warn_fg": "#8A5A12", "st_warn_bg": "#FBF1DD", "st_warn_bd": "#EBD6A8",
    "st_error_fg": "#A02B22", "st_error_bg": "#FBE9E7", "st_error_bd": "#F0C4BF",
    "calc_fg": "#5E3F8C", "calc_bg": "#F1EBF8",
}
_DARK = {
    "window": "#171B21", "surface": "#21272F", "surface_alt": "#1D232B",
    "base": "#1B2128", "text": "#E4E9EF", "text_muted": "#97A3B0",
    "border": "#2F3843", "border_strong": "#3B4550", "grid": "#2A323C",
    "header_bg": "#232B34",
    "accent": "#4B9CD3", "accent_hover": "#5CA9DD", "accent_pressed": "#3E8AC0",
    "accent_fg": "#0C1116", "accent_soft": "#263643",
    "disabled_bg": "#1B2028", "disabled_fg": "#79838F",
    "st_info_fg": "#8FC4E8", "st_info_bg": "#1E2E3A", "st_info_bd": "#2C4457",
    "st_ok_fg": "#7FCBA4", "st_ok_bg": "#172A20", "st_ok_bd": "#2A4636",
    "st_warn_fg": "#E0B357", "st_warn_bg": "#2C2413", "st_warn_bd": "#4A3D1D",
    "st_error_fg": "#E8897F", "st_error_bg": "#2E1B19", "st_error_bd": "#4C2A26",
    "calc_fg": "#C4AEE8", "calc_bg": "#2A2438",
}

_IS_DARK = False
_ACTIVE = dict(_LIGHT)


def is_dark() -> bool:
    return bool(_IS_DARK)


def plot_palette() -> dict:
    """Plot colours of the active theme (a copy; light before any theme is applied)."""
    return dict(_PLOT_DARK if _IS_DARK else _PLOT_LIGHT)


def active_theme() -> dict:
    return dict(_ACTIVE)


_ICON_PATHS: dict = {}


def _icon_path(kind: str, colour: str) -> str:
    """Small PNGs the style sheet needs as real files (a check mark, spin-box arrows,
    the resize grip); generated once per (kind, colour).  "" when they cannot be made."""
    key = (kind, colour)
    if key in _ICON_PATHS:
        return _ICON_PATHS[key]
    path = ""
    try:
        import tempfile
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPolygonF
        s = 28
        img = QImage(s, s, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        if kind == "check":
            pen = QPen(QColor(colour)); pen.setWidth(3); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(6, 14), QPointF(12, 21), QPointF(22, 8)])
        elif kind in ("up", "down"):
            p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(colour)))
            m, top, bot = 5.0, 8.0, 20.0
            pts = ([QPointF(m, bot), QPointF(s - m, bot), QPointF(s / 2, top)] if kind == "up"
                   else [QPointF(m, top), QPointF(s - m, top), QPointF(s / 2, bot)])
            p.drawPolygon(QPolygonF(pts))
        elif kind == "grip":
            pen = QPen(QColor(colour)); pen.setWidth(2); pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for off in (7, 13, 19):
                p.drawLine(QPointF(s - 3, s - off - 3), QPointF(s - off - 3, s - 3))
        p.end()
        path = os.path.join(tempfile.gettempdir(), f"adichtion_qss_{kind}_{colour.lstrip('#')}.png")
        img.save(path)
        path = path.replace("\\", "/")
    except Exception:                                   # pragma: no cover - cosmetic
        path = ""
    _ICON_PATHS[key] = path
    return path


_QSS = Template(r"""
QWidget { background: transparent; color: $text; }
QMainWindow, QDialog { background-color: $window; }

QToolTip {
    background-color: $surface; color: $text; border: 1px solid $border;
    padding: 6px 9px; border-radius: 6px;
}

QGroupBox {
    background-color: $surface; border: 1px solid $border; border-radius: 10px;
    margin-top: 20px; padding: 16px 16px 14px 16px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 14px; top: 2px;
    padding: 0 6px; color: $text_muted; letter-spacing: 1px;
}

QPushButton {
    background-color: $surface; color: $text; border: 1px solid $border_strong;
    border-radius: 8px; padding: 7px 16px; min-height: 18px;
}
QPushButton:hover { background-color: $surface_alt; border-color: $accent; }
QPushButton:pressed { background-color: $accent_soft; }
QPushButton:focus { outline: none; border-color: $accent; }
QPushButton:default { border-color: $accent; }
QPushButton:disabled { color: $disabled_fg; background-color: $disabled_bg; border-color: $border; }
QPushButton#primary, QPushButton[primary="true"] {
    background-color: $accent; color: $accent_fg; border: 1px solid $accent; font-weight: 600;
}
QPushButton#primary:hover, QPushButton[primary="true"]:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:pressed, QPushButton[primary="true"]:pressed { background-color: $accent_pressed; border-color: $accent_pressed; }
QPushButton#primary:disabled, QPushButton[primary="true"]:disabled { background-color: $disabled_bg; color: $disabled_fg; border-color: $border; }
QPushButton[compact="true"] { padding: 4px 10px; min-width: 0; font-weight: 600; }

/* the main toolbar: flat, quiet buttons under the menu bar */
QToolBar {
    background-color: $surface; border: none; border-bottom: 1px solid $border;
    padding: 4px 8px; spacing: 4px;
}
QToolBar::separator { width: 1px; background: $border; margin: 4px 6px; }
QToolButton {
    background: transparent; color: $text; border: 1px solid transparent;
    border-radius: 6px; padding: 4px 9px;
}
QToolButton:hover { background-color: $surface_alt; border-color: $border_strong; }
QToolButton:pressed, QToolButton:checked { background-color: $accent_soft; border-color: $accent; color: $accent; }
QToolButton:disabled { color: $disabled_fg; }
QToolButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; right: 4px; }
QToolButton#plotBtn {
    padding: 0px; border-radius: 4px; font-weight: 600; background-color: $surface; border: 1px solid $border;
}
QToolButton#plotBtn:hover { border-color: $accent; color: $accent; }
QMenuBar { background-color: $surface; border-bottom: 1px solid $border; padding: 2px 6px; }
QMenuBar::item { padding: 5px 10px; border-radius: 5px; background: transparent; }
QMenuBar::item:selected { background-color: $accent_soft; color: $accent; }

QTabWidget::pane { border: none; border-top: 1px solid $border; top: -1px; background: transparent; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab {
    background: transparent; color: $text_muted; padding: 9px 18px; margin: 0 2px;
    border: none; border-bottom: 2px solid transparent; min-width: 40px;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $accent; border-bottom: 2px solid $accent; font-weight: 600; }
QTabBar:focus { outline: none; }

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QAbstractSpinBox {
    background-color: $base; color: $text; border: 1px solid $border; border-radius: 7px;
    padding: 5px 8px; min-height: 18px;
    selection-background-color: $accent; selection-color: $accent_fg;
}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover { border-color: $border_strong; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QAbstractSpinBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {
    background-color: $disabled_bg; color: $disabled_fg; border-color: $border;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { subcontrol-origin: border; width: 16px; border: none; background: transparent; }
QAbstractSpinBox::up-button   { subcontrol-position: top right;    margin: 2px 2px 0 0; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin: 0 2px 2px 0; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: $accent_soft; border-radius: 3px; }
QAbstractSpinBox::up-arrow   { image: url("$arrow_up");   width: 9px; height: 9px; }
QAbstractSpinBox::down-arrow { image: url("$arrow_down"); width: 9px; height: 9px; }
QAbstractSpinBox::up-arrow:disabled,   QAbstractSpinBox::up-arrow:off   { image: url("$arrow_up_dim"); }
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off { image: url("$arrow_down_dim"); }
QComboBox QAbstractItemView {
    background-color: $surface; color: $text; border: 1px solid $border;
    selection-background-color: $accent_soft; selection-color: $text; outline: none; padding: 2px;
}

QCheckBox, QRadioButton { spacing: 8px; background: transparent; color: $text; }
QCheckBox:disabled, QRadioButton:disabled { color: $disabled_fg; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid $border_strong; background: $base; }
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border: 1px solid $accent; }
QCheckBox::indicator:checked { border: 1px solid $accent; background: $accent; image: url("$check_icon"); }
QRadioButton::indicator:checked { border: 1px solid $accent; background: $accent; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { border: 1px solid $border; background: $disabled_bg; }
QCheckBox:focus, QRadioButton:focus { outline: none; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { width: 14px; height: 14px; border: 2px solid $accent; }

QTableView, QTableWidget, QTreeView, QListView {
    background-color: $surface; alternate-background-color: $surface_alt; color: $text;
    gridline-color: $grid; border: 1px solid $border; border-radius: 8px;
    selection-background-color: $accent_soft; selection-color: $text;
    qproperty-alternatingRowColors: true;
}
QTableView::item, QTableWidget::item { padding: 3px 6px; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background-color: $header_bg; color: $text_muted; padding: 5px 8px; border: none;
    border-right: 1px solid $grid; border-bottom: 1px solid $border; font-weight: 600;
}
QHeaderView::section:last { border-right: none; }
QTableCornerButton::section { background-color: $header_bg; border: none; border-bottom: 1px solid $border; border-right: 1px solid $grid; }

QProgressBar { background-color: $surface_alt; border: 1px solid $border; border-radius: 7px; text-align: center; color: $text; min-height: 18px; }
QProgressBar::chunk { background-color: $accent; border-radius: 5px; margin: 2px; }

QSplitter::handle { background-color: $border; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter::handle:hover { background-color: $accent; }
/* the channel stack's handles are grabbable rows, not hairlines */
QSplitter#channelStack::handle:vertical { height: 5px; background-color: $window; }
QSplitter#channelStack::handle:vertical:hover { background-color: $accent_soft; }

QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 6px; min-height: 28px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 6px; min-width: 28px; margin: 2px; }
QScrollBar::handle:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu { background-color: $surface; color: $text; border: 1px solid $border; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 5px; }
QMenu::item:selected { background-color: $accent_soft; color: $text; }
QMenu::item:disabled { color: $disabled_fg; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }

QLabel { background: transparent; color: $text; }
QLabel[role="heading"] { font-size: 15px; font-weight: 600; color: $text; }
QLabel[role="panelTitle"] { color: $text_muted; font-weight: 600; letter-spacing: 1px; }
QLabel[status="muted"] { color: $text_muted; }
QLabel:disabled, QLabel[status="muted"]:disabled { color: $disabled_fg; }
QLabel[banner="true"] { border-style: solid; border-width: 1px; border-color: transparent; border-radius: 6px; padding: 5px 10px; }
QLabel[status="info"] { color: $st_info_fg; background-color: $st_info_bg; border-color: $st_info_bd; }
QLabel[status="ok"] { color: $st_ok_fg; background-color: $st_ok_bg; border-color: $st_ok_bd; }
QLabel[status="warn"] { color: $st_warn_fg; background-color: $st_warn_bg; border-color: $st_warn_bd; }
QLabel[status="error"] { color: $st_error_fg; background-color: $st_error_bg; border-color: $st_error_bd; }
QLabel[status="calc"] { color: $calc_fg; background-color: $calc_bg; border-color: $calc_bg; }

QFrame#appHeader { background-color: $surface; border: none; border-bottom: 1px solid $border; }
QLabel#appTitle { font-size: 17px; font-weight: 700; color: $accent; }
QLabel#appSubtitle { color: $text_muted; font-size: 11px; }
QFrame#panel { background-color: $surface; border: 1px solid $border; border-radius: 10px; }
QStatusBar { background-color: $surface; border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QSizeGrip { image: url("$grip_icon"); width: 13px; height: 13px; background: transparent; }
""")


def _prefers_dark(app) -> bool:
    env = os.environ.get("ADICHTION_THEME", "").strip().lower()
    if env in ("dark", "light"):
        return env == "dark"
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def _build_palette(t: dict):
    from PySide6.QtGui import QColor, QPalette

    def c(key):
        return QColor(t[key])

    p = QPalette()
    p.setColor(QPalette.Window, c("window"))
    p.setColor(QPalette.WindowText, c("text"))
    p.setColor(QPalette.Base, c("base"))
    p.setColor(QPalette.AlternateBase, c("surface_alt"))
    p.setColor(QPalette.ToolTipBase, c("surface"))
    p.setColor(QPalette.ToolTipText, c("text"))
    p.setColor(QPalette.Text, c("text"))
    p.setColor(QPalette.Button, c("surface"))
    p.setColor(QPalette.ButtonText, c("text"))
    p.setColor(QPalette.BrightText, c("st_error_fg"))
    p.setColor(QPalette.Highlight, c("accent"))
    p.setColor(QPalette.HighlightedText, c("accent_fg"))
    p.setColor(QPalette.Link, c("accent"))
    p.setColor(QPalette.LinkVisited, c("accent_pressed"))
    try:
        p.setColor(QPalette.PlaceholderText, c("text_muted"))
    except Exception:
        pass
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("disabled_fg"))
    p.setColor(QPalette.Disabled, QPalette.Highlight, c("disabled_bg"))
    p.setColor(QPalette.Disabled, QPalette.HighlightedText, c("disabled_fg"))
    return p


def apply_theme(app) -> str:
    """Fusion + palette + style sheet + base font + pyqtgraph defaults.  Never raises."""
    global _ACTIVE, _IS_DARK
    try:
        dark = _prefers_dark(app)
        _IS_DARK = dark
        tokens = _DARK if dark else _LIGHT
        _ACTIVE = dict(tokens)
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
        try:
            f = app.font()
            if f.pointSizeF() > 0:
                f.setPointSizeF(max(f.pointSizeF(), 10.5))
            else:
                f.setPixelSize(14)
            app.setFont(f)
        except Exception:
            pass
        try:
            app.setPalette(_build_palette(tokens))
        except Exception:
            pass
        try:
            app.setStyleSheet(_QSS.safe_substitute(dict(
                tokens,
                check_icon=_icon_path("check", tokens["accent_fg"]),
                grip_icon=_icon_path("grip", tokens["text_muted"]),
                arrow_up=_icon_path("up", tokens["text"]),
                arrow_down=_icon_path("down", tokens["text"]),
                arrow_up_dim=_icon_path("up", tokens["disabled_fg"]),
                arrow_down_dim=_icon_path("down", tokens["disabled_fg"]))))
        except Exception:
            pass
        style_pyqtgraph()
        return "dark" if dark else "light"
    except Exception:
        return "light"


def style_pyqtgraph() -> None:
    try:
        import pyqtgraph as pg
    except Exception:
        return
    pal = plot_palette()
    try:
        pg.setConfigOptions(antialias=False, background=pal["bg"], foreground=pal["fg"])
    except Exception:
        pass


def repolish(widget) -> None:
    """Re-apply the style after a dynamic property changed.  Never raises."""
    try:
        st = widget.style()
        if st is not None:
            st.unpolish(widget)
            st.polish(widget)
        widget.update()
    except Exception:
        pass
