"""The start-up splash: a sheet of the logo's chart-recorder paper.

The same world as the app icon (``logo.py``): cream paper with the green millimetre grid,
the logo tile at the left, the wordmark and tagline in recorder ink, and along the bottom
one ink trace of quiet breathing with a sniff that is marked as a selection with the amber
band, the app's core action. ``make_splash(app)`` renders it with ``QSvgRenderer`` at the
screen's device-pixel ratio and returns a ``QSplashScreen`` (``None`` without Qt SVG), shown
by ``app.py`` while the main window is built.

The layout is in logical pixels (780×460); ``build_splash_svg`` is pure Python and testable
without Qt.
"""
from __future__ import annotations

import math

from .logo import AMBER, GRID, INK, PAPER_EDGE, PAPER_HI, PAPER_LO

WIDTH, HEIGHT = 780, 460

_GREEN_TEXT = "#3F6B34"   # the grid's green, dark enough to read: the tagline
_MUTED = "#7A6E58"        # faded ink on paper: sub-tagline, credit
_LINK = "#5C4A1E"         # the repository line

_TAGLINE = "LabChart recordings without LabChart"
_SUBTAGLINE = "channels · blocks · comments · video · export as text, CSV or Excel"
_REPOSITORY = "github.com/emilwalsted/adichtion"

# logo-tile placement (logical px); the tile is self-contained
LOGO_X, LOGO_Y, LOGO_S = 48, 96, 128
# the ink trace along the bottom: its baseline and the sniff that is selected
_TRACE_Y, _TRACE_X0, _TRACE_X1 = 352, 42, 738
_SNIFF_X, _BAND = 560, (528, 592)

_FONT = "Mulish, Muli, Avenir Next, Segoe UI, Helvetica Neue, Arial, sans-serif"
_fonts_resolved = False


def _resolve_svg_font() -> None:
    """Rebind ``_FONT`` to one *installed* family. Qt's SVG renderer takes ``font-family``
    as a single name, not a CSS fallback list, and would hunt for a family literally
    called "Mulish, Muli, …" (with a warning per text element). Needs a QApplication."""
    global _FONT, _fonts_resolved
    if _fonts_resolved:
        return
    try:
        from PySide6.QtGui import QFontDatabase
        installed = set(QFontDatabase.families())
        for cand in _FONT.split(","):
            cand = cand.strip()
            if cand in installed:
                _FONT = cand
                break
        else:
            fam = QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
            _FONT = fam or "sans-serif"                 # a fontless headless system
        _fonts_resolved = True
    except Exception:                                   # pragma: no cover - cosmetic only
        pass


def _grid(w: int, h: int, step: int = 14, major_every: int = 5) -> str:
    """The millimetre grid over the whole sheet, fainter than the tile's so the tile stands out."""
    out = []
    k, v = 0, step
    while v < max(w, h):
        k += 1
        major = k % major_every == 0
        op = 0.36 if major else 0.20
        sw = 1.4 if major else 1.0
        if v < w:
            out.append(f'<line x1="{v}" y1="0" x2="{v}" y2="{h}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{sw}"/>')
        if v < h:
            out.append(f'<line x1="0" y1="{v}" x2="{w}" y2="{v}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{sw}"/>')
        v += step
    return "".join(out)


