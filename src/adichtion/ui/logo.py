"""The adichtion logo and app icon, generated as SVG and rendered with Qt.

Concept "a-trace" (chosen 28-09-2026, ``docs/beslutninger.md``): a sheet of chart-recorder
paper (cream, green millimetre grid) on which one ink trace writes a lowercase "a" and runs
on into a sniff-like spike, with the spike marked as a selection (amber band), the app's
core action. The paper points back to the strip-chart recorder that "LabChart" is named
after; the single ink trace and the selection are what adichtion does with such a recording.

Two drawings share the tile:

- ``build_logo_svg(size)``: the full artwork (grid, band, trace with the "a" and the spike)
  for 128 px and up: splash, About dialog, app store, dmg, README.
- ``build_icon_svg(size)``: the simplified mark for small sizes (64 px and below): the "a"
  alone, in ink on the paper, without grid or band. ``svg_for_size(px)`` picks one.

This module is the single source of the drawing: palette and geometry live here and nowhere
else. ``design/logo/adichtion_logo.py`` imports it to write the checked-in SVGs and PNG
renders (a test keeps those in sync), and ``scripts/render_icons.py`` renders the installer
icons from it. The SVG part is pure Python (no Qt at import time); ``make_pixmap`` and
``app_icon`` hand the string to ``QSvgRenderer``. No filters, masks or clipPaths are used,
so Qt's SVG Tiny renderer draws it exactly like a browser or cairosvg. Geometry is in a
1024×1024 viewBox with the tile inset like RespMech's (87.5 % of the canvas, 25 % corner
radius), so the two apps sit level in a dock.
"""
from __future__ import annotations

import math

# ---- canvas and tile ----------------------------------------------------------------
CANVAS = 1024
TILE0, TILE1 = 64, 960            # tile extent on the canvas (RespMech's proportions)
TILE_W = TILE1 - TILE0
RADIUS = 224                      # 25 % of the tile width

# ---- palette ---------------------------------------------------------------------------
PAPER_HI, PAPER_LO = "#F7F0DC", "#EBDFBF"   # chart-recorder paper, top-left to bottom-right
PAPER_EDGE = "#CDBB90"                      # thin edge that keeps the light tile defined
GRID = "#7FA26F"                            # the green millimetre grid
INK = "#26242E"                             # blue-black recorder ink
AMBER = "#E39A2B"                           # the selection band

# ---- geometry of the mark ---------------------------------------------------------------
# The large "a": bowl centre, radius; the stem sits on the bowl's right at x = cx + r.
LOGO_A = dict(cx=420, cy=530, r=118)
LOGO_SPIKE_H = 210                # height of the spike above the baseline
LOGO_LEAD_IN, LOGO_LEAD_OUT = 118, 906
LOGO_STROKE = 46
LOGO_BAND = (660, 776)            # x extent of the selection band (centred on the spike)
# The small "a": bigger and centred, no tail.
ICON_A = dict(cx=486, cy=512, r=150)
ICON_STROKE = 104

#: The icon drawing is used up to this size (px), the logo drawing above it.
ICON_MAX_PX = 64

LOGO_SIZES = (1024, 512, 256, 128)
ICON_SIZES = (64, 48, 32, 16)


def _rounded_span(v: float):
    """Inside extent of the rounded tile along one axis at coordinate ``v`` on the other,
    so grid lines can run edge to edge without a clipPath."""
    if v < TILE0 + RADIUS:
        d = (TILE0 + RADIUS) - v
    elif v > TILE1 - RADIUS:
        d = v - (TILE1 - RADIUS)
    else:
        return TILE0, TILE1
    if d >= RADIUS:
        return None
    h = math.sqrt(RADIUS * RADIUS - d * d)
    return (TILE0 + RADIUS) - h, (TILE1 - RADIUS) + h


def _grid(step=56, minor_opacity=0.30, major_every=5, major_opacity=0.55, inset=8, width=3):
    out = []
    k, v = 0, TILE0 + step
    while v < TILE1:
        k += 1
        major = k % major_every == 0
        op = major_opacity if major else minor_opacity
        w = width + 1 if major else width
        span = _rounded_span(v)
        if span:
            a, b = span[0] + inset, span[1] - inset
            out.append(f'<line x1="{v}" y1="{a:.0f}" x2="{v}" y2="{b:.0f}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{w}"/>')
            out.append(f'<line x1="{a:.0f}" y1="{v}" x2="{b:.0f}" y2="{v}" stroke="{GRID}" '
                       f'stroke-opacity="{op}" stroke-width="{w}"/>')
        v += step
    return "".join(out)


def _band(x0, x1, opacity=0.32):
    y0, y1 = TILE0 + 40, TILE1 - 40
    return (f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="{AMBER}" '
            f'fill-opacity="{opacity}"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="{AMBER}" stroke-width="5" stroke-opacity="0.9"/>'
            f'<line x1="{x1}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="{AMBER}" stroke-width="5" stroke-opacity="0.9"/>')


def a_trace_path(cx, cy, r, spike_h, lead_in, lead_out):
    """One continuous stroke: baseline in from the left, the bowl of the "a" (a full circle
    entered at its bottom), the stem up and down again, then the spike and the baseline out."""
    top, base, sx = cy - r, cy + r, cx + r
    return (f"M{lead_in},{base} H{cx} A{r},{r} 0 1 1 {sx},{cy} V{top} V{base} "
            f"H{sx + 148} L{sx + 180},{base - spike_h} L{sx + 212},{base} H{lead_out}")


