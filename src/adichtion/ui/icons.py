"""Line icons for the toolbar, drawn as small SVGs in the theme's colours.

One 24 x 24 stroke drawing per name (``ICONS``); ``toolbar_icon(name)`` renders it for
the normal, disabled and checked states of the active theme, at a few sizes and for
retina screens, so the toolbar buttons show a picture and the tooltip carries the words.
The SVG builder is Qt-free (a test checks every drawing parses and is coloured).
"""
from __future__ import annotations

from . import theme

_STROKE = 'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'

#: The drawings: SVG path data ("d" strings), one entry per stroked path.
ICONS: dict[str, list[str]] = {
    # a folder
    "open": ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
    # a tray with an arrow leaving it
    "export": ["M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4", "M12 15V3", "M7.5 7.5L12 3l4.5 4.5"],
    # a magnifier with a plus / a minus
    "zoom_in": ["M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14z", "M21 21l-5-5", "M11 8v6", "M8 11h6"],
    "zoom_out": ["M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14z", "M21 21l-5-5", "M8 11h6"],
    # arrows pointing out to both ends: the whole file across the full width
    "zoom_all": ["M3 12h18", "M7 8l-4 4l4 4", "M17 8l4 4l-4 4", "M3 5v14", "M21 5v14"],
    # two arrows pushing the y-axis out to a top and a bottom line: fit to the visible data
    "zoom_fit": ["M4 4h16", "M4 20h16", "M12 12V7", "M9.5 9.5L12 7l2.5 2.5", "M12 12v5", "M9.5 14.5L12 17l2.5-2.5"],
    # a sparkle: it fits by itself
    "auto_zoom": ["M11 3l1.9 5.1L18 10l-5.1 1.9L11 17l-1.9-5.1L4 10l5.1-1.9z", "M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"],
    # a list
    "channels": ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
    # interval brackets with a cross
    "clear_selection": ["M8 4H5v16h3", "M16 4h3v16h-3", "M9.5 9.5l5 5", "M14.5 9.5l-5 5"],
}


def icon_svg(name: str, colour: str) -> str:
    """The SVG text of icon ``name`` stroked in ``colour`` (a ``#rrggbb`` string)."""
    paths = "".join(f'<path d="{d}"/>' for d in ICONS[name])
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {_STROKE.format(c=colour)}>{paths}</svg>'


def _render(name: str, colour: str, px: int, dpr: float):
    from .logo import render_svg
    return render_svg(icon_svg(name, colour), px, dpr)


def toolbar_icon(name: str, sizes=(18, 22, 28), dpr: float = 2.0):
    """A ``QIcon`` for ``name``: normal, disabled and (checked) on states in the active theme's
    colours.  Empty (``QIcon()``) when Qt SVG is unavailable, so the button keeps its text."""
    from PySide6.QtGui import QIcon
    t = theme.active_theme()
    icon = QIcon()
    for px in sizes:
        for mode, colour, state in ((QIcon.Normal, t["text"], QIcon.Off),
                                    (QIcon.Active, t["accent"], QIcon.Off),
                                    (QIcon.Disabled, t["disabled_fg"], QIcon.Off),
                                    (QIcon.Normal, t["accent"], QIcon.On),
                                    (QIcon.Active, t["accent"], QIcon.On),
                                    (QIcon.Disabled, t["disabled_fg"], QIcon.On)):
            pm = _render(name, colour, px, dpr)
            if pm is None:
                return QIcon()
            icon.addPixmap(pm, mode, state)
    return icon