def breathing_trace(x0: float, x1: float, base: float, amp: float = 22.0, period: float = 116.0,
                    sniff_x: float = _SNIFF_X, sniff_h: float = 82.0, sniff_w: float = 5.5,
                    n: int = 700) -> str:
    """Points of one ink trace: quiet tidal breathing (a smooth wave) with a single sharp
    sniff at ``sniff_x`` (a narrow Gaussian added to the wave), as a polyline string."""
    pts = []
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        y = base - amp * math.sin(2.0 * math.pi * (x - x0) / period)
        y -= sniff_h * math.exp(-0.5 * ((x - sniff_x) / sniff_w) ** 2)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def build_splash_svg(width: int = WIDTH, height: int = HEIGHT, version: str | None = None) -> str:
    """The whole splash but the logo tile (which ``make_splash`` draws on top as a pixmap):
    paper, grid, wordmark, tagline, the trace with its selected sniff and the footer."""
    if version is None:
        try:
            from adichtion import __version__ as version
        except Exception:                               # pragma: no cover
            version = ""
    w, h = width, height
    footer_y = h - 34
    credit = f"v{version} · Emil Ingerslev Walsted" if version else "Emil Ingerslev Walsted"
    bx0, bx1 = _BAND
    by0, by1 = _TRACE_Y - 92, _TRACE_Y + 44
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">
  <defs>
    <linearGradient id="paper" x1="0" y1="0" x2="0.4" y2="1">
      <stop offset="0" stop-color="{PAPER_HI}"/><stop offset="1" stop-color="{PAPER_LO}"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{w}" height="{h}" rx="16" fill="url(#paper)"/>
  {_grid(w, h)}
  <!-- the selected sniff: the amber band first, the ink on top of it -->
  <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}" fill="{AMBER}" fill-opacity="0.32"/>
  <line x1="{bx0}" y1="{by0}" x2="{bx0}" y2="{by1}" stroke="{AMBER}" stroke-width="2" stroke-opacity="0.9"/>
  <line x1="{bx1}" y1="{by0}" x2="{bx1}" y2="{by1}" stroke="{AMBER}" stroke-width="2" stroke-opacity="0.9"/>
  <polyline points="{breathing_trace(_TRACE_X0, _TRACE_X1, _TRACE_Y)}" fill="none" stroke="{INK}"
            stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>

  <text x="204" y="152" font-family="{_FONT}" font-size="60" font-weight="800"
        letter-spacing="-1.0" fill="{INK}">adichtion</text>
  <rect x="206" y="168" width="56" height="5" rx="2.5" fill="{AMBER}"/>
  <text x="206" y="202" font-family="{_FONT}" font-size="19" font-weight="600"
        letter-spacing="0.2" fill="{_GREEN_TEXT}">{_TAGLINE}</text>
  <text x="206" y="228" font-family="{_FONT}" font-size="13.5" letter-spacing="0.3"
        fill="{_MUTED}">{_SUBTAGLINE}</text>

  <line x1="42" y1="{footer_y - 16}" x2="{w - 42}" y2="{footer_y - 16}"
        stroke="{INK}" stroke-opacity="0.18" stroke-width="1"/>
  <text x="42" y="{footer_y + 4}" font-family="{_FONT}" font-size="13" fill="{_MUTED}">{credit}</text>
  <text x="{w - 42}" y="{footer_y + 4}" font-family="{_FONT}" font-size="13"
        fill="{_LINK}" text-anchor="end">{_REPOSITORY}</text>
  <rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="15" fill="none" stroke="{PAPER_EDGE}" stroke-width="2"/>
</svg>'''


def make_splash(app=None, width: int = WIDTH, height: int = HEIGHT):
    """Render the splash and return a ``QSplashScreen``, or ``None`` without Qt SVG."""
    try:
        from PySide6.QtCore import QByteArray, QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QSplashScreen
        from .logo import logo_pixmap
    except Exception:                                   # pragma: no cover
        return None

    class _ClickSafeSplash(QSplashScreen):
        """``QSplashScreen`` hides itself on any click; there is nothing behind it until the
        window is built, so a click here is a no-op instead of a route to an empty screen."""
        def mousePressEvent(self, event):               # noqa: N802 - Qt override
            pass

    _resolve_svg_font()

    dpr = 2.0
    try:
        screen = app.primaryScreen() if app is not None else None
        if screen is not None and screen.devicePixelRatio() > 0:
            dpr = max(1.0, min(4.0, float(screen.devicePixelRatio())))
    except Exception:                                   # pragma: no cover
        pass

    pm = QPixmap(int(width * dpr), int(height * dpr))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(dpr, dpr)
    QSvgRenderer(QByteArray(build_splash_svg(width, height).encode("utf-8"))).render(
        painter, QRectF(0, 0, width, height))
    logo = logo_pixmap(LOGO_S, dpr)
    if logo is not None:
        painter.drawPixmap(QRectF(LOGO_X, LOGO_Y, LOGO_S, LOGO_S), logo, QRectF(logo.rect()))
    painter.end()
    pm.setDevicePixelRatio(dpr)

    splash = _ClickSafeSplash(pm)
    splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    splash.setWindowModality(Qt.ApplicationModal)       # nothing covers it while the window is built
    # The progress line sits under the footer; a fixed pixel size matching the footer's own
    # text, since the splash is shown before the theme sets the app font.
    msg_font = QFont(_FONT)
    msg_font.setPixelSize(13)
    splash.setFont(msg_font)
    splash.showMessage("Starting…", Qt.AlignHCenter | Qt.AlignBottom, QColor(_MUTED))
    return splash