def a_path(cx, cy, r):
    """The bare "a": a full bowl and the stem on its right (two sub-paths, one stroke)."""
    top, base, sx = cy - r, cy + r, cx + r
    return f"M{sx},{cy} A{r},{r} 0 1 1 {sx},{cy - 0.01} M{sx},{top} V{base}"


def _svg(body: str, size: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}" '
            f'width="{size}" height="{size}">'
            f'<defs><linearGradient id="paper" x1="0" y1="0" x2="0.4" y2="1">'
            f'<stop offset="0" stop-color="{PAPER_HI}"/><stop offset="1" stop-color="{PAPER_LO}"/>'
            f'</linearGradient></defs>'
            f'<rect x="{TILE0}" y="{TILE0}" width="{TILE_W}" height="{TILE_W}" rx="{RADIUS}" fill="url(#paper)"/>'
            f'{body}'
            f'<rect x="{TILE0 + 3}" y="{TILE0 + 3}" width="{TILE_W - 6}" height="{TILE_W - 6}" rx="{RADIUS - 3}" '
            f'fill="none" stroke="{PAPER_EDGE}" stroke-width="6"/>'
            f'</svg>')


def build_logo_svg(size: int = 1024) -> str:
    """The full logo: chart paper, selection band, the ink trace writing the "a" and the spike."""
    body = _grid()
    body += _band(*LOGO_BAND)
    d = a_trace_path(spike_h=LOGO_SPIKE_H, lead_in=LOGO_LEAD_IN, lead_out=LOGO_LEAD_OUT, **LOGO_A)
    body += (f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="{LOGO_STROKE}" '
             f'stroke-linecap="round" stroke-linejoin="round"/>')
    return _svg(body, size)


def build_icon_svg(size: int = 64) -> str:
    """The small mark: the "a" alone in ink on the paper (for 64 px and below)."""
    body = (f'<path d="{a_path(**ICON_A)}" fill="none" stroke="{INK}" stroke-width="{ICON_STROKE}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')
    return _svg(body, size)


def svg_for_size(px: int) -> str:
    """The drawing to use for a given pixel size: the icon up to 64 px, the logo above."""
    return build_icon_svg(px) if px <= ICON_MAX_PX else build_logo_svg(px)


# ---- Qt rendering ----------------------------------------------------------------------

def render_svg(svg: str, px: int, dpr: float = 1.0):
    """Render an SVG string to a transparent ``QPixmap`` of ``px`` logical pixels
    (``px * dpr`` device pixels, with the ratio set on the pixmap); ``None`` without Qt SVG."""
    try:
        from PySide6.QtCore import QByteArray, QRectF, Qt
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
    except Exception:                                   # pragma: no cover - no Qt SVG
        return None
    dpr = max(1.0, float(dpr))
    dev = max(1, int(round(px * dpr)))
    pm = QPixmap(dev, dev)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, dev, dev))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_pixmap(size: int = 128, dpr: float = 1.0):
    """The drawing for ``size`` logical pixels (the icon up to 64 px, the logo above) as a
    ``QPixmap``; the choice is made on the device size, so a 64 px logical tile on a 2×
    screen gets the logo. ``None`` when Qt SVG is unavailable."""
    return render_svg(svg_for_size(int(round(size * max(1.0, dpr)))), size, dpr)


def logo_pixmap(size: int = 256, dpr: float = 1.0):
    """The full logo drawing at any size (the splash and the About dialog)."""
    return render_svg(build_logo_svg(size), size, dpr)


def app_icon():
    """A multi-resolution ``QIcon`` (the icon drawing up to 64 px, the logo from 128 px);
    ``None`` when Qt is unavailable."""
    try:
        from PySide6.QtGui import QIcon
        icon = QIcon()
        for s in (16, 24, 32, 48, 64, 128, 256, 512):
            pm = make_pixmap(s)
            if pm is not None:
                icon.addPixmap(pm)
        return icon if not icon.isNull() else None
    except Exception:                                   # pragma: no cover - cosmetic
        return None


#: The sizes of the macOS Dock icon when the app runs from source: the full logo only.
DOCK_ICON_SIZES = (128, 256, 512)


def dock_icon():
    """The application icon for macOS when NOT running as the packaged app (from source):
    the full logo only, 128 px and up, smallest first; ``None`` when Qt is unavailable.

    ``app_icon()`` is wrong for the Dock. Qt 6.9.1 to 6.10.0 (what the packaged app gets,
    PySide6 6.9.3, because min_os_version 12.0 excludes the macosx_13_0 wheels of 6.10+)
    turn the QIcon into an NSImage whose point size is its FIRST representation, the 16 px
    bare "a", which the Dock then blows up. Qt 6.10.2 and later pick one pixmap for the
    dock tile, which is also the bare "a" whenever tile size x pixel ratio <= 64 px. With
    only the logo, every Qt version hands the Dock the logo. (The packaged app sets no application icon at all:
    its adichtion.icns is the Dock icon, see ``app.application_icon_choice``.)"""
    try:
        from PySide6.QtGui import QIcon
        icon = QIcon()
        for s in DOCK_ICON_SIZES:
            pm = logo_pixmap(s)
            if pm is not None:
                icon.addPixmap(pm)
        return icon if not icon.isNull() else None
    except Exception:                                   # pragma: no cover - cosmetic
        return None
